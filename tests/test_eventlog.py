import hashlib
import json

import pytest

from conftest import load_logwriter
from hic_et_nunc.eventlog import (
    Commitment,
    EventLog,
    MerkleTree,
    commit,
    inclusion_proof,
    merkle_root,
    read_entries,
    read_log,
    verify_chain,
    verify_commitment,
    verify_inclusion,
)


def test_chain_roundtrip_and_tamper(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    for i in range(10):
        log.append("stroke", {"i": i}, t=1000.0 + i)
    entries = list(read_entries(tmp_path / "e.jsonl"))
    assert verify_chain(entries).ok

    entries[4]["data"]["i"] = 99
    r = verify_chain(entries)
    assert not r.ok and r.first_bad_seq == 4


def test_reopen_continues_chain(tmp_path):
    p = tmp_path / "e.jsonl"
    EventLog(p).append("a", {})
    EventLog(p).append("b", {})
    entries = list(read_entries(p))
    assert [e["seq"] for e in entries] == [0, 1]
    assert verify_chain(entries).ok


def test_inclusion_proofs_all_sizes():
    for n in range(1, 18):
        hashes = [
            hashlib.sha256(str(i).encode()).hexdigest() for i in range(n)
        ]
        root = merkle_root(hashes)
        for i in range(n):
            proof = inclusion_proof(hashes, i)
            assert verify_inclusion(hashes[i], i, n, proof, root), (n, i)
            assert not verify_inclusion(
                hashes[(i + 1) % n] if n > 1 else "0" * 64, i, n, proof, root
            )


def test_commitment_detects_rewrite(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    for i in range(20):
        log.append("stroke", {"i": i}, t=float(i))
    entries = list(read_entries(tmp_path / "e.jsonl"))
    c = commit(entries[:12], t=0)
    assert verify_commitment(entries, c)  # log may grow after a commitment

    forged = EventLog(
        tmp_path / "forged.jsonl"
    )  # same length, different history
    for i in range(20):
        forged.append("stroke", {"i": i * 2}, t=float(i))
    assert not verify_commitment(
        list(read_entries(tmp_path / "forged.jsonl")), c
    )


def test_plugin_writer_is_compatible(tmp_path):
    lw = load_logwriter()
    log = lw.BufferedEventLog(tmp_path / "session")
    for i in range(50):
        log.append("stroke", {"points": [[i, 1.5, 2.5, 0.5, 0, 0]]})
        if i % 20 == 0:
            log.checkpoint()
    log.checkpoint()
    entries = list(read_entries(tmp_path / "session/events.jsonl"))
    assert verify_chain(entries).ok and len(entries) == 50
    commits = [
        json.loads(line)
        for line in (tmp_path / "session/commitments.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [c["size"] for c in commits] == [1, 21, 41, 50]
    hashes = [e["hash"] for e in entries]
    for c in commits:
        assert verify_commitment(entries, Commitment(**c))
        # any single event proves against the committed root without the
        # rest of the log
        for i in (0, c["size"] // 2, c["size"] - 1):
            proof = inclusion_proof(hashes[: c["size"]], i)
            assert verify_inclusion(hashes[i], i, c["size"], proof, c["root"])


def test_incremental_tree_matches_reference():
    lw = load_logwriter()
    hashes = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(130)]
    ours, plugin = MerkleTree(), lw.MerkleTree()
    for n in range(len(hashes) + 1):
        ref = merkle_root(hashes[:n])
        assert ours.root() == plugin.root() == ref, n
        if n < len(hashes):
            ours.append(hashes[n])
            plugin.append(hashes[n])


def test_read_log_leaves_out_only_a_cut_off_last_line(tmp_path):
    """A crash cuts the last line; any other bad line still raises."""
    p = tmp_path / "e.jsonl"
    log = EventLog(p)
    for i in range(5):
        log.append("stroke", {"i": i, "name": "Ebene ü"}, t=1000.0 + i)
    whole = p.read_bytes()
    lines = whole.split(b"\n")

    entries, cut = read_log(p)
    assert len(entries) == 5 and not cut

    p.write_bytes(whole[:-1])  # complete last entry, newline not written
    entries, cut = read_log(p)
    assert len(entries) == 5 and not cut

    for at in (1, len(lines[4]) // 2, lines[4].index("ü".encode()) + 1):
        p.write_bytes(b"\n".join(lines[:4]) + b"\n" + lines[4][:at])
        entries, cut = read_log(p)
        assert len(entries) == 4 and cut and verify_chain(entries).ok

    p.write_bytes(b"\n".join(lines[:2] + [lines[2][:20]] + lines[3:]))
    with pytest.raises(json.JSONDecodeError):
        read_log(p)
    p.write_bytes(whole[:-10] + b"\n")  # cut, then a newline: not a crash
    with pytest.raises(json.JSONDecodeError):
        read_log(p)
