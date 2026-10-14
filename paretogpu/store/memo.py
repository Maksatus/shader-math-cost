"""(kind, key) -> JSON value. kind carries the rule's version ("loops/1"): bump it when the rule's logic changes; key
must hold everything the value is made of (shader text hash, core, malioc version)."""
import atexit
import json
import os
import sqlite3
import threading
import zlib

from paretogpu.store import workspace

PATH = os.path.join(workspace.OUT, "_cache", "memo.sqlite")


class Memo:
    def __init__(self, path=PATH):
        self.path = path
        self.tls = threading.local()
        self.conns = []
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path, timeout=60)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS memo (kind TEXT, key TEXT, value BLOB NOT NULL, "
                       "PRIMARY KEY (kind, key))")
            db.commit()
        finally:
            db.close()

    def _db(self):
        db = getattr(self.tls, "db", None)
        if db is None:
            db = self.tls.db = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
            db.execute("PRAGMA synchronous=NORMAL")
            with self.lock:
                self.conns.append(db)
        return db

    def get(self, kind, key, default=None):
        row = self._db().execute("SELECT value FROM memo WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else default

    def put_many(self, kind, items):
        if not items:
            return
        db = self._db()
        db.executemany("INSERT OR REPLACE INTO memo (kind, key, value) VALUES (?, ?, ?)",
                       [(kind, k, zlib.compress(json.dumps(v).encode("utf-8"), 6)) for k, v in items])
        db.commit()

    def close(self):
        with self.lock:
            for db in self.conns:
                db.close()
            self.conns = []


_default = None
_guard = threading.Lock()


def default():
    global _default
    with _guard:
        if _default is None:
            _default = Memo()
            atexit.register(_default.close)
        return _default
