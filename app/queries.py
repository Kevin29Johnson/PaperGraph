"""All Cypher used by the app. Every statement is parameterized -- user input
is never formatted into a query string -- and every row-returning query has
an explicit row limit.

Citation-path hop bound (*1..6) is a fixed internal constant, not user input:
Cypher's variable-length relationship bound must be a literal in the pattern,
it cannot be passed as a parameter (verified against relate-db 1.0.1 docs).
"""

from __future__ import annotations

import re
from typing import Any

from app.db import DEFAULT_ROW_LIMIT, RelateWorker

OPENALEX_ID_RE = re.compile(r"^https://openalex\.org/[AWT]\d+$")
MAX_SEARCH_LENGTH = 200
CITATION_PATH_MAX_HOPS = 6


def is_valid_openalex_id(value: str) -> bool:
    return bool(OPENALEX_ID_RE.match(value))


def search_papers(worker: RelateWorker, q: str, limit: int = 10) -> list[dict[str, Any]]:
    q = q.strip()[:MAX_SEARCH_LENGTH]
    return worker.query(
        """
        MATCH (p:Paper)
        WHERE toLower(p.title) CONTAINS toLower($q)
        RETURN p.id AS id, p.title AS title, p.year AS year, p.venue AS venue
        ORDER BY p.cited_by_count DESC
        LIMIT $limit
        """,
        {"q": q, "limit": limit},
    )


def get_paper(worker: RelateWorker, paper_id: str) -> dict[str, Any] | None:
    rows = worker.query(
        """
        MATCH (p:Paper {id: $id})
        OPTIONAL MATCH (c:Paper)-[:CITES]->(p)
        WITH p, count(c) AS in_set_citations
        RETURN p.id AS id, p.title AS title, p.year AS year, p.doi AS doi,
               p.venue AS venue, p.arxiv_id AS arxiv_id, p.is_seed AS is_seed,
               p.fields AS fields, p.cited_by_count AS worldwide_citations,
               in_set_citations
        """,
        {"id": paper_id},
    )
    if not rows:
        return None
    paper = rows[0]

    paper["authors"] = worker.query(
        """
        MATCH (a:Author)-[r:AUTHORED]->(p:Paper {id: $id})
        RETURN a.id AS id, a.name AS name, r.position AS position
        ORDER BY CASE r.position WHEN 'first' THEN 0 WHEN 'middle' THEN 1 ELSE 2 END
        LIMIT $limit
        """,
        {"id": paper_id, "limit": DEFAULT_ROW_LIMIT},
    )
    paper["topics"] = worker.query(
        """
        MATCH (p:Paper {id: $id})-[r:HAS_TOPIC]->(t:Topic)
        RETURN t.id AS id, t.name AS name, r.score AS score
        LIMIT $limit
        """,
        {"id": paper_id, "limit": DEFAULT_ROW_LIMIT},
    )
    return paper


def _directed_citation_path(worker: RelateWorker, citing_id: str, cited_id: str) -> list[dict[str, Any]] | None:
    """Shortest path where citing_id cites ... cites cited_id (that order)."""
    rows = worker.query(
        f"""
        MATCH p = shortestPath((a:Paper {{id: $citing_id}})-[:CITES*1..{CITATION_PATH_MAX_HOPS}]->(b:Paper {{id: $cited_id}}))
        RETURN [n IN nodes(p) | {{id: n.id, title: n.title, year: n.year}}] AS path
        """,
        {"citing_id": citing_id, "cited_id": cited_id},
    )
    return rows[0]["path"] if rows else None


