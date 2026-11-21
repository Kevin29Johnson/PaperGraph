"""Combine bibliographic coupling, co-citation, and embedding similarity into
one related-papers score, each part shown and backed by evidence.

Candidate pool: the union of the top 50 by vector similarity, the top 50 by
bibliographic coupling, and the top 50 by co-citation. All three signals are
then computed for every candidate in that union, not just the ones each
individual top-50 list happened to surface -- a candidate can be a strong
coupling match while having middling similarity (or vice versa), and only
scoring the full union lets those candidates compete on the combined score.

Normalisation, using in-set counts only (CITES edges never leave the set):
- similarity: Relate's vector.similarity.cosine, already on relate's 0..1
  scale, not raw cosine -1..1 (see relatedb.ai/docs, differences-from-neo4j.md:
  "Raw cosine runs from -1 to 1 ... to match relate's similarity scale").
  Verified directly: vector.similarity.cosine(a, b) == SEARCH ... SCORE for
  the same pair. That scale alone is too flat to compare across candidates
  (real cosine similarities for this model cluster tightly, e.g. 0.80-0.95),
  so it's additionally min-max rescaled across this target's own candidate
  pool: the closest candidate becomes 1.0, the furthest becomes 0.0.
- coupling: shared_references / sqrt(refs_t * refs_c) -- Salton's cosine
  measure (the standard bibliometric formula for both coupling and
  co-citation strength) over each paper's in-set reference count, so a paper
  with few in-set references isn't penalised just for citing less. This is
  not the overlap coefficient (which divides by min(refs_t, refs_c) instead).
- co-citation: shared_citers / sqrt(cites_t * cites_c), the same measure
  over each paper's in-set citation count.
Both are naturally in 0..1 (shared count can't exceed either paper's own
in-set degree) and need no further rescaling. A zero denominator (a paper
with no in-set references, or none citing it) makes that term 0 rather than
raising a ZeroDivisionError.

Weights start equal; the constants below are the only thing to change to
retune them against real seed papers.
"""

from __future__ import annotations

import math
from typing import Any

from app.db import RelateWorker

WEIGHTS = {"coupling": 1 / 3, "cocitation": 1 / 3, "similarity": 1 / 3}
CANDIDATE_POOL_SIZE = 50
EVIDENCE_LIMIT = 25


