"""Append-only, hash-chained event log with Merkle commitments.

Format (one JSON object per line, UTF-8):

    {"seq": 0, "t": 1793455119.123, "type": "tablet", "data": {...},
     "prev": "<hex sha256 of previous entry, or 64 zeros>",
     "hash": "<hex>"}

``hash`` = sha256 over the canonical JSON of the entry without the
``hash`` field. The Krita plugin vendors a writer for exactly this
format (``plugins/krita/hic_et_nunc_capture/logwriter.py``); keep the
two in sync.

Merkle trees follow RFC 6962 (Certificate Transparency): leaves are the
entry hashes, ``leaf = H(0x00 || h)``, ``node = H(0x01 || left ||
right)``, split at the largest power of two below n. This lets an artist
publish a single root during work and later reveal any individual event
with an inclusion proof, without revealing the rest of the log.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    """Return the canonical JSON encoding that entry hashes cover."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def entry_hash(entry: dict) -> str:
    """Return the hex sha256 of an entry without its ``hash`` field."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


class EventLog:
    """Writer: appends entries to a JSONL file, keeping the chain."""

    def __init__(self, path: str | Path):
        """Open the log at `path`, continuing the chain if it exists."""
        self.path = Path(path)
        self.seq = 0
        self.head = GENESIS
        if self.path.exists():
            for e in read_entries(self.path):
                self.seq, self.head = e["seq"] + 1, e["hash"]

    def append(self, type_: str, data: dict, t: float | None = None) -> dict:
        """Write one event and return the entry.

        `t` defaults to the current time.
        """
        entry = {
            "seq": self.seq,
            "t": time.time() if t is None else t,
            "type": type_,
            "data": data,
            "prev": self.head,
        }
        entry["hash"] = entry_hash(entry)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
            )
        self.seq += 1
        self.head = entry["hash"]
        return entry


def read_entries(path: str | Path) -> Iterator[dict]:
    """Yield the entries of a JSONL log, skipping blank lines."""
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_log(path: str | Path) -> tuple[list[dict], bool]:
    """Read a JSONL log whose last line may have been cut off mid-write.

    A writer stopped mid-write (Krita crashing, power lost) leaves a
    last line with no newline that isn't valid JSON. That line is left
    out; every other line is read as by `read_entries`.

    Returns:
        The entries, and whether a cut-off last line was left out.

    Raises:
        json.JSONDecodeError, UnicodeDecodeError: a line other than an
            unterminated last line can't be read.
    """
    data = Path(path).read_bytes()
    lines = data.split(b"\n")
    cut_off = False
    if not data.endswith(b"\n") and lines[-1].strip():
        try:
            json.loads(lines[-1].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            lines.pop()
            cut_off = True
    entries = [json.loads(x.decode("utf-8")) for x in lines if x.strip()]
    return entries, cut_off


@dataclass
class ChainReport:
    """Result of `verify_chain`.

    Attributes:
        ok: whether the whole chain is intact.
        entries: entries checked before the first error, or all of them.
        error: what broke, if anything.
        first_bad_seq: position of the first bad entry.
    """

    ok: bool
    entries: int
    error: str | None = None
    first_bad_seq: int | None = None


def verify_chain(entries: Iterable[dict]) -> ChainReport:
    """Check seq numbers, links and hashes; stop at the first error."""
    prev, n = GENESIS, 0
    for i, e in enumerate(entries):
        if e.get("seq") != i:
            return ChainReport(False, i, f"sequence gap at position {i}", i)
        if e.get("prev") != prev:
            return ChainReport(False, i, f"broken link at seq {i}", i)
        if entry_hash(e) != e.get("hash"):
            return ChainReport(False, i, f"hash mismatch at seq {i}", i)
        prev, n = e["hash"], i + 1
    return ChainReport(True, n)


# ---------------------------------------------------- Merkle (RFC 6962)


def _leaf(h: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + h).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def _mth(leaves: list[bytes]) -> bytes:
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return _leaf(leaves[0])
    k = _split(n)
    return _node(_mth(leaves[:k]), _mth(leaves[k:]))


def merkle_root(hashes: list[str]) -> str:
    """Return the hex RFC 6962 root over hex entry hashes."""
    return _mth([bytes.fromhex(h) for h in hashes]).hex()


class MerkleTree:
    """Incremental RFC 6962 tree.

    Keeps only the roots of the complete subtrees (one per set bit of
    `size`, largest first), so `append` and `root` are O(log n) and
    `root()` equals `merkle_root` over all leaves. The Krita plugin
    vendors the same class.
    """

    def __init__(self) -> None:
        """Start an empty tree."""
        self.size = 0
        self._peaks: list[bytes] = []

    def append(self, h: str) -> None:
        """Add a leaf given as the entry hash in hex."""
        node, n = _leaf(bytes.fromhex(h)), self.size
        while n & 1:  # merge equal-sized subtrees
            node = _node(self._peaks.pop(), node)
            n >>= 1
        self._peaks.append(node)
        self.size += 1

    def root(self) -> str:
        """Return the hex root of all leaves so far."""
        if not self._peaks:
            return hashlib.sha256(b"").hexdigest()
        r = self._peaks[-1]
        for p in reversed(self._peaks[:-1]):
            r = _node(p, r)
        return r.hex()


def inclusion_proof(hashes: list[str], index: int) -> list[str]:
    """Return the RFC 6962 audit path for leaf `index`, as hex.

    Raises:
        IndexError: `index` is outside the log.
    """
    leaves = [bytes.fromhex(h) for h in hashes]

    def path(m: int, d: list[bytes]) -> list[bytes]:
        if len(d) <= 1:
            return []
        k = _split(len(d))
        if m < k:
            return path(m, d[:k]) + [_mth(d[k:])]
        return path(m - k, d[k:]) + [_mth(d[:k])]

    if not 0 <= index < len(leaves):
        raise IndexError(index)
    return [p.hex() for p in path(index, leaves)]


def verify_inclusion(
    leaf_hash: str, index: int, size: int, proof: list[str], root: str
) -> bool:
    """RFC 9162 section 2.1.3.2 inclusion verification."""
    if not 0 <= index < size:
        return False
    fn, sn = index, size - 1
    r = _leaf(bytes.fromhex(leaf_hash))
    for p in proof:
        pb = bytes.fromhex(p)
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = _node(pb, r)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = _node(r, pb)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r.hex() == root


@dataclass(frozen=True)
class Commitment:
    """A published checkpoint: the root of the first `size` entries.

    During capture these go to a timestamping server every few minutes,
    so a history can't be fabricated after the fact. For now they're
    written locally (`commitments.jsonl`, one `to_json()` per line).
    """

    size: int
    root: str
    t: float

    def to_json(self) -> dict:
        """Return the commitment as a JSON-ready dict."""
        return {"size": self.size, "root": self.root, "t": self.t}


def commit(entries: list[dict], t: float | None = None) -> Commitment:
    """Commit to all `entries`; `t` defaults to the current time."""
    return Commitment(
        len(entries),
        merkle_root([e["hash"] for e in entries]),
        time.time() if t is None else t,
    )


def verify_commitment(entries: list[dict], c: Commitment) -> bool:
    """Check that the log's first `c.size` entries reproduce the root.

    A log that was rewritten after the commitment fails this check.
    """
    if len(entries) < c.size:
        return False
    return merkle_root([e["hash"] for e in entries[: c.size]]) == c.root
