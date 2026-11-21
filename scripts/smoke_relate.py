"""Smoke test for Relate: single-file store, write/read, and a read-only
connection opened from a second thread.

Verified against relate-db 1.0.1 docs (relatedb.ai/docs):
- relate.connect(path, read_only=...) for a single-file ".rl" store.
- A connection belongs to the thread that opened it (relate.threadsafety == 1).
- A file-backed store allows only one writer; the write connection must be
  closed before a read-only connection can open the same file.
"""

import sys
import tempfile
import threading
from pathlib import Path

import relate


def write_sample_data(store_path: Path) -> None:
    conn = relate.connect(str(store_path))
    try:
        conn.execute(
            "CREATE (:Paper {id: $id, title: $title})",
            {"id": "W1", "title": "Paper One"},
        )
        conn.execute(
            "CREATE (:Paper {id: $id, title: $title})",
            {"id": "W2", "title": "Paper Two"},
        )
        conn.execute(
            """
            MATCH (a:Paper {id: $from_id}), (b:Paper {id: $to_id})
            CREATE (a)-[:CITES]->(b)
            """,
            {"from_id": "W1", "to_id": "W2"},
        )

        rows = conn.execute(
            "MATCH (p:Paper) RETURN p.id AS id, p.title AS title ORDER BY p.id"
        ).fetchall()
        assert [r["id"] for r in rows] == ["W1", "W2"], f"unexpected papers: {rows}"

        edge_rows = conn.execute(
            "MATCH (a:Paper)-[:CITES]->(b:Paper) RETURN a.id AS from_id, b.id AS to_id"
        ).fetchall()
        assert len(edge_rows) == 1, f"expected 1 CITES edge, got {edge_rows}"
        assert edge_rows[0]["from_id"] == "W1"
        assert edge_rows[0]["to_id"] == "W2"

        print("write/read on the writer connection: OK")
    finally:
        conn.close()


def read_only_from_second_thread(store_path: Path) -> None:
    result = {}
    error = {}

    def worker():
        try:
            conn = relate.connect(str(store_path), read_only=True)
            try:
                rows = conn.execute(
                    "MATCH (p:Paper) RETURN count(p) AS n"
                ).fetchall()
                result["count"] = rows[0]["n"]

                try:
                    conn.execute(
                        "CREATE (:Paper {id: $id, title: $title})",
                        {"id": "W3", "title": "Should not be written"},
                    )
                    error["write_should_have_failed"] = True
                except relate.Error:
                    result["write_rejected"] = True
            finally:
                conn.close()
        except Exception as exc:  # surfaced on the main thread below
            error["exception"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    if "exception" in error:
        raise error["exception"]
    assert "write_should_have_failed" not in error, (
        "a read-only connection accepted a write"
    )
    assert result.get("count") == 2, f"expected 2 papers via read-only conn, got {result}"
    assert result.get("write_rejected") is True

    print("read-only query from a second thread: OK")
    print("read-only connection correctly rejected a write: OK")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        store_path = Path(tmp_dir) / "smoke.rl"
        write_sample_data(store_path)
        read_only_from_second_thread(store_path)

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