def get_related_papers(
    worker: RelateWorker, paper_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    degrees = worker.query(
        """
        MATCH (t:Paper {id: $id})
        OPTIONAL MATCH (t)-[:CITES]->(r)
        WITH t, count(r) AS refs
        OPTIONAL MATCH (x:Paper)-[:CITES]->(t)
        RETURN refs, count(x) AS cites
        """,
        {"id": paper_id},
    )
    if not degrees:
        return []
    refs_t, cites_t = degrees[0]["refs"], degrees[0]["cites"]

    similarity_candidates = worker.query(
        """
        MATCH (t:Paper {id: $id})
        WITH t.embedding AS vec
        MATCH (c:Paper)
        SEARCH c IN (VECTOR INDEX paper_embedding FOR vec LIMIT $k) SCORE AS similarity
        RETURN c.id AS id
        """,
        {"id": paper_id, "k": CANDIDATE_POOL_SIZE + 1},
    )
    coupling_candidates = worker.query(
        """
        MATCH (t:Paper {id: $id})-[:CITES]->(r:Paper)<-[:CITES]-(c:Paper)
        WHERE c.id <> $id
        WITH c.id AS id, count(DISTINCT r) AS shared
        ORDER BY shared DESC
        LIMIT $k
        RETURN id
        """,
        {"id": paper_id, "k": CANDIDATE_POOL_SIZE},
    )
    cocitation_candidates = worker.query(
        """
        MATCH (t:Paper {id: $id})<-[:CITES]-(x:Paper)-[:CITES]->(c:Paper)
        WHERE c.id <> $id
        WITH c.id AS id, count(DISTINCT x) AS shared
        ORDER BY shared DESC
        LIMIT $k
        RETURN id
        """,
        {"id": paper_id, "k": CANDIDATE_POOL_SIZE},
    )

    sources_by_id: dict[str, set[str]] = {}
    for rows, source in (
        (similarity_candidates, "similarity"),
        (coupling_candidates, "coupling"),
        (cocitation_candidates, "cocitation"),
    ):
        for row in rows:
            sources_by_id.setdefault(row["id"], set()).add(source)

    candidate_ids = [cid for cid in sources_by_id if cid != paper_id]
    if not candidate_ids:
        return []

    titles = {
        r["id"]: r
        for r in worker.query(
            "MATCH (c:Paper) WHERE c.id IN $ids RETURN c.id AS id, c.title AS title, c.year AS year",
            {"ids": candidate_ids},
        )
    }
    similarities = {
        r["id"]: r["similarity"]
        for r in worker.query(
            """
            UNWIND $ids AS cid
            MATCH (t:Paper {id: $id}), (c:Paper {id: cid})
            RETURN c.id AS id, vector.similarity.cosine(t.embedding, c.embedding) AS similarity
            """,
            {"id": paper_id, "ids": candidate_ids},
        )
    }
    shared_refs = {
        r["id"]: r["shared"]
        for r in worker.query(
            """
            MATCH (t:Paper {id: $id})-[:CITES]->(r:Paper)<-[:CITES]-(c:Paper)
            WHERE c.id IN $ids
            RETURN c.id AS id, count(DISTINCT r) AS shared
            """,
            {"id": paper_id, "ids": candidate_ids},
        )
    }
    shared_citers = {
        r["id"]: r["shared"]
        for r in worker.query(
            """
            MATCH (t:Paper {id: $id})<-[:CITES]-(x:Paper)-[:CITES]->(c:Paper)
            WHERE c.id IN $ids
            RETURN c.id AS id, count(DISTINCT x) AS shared
            """,
            {"id": paper_id, "ids": candidate_ids},
        )
    }
    candidate_degrees = {
        r["id"]: r
        for r in worker.query(
            """
            MATCH (c:Paper) WHERE c.id IN $ids
            OPTIONAL MATCH (c)-[:CITES]->(r)
            WITH c, count(r) AS refs
            OPTIONAL MATCH (x:Paper)-[:CITES]->(c)
            RETURN c.id AS id, refs, count(x) AS cites
            """,
            {"ids": candidate_ids},
        )
    }

    def saltons_cosine(shared: int, deg_a: int, deg_b: int) -> float:
        if deg_a == 0 or deg_b == 0:
            return 0.0
        return shared / math.sqrt(deg_a * deg_b)

    raw_similarities = [similarities.get(cid, 0.0) for cid in candidate_ids]
    min_sim, max_sim = min(raw_similarities), max(raw_similarities)

    def normalize_similarity(raw: float) -> float:
        if max_sim == min_sim:
            return 1.0
        return (raw - min_sim) / (max_sim - min_sim)

    scored = []
    for cid in candidate_ids:
        refs_c = candidate_degrees[cid]["refs"]
        cites_c = candidate_degrees[cid]["cites"]
        shared_refs_count = shared_refs.get(cid, 0)
        shared_citers_count = shared_citers.get(cid, 0)
        coupling_norm = saltons_cosine(shared_refs_count, refs_t, refs_c)
        cocitation_norm = saltons_cosine(shared_citers_count, cites_t, cites_c)
        similarity_norm = normalize_similarity(similarities.get(cid, 0.0))
        score = (
            WEIGHTS["coupling"] * coupling_norm
            + WEIGHTS["cocitation"] * cocitation_norm
            + WEIGHTS["similarity"] * similarity_norm
        )
        scored.append(
            {
                "id": cid,
                "title": titles[cid]["title"],
                "year": titles[cid]["year"],
                "score": round(score, 4),
                "coupling": round(coupling_norm, 4),
                "cocitation": round(cocitation_norm, 4),
                "similarity": round(similarity_norm, 4),
                "shared_references_count": shared_refs_count,
                "co_citing_count": shared_citers_count,
                "found_via": sorted(sources_by_id[cid]),
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:limit]
    for entry in top:
        entry["evidence"] = _get_evidence(worker, paper_id, entry["id"])
    return top


def _get_evidence(worker: RelateWorker, target_id: str, candidate_id: str) -> dict[str, Any]:
    shared_references = worker.query(
        """
        MATCH (t:Paper {id: $t})-[:CITES]->(r:Paper)<-[:CITES]-(c:Paper {id: $c})
        RETURN r.id AS id, r.title AS title
        LIMIT $limit
        """,
        {"t": target_id, "c": candidate_id, "limit": EVIDENCE_LIMIT},
    )
    co_citing_papers = worker.query(
        """
        MATCH (t:Paper {id: $t})<-[:CITES]-(x:Paper)-[:CITES]->(c:Paper {id: $c})
        RETURN x.id AS id, x.title AS title
        LIMIT $limit
        """,
        {"t": target_id, "c": candidate_id, "limit": EVIDENCE_LIMIT},
    )
    return {"shared_references": shared_references, "co_citing_papers": co_citing_papers}
