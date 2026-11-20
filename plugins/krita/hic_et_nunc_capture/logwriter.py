"""Vendored, dependency-free writer for the hic et nunc log format.

Must produce exactly what `hic_et_nunc.eventlog` verifies (same
canonical JSON, same chain, same RFC 6962 Merkle roots). Kept separate
from the Qt code so it can be tested without Krita. Entries are hashed
at append time and written in batches to keep disk I/O off the input
path.
"""

import hashlib
import json
import time
from pathlib import Path

GENESIS = "0" * 64


def _canonical(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


class MerkleTree:
    """Incremental RFC 6962 tree; copy of `eventlog.MerkleTree`.

    Keeps the roots of the complete subtrees, O(log n) per event.
    """

    def __init__(self):
        """Start an empty tree."""
        self.size = 0
        self._peaks = []

    def append(self, h):
        """Add a leaf given as the entry hash in hex."""
        node, n = (
            hashlib.sha256(b"\x00" + bytes.fromhex(h)).digest(),
            self.size,
        )
        while n & 1:
            node = hashlib.sha256(b"\x01" + self._peaks.pop() + node).digest()
            n >>= 1
        self._peaks.append(node)
        self.size += 1

    def root(self):
        """Return the hex Merkle root of all leaves so far."""
        if not self._peaks:
            return hashlib.sha256(b"").hexdigest()
        r = self._peaks[-1]
        for p in reversed(self._peaks[:-1]):
            r = hashlib.sha256(b"\x01" + p + r).digest()
        return r.hex()


class BufferedEventLog:
    """Hash-chained event log that writes entries in batches."""

    def __init__(self, directory):
        """Open a new log in `directory`, creating it if needed."""
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"
        self.commit_path = self.dir / "commitments.jsonl"
        self.seq = 0
        self.head = GENESIS
        self.tree = MerkleTree()
        self._pending = []

    def append(self, type_, data, t=None):
        """Hash and buffer one event; return the entry.

        `t` defaults to the current time. Nothing is written until
        `flush`.
        """
        entry = {
            "seq": self.seq,
            "t": time.time() if t is None else t,
            "type": type_,
            "data": data,
            "prev": self.head,
        }
        entry["hash"] = hashlib.sha256(_canonical(entry)).hexdigest()
        self._pending.append(entry)
        self.tree.append(entry["hash"])
        self.seq += 1
        self.head = entry["hash"]
        return entry

    def flush(self):
        """Write buffered entries to disk; return how many."""
        if not self._pending:
            return 0
        with self.path.open("a", encoding="utf-8") as f:
            for e in self._pending:
                f.write(
                    json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n"
                )
        n = len(self._pending)
        self._pending = []
        return n

    def checkpoint(self):
        """Flush, then record the Merkle root of every entry so far.

        The record has the same shape as
        `hic_et_nunc.eventlog.Commitment`. Any single entry can later be
        proven against it without revealing the rest. Later this goes to
        a timestamping server instead of a local file.
        """
        self.flush()
        rec = {
            "size": self.tree.size,
            "root": self.tree.root(),
            "t": time.time(),
        }
        with self.commit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return rec
