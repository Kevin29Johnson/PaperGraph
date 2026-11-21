"""Owns the single read-only Relate connection the whole app shares.

Verified against relate-db 1.0.1 (relatedb.ai/docs) and tested directly:
- A connection is tied to the thread that opened it (relate.threadsafety == 1).
  A statement from another thread raises relate.ProgrammingError (WrongThread).
- Only the loader (ingest/load.py) ever writes to data/papergraph.rl. This app
  opens the store with read_only=True and never writes.

So this module runs one dedicated worker thread that opens the read-only
connection and owns it for the process's lifetime. Every query is submitted
to that thread through a queue and the caller blocks on a Future for the
result, instead of touching the connection from whatever thread is calling in.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import relate

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE_PATH = PROJECT_ROOT / "data" / "papergraph.rl"
DEFAULT_ROW_LIMIT = 25


class _Job:
    __slots__ = ("statement", "params", "future")

    def __init__(self, statement: str, params: dict[str, Any], future: "Future[list[dict[str, Any]]]"):
        self.statement = statement
        self.params = params
        self.future = future


class RelateWorker:
    """Runs all Cypher queries on one thread that owns a read-only connection."""

    def __init__(self, store_path: Path | str = DEFAULT_STORE_PATH):
        self._store_path = str(store_path)
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._started = threading.Event()
        self._start_error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started.wait()
        if self._start_error is not None:
            raise self._start_error

    def _run(self) -> None:
        try:
            conn = relate.connect(self._store_path, read_only=True)
        except Exception as exc:
            self._start_error = exc
            self._started.set()
            return

        self._started.set()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                try:
                    rows = conn.execute(job.statement, job.params).fetchall()
                    job.future.set_result([dict(row) for row in rows])
                except Exception as exc:
                    job.future.set_exception(exc)
        finally:
            conn.close()

    def query(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a parameterized Cypher statement and return rows as dicts."""
        future: Future[list[dict[str, Any]]] = Future()
        self._jobs.put(_Job(statement, params or {}, future))
        return future.result()

    def close(self) -> None:
        self._jobs.put(None)
        self._thread.join(timeout=5)
