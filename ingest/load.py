"""Build the committed Relate store from the ingestion CSVs and embeddings.

Rerunning this script deletes and rebuilds data/papergraph.rl from scratch,
in one transaction.

Verified directly against a real Relate store (relate-db 1.0.1) before use:
- CREATE CONSTRAINT <name> FOR (n:Label) REQUIRE n.prop IS UNIQUE
- CREATE INDEX <name> FOR (n:Label) ON (n.prop)
- CREATE VECTOR INDEX <name> FOR (n:Label) ON (n.prop)
  OPTIONS {indexConfig: {`vector.dimensions`: N, `vector.similarity_function`: 'cosine'}}
- UNWIND $rows AS row CREATE (...)  /  UNWIND $rows AS row MATCH (...) CREATE (...)-[:REL]->(...)
  both work with $rows bound to a list-of-dicts parameter.
- vector(list, dimensions, FLOAT32) builds a stored vector property from a
  parameter list.
- relate.connect() opens an existing .rl file rather than resetting it, so
  any existing store file is deleted here before connecting.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import relate

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = PROJECT_ROOT / "data" / "csv"
DATA_DIR = PROJECT_ROOT / "data"
STORE_PATH = DATA_DIR / "papergraph.rl"
EMBEDDINGS_PATH = DATA_DIR / "embeddings.npy"
EMBEDDINGS_IDS_PATH = DATA_DIR / "embeddings_ids.json"

EMBEDDING_DIM = 384
BATCH_SIZE = 2000


def read_csv(name: str) -> list[dict[str, str]]:
    with (CSV_DIR / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def none_if_blank(value: str) -> str | None:
    return value if value else None


def execute_in_batches(conn, statement: str, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        conn.execute(statement, {"rows": batch})


def load_embeddings() -> dict[str, list[float]]:
    embeddings = np.load(EMBEDDINGS_PATH)
    ids = json.loads(EMBEDDINGS_IDS_PATH.read_text(encoding="utf-8"))
    if embeddings.shape[1] != EMBEDDING_DIM:
        raise SystemExit(
            f"Expected {EMBEDDING_DIM}-dim embeddings, got {embeddings.shape[1]}"
        )
    return {pid: embeddings[i].tolist() for i, pid in enumerate(ids)}


def main() -> int:
    if STORE_PATH.exists():
        STORE_PATH.unlink()
        print(f"Deleted existing store at {STORE_PATH}")

    papers = read_csv("papers.csv")
    authors = read_csv("authors.csv")
    authored = read_csv("authored.csv")
    topics = read_csv("topics.csv")
    has_topic = read_csv("has_topic.csv")
    cites = read_csv("cites.csv")
    embeddings_by_id = load_embeddings()

    conn = relate.connect(str(STORE_PATH))
    try:
        conn.autocommit = False

        print("Creating constraints and indexes...")
        conn.execute(
            "CREATE CONSTRAINT paper_id IF NOT EXISTS "
            "FOR (p:Paper) REQUIRE p.id IS UNIQUE"
        )
        conn.execute(
            "CREATE CONSTRAINT author_id IF NOT EXISTS "
            "FOR (a:Author) REQUIRE a.id IS UNIQUE"
        )
        conn.execute(
            "CREATE CONSTRAINT topic_id IF NOT EXISTS "
            "FOR (t:Topic) REQUIRE t.id IS UNIQUE"
        )
        conn.execute(
            "CREATE INDEX paper_title IF NOT EXISTS FOR (p:Paper) ON (p.title)"
        )

        print(f"Loading {len(papers)} papers...")
        paper_rows = []
        missing_embeddings = 0
        for row in papers:
            pid = row["id"]
            embedding = embeddings_by_id.get(pid)
            if embedding is None:
                missing_embeddings += 1
            paper_rows.append(
                {
                    "id": pid,
                    "doi": none_if_blank(row["doi"]),
                    "title": row["title"],
                    "year": int(row["year"]) if row["year"] else None,
                    "cited_by_count": int(row["cited_by_count"] or 0),
                    "venue": none_if_blank(row["venue"]),
                    "arxiv_id": none_if_blank(row["arxiv_id"]),
                    "is_seed": row["is_seed"] == "true",
                    "fields": row["fields"].split("|") if row["fields"] else [],
                    "embedding": embedding or [0.0] * EMBEDDING_DIM,
                }
            )
        if missing_embeddings:
            print(f"  warning: {missing_embeddings} papers had no embedding (zero-filled)")

        execute_in_batches(
            conn,
            f"""
            UNWIND $rows AS row
            CREATE (:Paper {{
                id: row.id,
                doi: row.doi,
                title: row.title,
                year: row.year,
                cited_by_count: row.cited_by_count,
                venue: row.venue,
                arxiv_id: row.arxiv_id,
                is_seed: row.is_seed,
                fields: row.fields,
                embedding: vector(row.embedding, {EMBEDDING_DIM}, FLOAT32)
            }})
            """,
            paper_rows,
        )

        print(f"Loading {len(authors)} authors...")
        execute_in_batches(
            conn,
            "UNWIND $rows AS row CREATE (:Author {id: row.id, name: row.name})",
            [{"id": r["id"], "name": r["name"]} for r in authors],
        )

        print(f"Loading {len(topics)} topics...")
        execute_in_batches(
            conn,
            "UNWIND $rows AS row CREATE (:Topic {id: row.id, name: row.name})",
            [{"id": r["id"], "name": r["name"]} for r in topics],
        )

        print(f"Loading {len(authored)} AUTHORED edges...")
        execute_in_batches(
            conn,
            """
            UNWIND $rows AS row
            MATCH (a:Author {id: row.author_id}), (p:Paper {id: row.paper_id})
            CREATE (a)-[:AUTHORED {position: row.position}]->(p)
            """,
            [
                {
                    "author_id": r["author_id"],
                    "paper_id": r["paper_id"],
                    "position": r["position"],
                }
                for r in authored
            ],
        )

        print(f"Loading {len(has_topic)} HAS_TOPIC edges...")
        execute_in_batches(
            conn,
            """
            UNWIND $rows AS row
            MATCH (p:Paper {id: row.paper_id}), (t:Topic {id: row.topic_id})
            CREATE (p)-[:HAS_TOPIC {score: row.score}]->(t)
            """,
            [
                {
                    "paper_id": r["paper_id"],
                    "topic_id": r["topic_id"],
                    "score": float(r["score"]) if r["score"] else None,
                }
                for r in has_topic
            ],
        )

        print(f"Loading {len(cites)} CITES edges...")
        execute_in_batches(
            conn,
            """
            UNWIND $rows AS row
            MATCH (a:Paper {id: row.from_id}), (b:Paper {id: row.to_id})
            CREATE (a)-[:CITES]->(b)
            """,
            [{"from_id": r["from_id"], "to_id": r["to_id"]} for r in cites],
        )

        print("Creating vector index...")
        conn.execute(
            """
            CREATE VECTOR INDEX paper_embedding IF NOT EXISTS
            FOR (p:Paper) ON (p.embedding)
            OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}
            """,
            {"dim": EMBEDDING_DIM},
        )

        conn.commit()
        print("Committed.")
    except BaseException:
        conn.rollback()
        conn.close()
        raise

    # Read back counts with a fresh read for the summary (connection still open).
    paper_count = conn.execute("MATCH (p:Paper) RETURN count(p) AS n").fetchall()[0]["n"]
    author_count = conn.execute("MATCH (a:Author) RETURN count(a) AS n").fetchall()[0]["n"]
    topic_count = conn.execute("MATCH (t:Topic) RETURN count(t) AS n").fetchall()[0]["n"]
    cites_count = conn.execute(
        "MATCH ()-[r:CITES]->() RETURN count(r) AS n"
    ).fetchall()[0]["n"]
    authored_count = conn.execute(
        "MATCH ()-[r:AUTHORED]->() RETURN count(r) AS n"
    ).fetchall()[0]["n"]
    has_topic_count = conn.execute(
        "MATCH ()-[r:HAS_TOPIC]->() RETURN count(r) AS n"
    ).fetchall()[0]["n"]

    conn.close()

    print("\n--- Summary ---")
    print(f"Papers:  {paper_count}")
    print(f"Authors: {author_count}")
    print(f"Topics:  {topic_count}")
    print(f"CITES edges:     {cites_count}")
    print(f"AUTHORED edges:  {authored_count}")
    print(f"HAS_TOPIC edges: {has_topic_count}")
    print(f"Store file: {STORE_PATH} ({STORE_PATH.stat().st_size / (1024*1024):.2f} MB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
