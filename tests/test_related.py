import pytest

from app import related

SEED_TITLES = [
    "Semi-Supervised Classification with Graph Convolutional Networks",
    "Inductive Representation Learning on Large Graphs",
    "Graph Attention Networks",
]


@pytest.mark.parametrize("seed_title", SEED_TITLES)
def test_related_papers_for_known_seeds(worker, seed_papers, seed_title):
    target = next(p for p in seed_papers if p["title"] == seed_title)
    results = related.get_related_papers(worker, target["id"], limit=10)

    assert len(results) > 0
    assert all(r["id"] != target["id"] for r in results)

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)

    for r in results:
        assert 0.0 <= r["coupling"] <= 1.0
        assert 0.0 <= r["cocitation"] <= 1.0
        assert 0.0 <= r["similarity"] <= 1.0
        assert r["shared_references_count"] >= 0
        assert r["co_citing_count"] >= 0
        assert r["found_via"]
        assert "shared_references" in r["evidence"]
        assert "co_citing_papers" in r["evidence"]


def test_related_papers_unknown_paper_returns_empty(worker):
    assert related.get_related_papers(worker, "https://openalex.org/W0000000000") == []


def test_related_papers_respects_limit(worker, seed_papers):
    gcn = next(p for p in seed_papers if "Graph Convolutional Networks" in p["title"])
    results = related.get_related_papers(worker, gcn["id"], limit=3)
    assert len(results) <= 3