def citation_path(worker: RelateWorker, from_id: str, to_id: str) -> dict[str, Any] | None:
    if from_id == to_id:
        rows = worker.query(
            "MATCH (p:Paper {id: $id}) RETURN p.id AS id, p.title AS title, p.year AS year",
            {"id": from_id},
        )
        if not rows:
            return None
        return {"path": [rows[0]], "directed": True, "reversed": False}

    # Try both citation directions -- the paper the user put in "from" is not
    # necessarily the citing one -- and keep the shorter path, since a short
    # path in the true citation direction is more informative than a long one
    # forced the way the user happened to type it.
    forward = _directed_citation_path(worker, from_id, to_id)
    backward = _directed_citation_path(worker, to_id, from_id)

    if forward and backward:
        chosen, reversed_ = (forward, False) if len(forward) <= len(backward) else (backward, True)
    elif forward:
        chosen, reversed_ = forward, False
    elif backward:
        chosen, reversed_ = backward, True
    else:
        chosen = None

    if chosen is not None:
        return {"path": chosen, "directed": True, "reversed": reversed_}

    undirected = worker.query(
        f"""
        MATCH p = shortestPath((a:Paper {{id: $from_id}})-[:CITES*1..{CITATION_PATH_MAX_HOPS}]-(b:Paper {{id: $to_id}}))
        RETURN [n IN nodes(p) | {{id: n.id, title: n.title, year: n.year}}] AS path
        """,
        {"from_id": from_id, "to_id": to_id},
    )
    if undirected:
        return {"path": undirected[0]["path"], "directed": False, "reversed": False}

    return None


def key_papers_in_topic(
    worker: RelateWorker, topic_id: str, limit: int = DEFAULT_ROW_LIMIT
) -> list[dict[str, Any]]:
    return worker.query(
        """
        MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic {id: $topic_id})
        OPTIONAL MATCH (c:Paper)-[:CITES]->(p)
        WITH p, count(c) AS in_set_citations
        RETURN p.id AS id, p.title AS title, p.year AS year,
               in_set_citations, p.cited_by_count AS worldwide_citations
        ORDER BY in_set_citations DESC, worldwide_citations DESC
        LIMIT $limit
        """,
        {"topic_id": topic_id, "limit": limit},
    )


def author_papers(
    worker: RelateWorker, author_id: str, limit: int = DEFAULT_ROW_LIMIT
) -> list[dict[str, Any]]:
    return worker.query(
        """
        MATCH (a:Author {id: $author_id})-[:AUTHORED]->(p:Paper)
        RETURN p.id AS id, p.title AS title, p.year AS year
        ORDER BY p.year DESC
        LIMIT $limit
        """,
        {"author_id": author_id, "limit": limit},
    )


def author_coauthors(
    worker: RelateWorker, author_id: str, limit: int = DEFAULT_ROW_LIMIT
) -> list[dict[str, Any]]:
    return worker.query(
        """
        MATCH (a:Author {id: $author_id})-[:AUTHORED]->(:Paper)<-[:AUTHORED]-(co:Author)
        WHERE co.id <> a.id
        RETURN co.id AS id, co.name AS name, count(*) AS shared_papers
        ORDER BY shared_papers DESC
        LIMIT $limit
        """,
        {"author_id": author_id, "limit": limit},
    )


def cites_edges_among(worker: RelateWorker, paper_ids: list[str]) -> list[dict[str, Any]]:
    """All CITES edges where both ends are in paper_ids -- used by the UI to
    draw the real citation structure among the papers shown in a graph view."""
    return worker.query(
        """
        MATCH (a:Paper)-[:CITES]->(b:Paper)
        WHERE a.id IN $ids AND b.id IN $ids
        RETURN a.id AS from_id, b.id AS to_id
        LIMIT $limit
        """,
        {"ids": paper_ids, "limit": DEFAULT_ROW_LIMIT * 4},
    )


def dataset_stats(worker: RelateWorker) -> dict[str, Any]:
    paper_stats = worker.query(
        "MATCH (p:Paper) RETURN count(p) AS count, min(p.year) AS min_year, max(p.year) AS max_year"
    )[0]
    author_count = worker.query("MATCH (a:Author) RETURN count(a) AS count")[0]["count"]
    topic_count = worker.query("MATCH (t:Topic) RETURN count(t) AS count")[0]["count"]
    cites_count = worker.query("MATCH ()-[r:CITES]->() RETURN count(r) AS count")[0]["count"]
    authored_count = worker.query("MATCH ()-[r:AUTHORED]->() RETURN count(r) AS count")[0]["count"]
    has_topic_count = worker.query("MATCH ()-[r:HAS_TOPIC]->() RETURN count(r) AS count")[0]["count"]

    return {
        "papers": paper_stats["count"],
        "authors": author_count,
        "topics": topic_count,
        "edges": {
            "cites": cites_count,
            "authored": authored_count,
            "has_topic": has_topic_count,
        },
        "year_range": [paper_stats["min_year"], paper_stats["max_year"]],
    }
