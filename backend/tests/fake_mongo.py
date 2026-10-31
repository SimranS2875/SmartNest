"""In-memory stand-ins for PyMongo objects, for unit tests.

``FakeCollection`` implements just the collection calls
``BaseRepository`` uses, with equality-only filters and ``$set`` /
``$setOnInsert`` updates.  It lets the repository's own logic (id
generation, timestamps, validation, error translation) be tested
without a server.  It is deliberately NOT a Mongo emulator — real query
semantics are covered by the optional Atlas integration tests.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Iterable

from pymongo.errors import BulkWriteError, DuplicateKeyError


def _matches(doc: dict, flt: dict) -> bool:
    return all(doc.get(key) == value for key, value in flt.items())


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, spec: Iterable[tuple[str, int]]) -> "FakeCursor":
        for key, direction in reversed(list(spec)):  # stable multi-key sort
            self._docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def skip(self, n: int) -> "FakeCursor":
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int) -> "FakeCursor":
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.docs: dict[Any, dict] = {}
        self.calls: list[str] = []

    # ── writes ───────────────────────────────────────────────────────────
    def insert_one(self, doc: dict) -> SimpleNamespace:
        self.calls.append("insert_one")
        if doc["_id"] in self.docs:
            raise DuplicateKeyError("E11000 duplicate key error")
        self.docs[doc["_id"]] = copy.deepcopy(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def insert_many(self, docs: list[dict], ordered: bool = True) -> SimpleNamespace:
        self.calls.append("insert_many")
        ids = []
        for doc in docs:
            if doc["_id"] in self.docs:
                raise BulkWriteError({"writeErrors": [{"code": 11000, "errmsg": "duplicate key"}]})
            self.docs[doc["_id"]] = copy.deepcopy(doc)
            ids.append(doc["_id"])
        return SimpleNamespace(inserted_ids=ids)

    def _apply(self, doc: dict, update: dict) -> None:
        doc.update(copy.deepcopy(update.get("$set", {})))

    def update_one(self, flt: dict, update: dict, upsert: bool = False) -> SimpleNamespace:
        self.calls.append("update_one")
        for doc in self.docs.values():
            if _matches(doc, flt):
                self._apply(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            new = {**flt, **copy.deepcopy(update.get("$set", {})),
                   **copy.deepcopy(update.get("$setOnInsert", {}))}
            self.docs[new["_id"]] = new
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=new["_id"])
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    def update_many(self, flt: dict, update: dict) -> SimpleNamespace:
        self.calls.append("update_many")
        matched = [d for d in self.docs.values() if _matches(d, flt)]
        for doc in matched:
            self._apply(doc, update)
        return SimpleNamespace(matched_count=len(matched), modified_count=len(matched))

    def delete_one(self, flt: dict) -> SimpleNamespace:
        self.calls.append("delete_one")
        for key, doc in list(self.docs.items()):
            if _matches(doc, flt):
                del self.docs[key]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, flt: dict) -> SimpleNamespace:
        self.calls.append("delete_many")
        gone = [k for k, d in self.docs.items() if _matches(d, flt)]
        for key in gone:
            del self.docs[key]
        return SimpleNamespace(deleted_count=len(gone))

    # ── reads ────────────────────────────────────────────────────────────
    def find_one(self, flt: dict | None = None) -> dict | None:
        self.calls.append("find_one")
        for doc in self.docs.values():
            if _matches(doc, flt or {}):
                return copy.deepcopy(doc)
        return None

    def find(self, flt: dict | None = None, projection: dict | None = None) -> FakeCursor:
        self.calls.append("find")
        return FakeCursor([copy.deepcopy(d) for d in self.docs.values() if _matches(d, flt or {})])

    def count_documents(self, flt: dict, limit: int = 0) -> int:
        self.calls.append("count_documents")
        n = sum(1 for d in self.docs.values() if _matches(d, flt))
        return min(n, limit) if limit else n


class BrokenCollection:
    """A collection whose every operation raises ``error``."""

    name = "broken"

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __getattr__(self, item: str):
        def fail(*args, **kwargs):
            raise self._error
        return fail
