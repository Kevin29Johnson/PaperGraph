import pytest

from app import queries


def test_is_valid_openalex_id():
    assert queries.is_valid_openalex_id("https://openalex.org/W2519887557")
    assert queries.is_valid_openalex_id("https://openalex.org/A5025691113")
    assert queries.is_valid_openalex_id("https://openalex.org/T11273")
    assert not queries.is_valid_openalex_id("W2519887557")
    assert not queries.is_valid_openalex_id("https://openalex.org/W123; DROP")
    assert not queries.is_valid_openalex_id("")


@pytest.mark.parametrize(
    "seed_title",
    [
        "Semi-Supervised Classification with Graph Convolutional Networks",
        "Inductive Representation Learning on Large Graphs",
        "Graph Attention Networks",
        "node2vec: Scalable Feature Learning for Networks",
        "Translating Embeddings for Modeling Multi-relational Data",
    ],
)
def test_search_finds_each_seed(worker, seed_papers, seed_title):
    expected = next(p for p in seed_papers if p["title"] == seed_title)
    results = queries.search_papers(worker, seed_title[:30])
    assert any(r["id"] == expected["id"] for r in results)


def test_search_handles_quote_and_special_characters(worker):
    results = queries.search_papers(worker, "graph\" OR '1'='1")
    assert results == []


def test_search_respects_limit(worker):
    results = queries.search_papers(worker, "graph", limit=3)
    assert len(results) <= 3


def test_get_paper_returns_details(worker, seed_papers):
    gcn = next(p for p in seed_papers if "Graph Convolutional Networks" in p["title"])
    paper = queries.get_paper(worker, gcn["id"])
    assert paper is not None
    assert paper["title"] == gcn["title"]
    assert paper["worldwide_citations"] == int(gcn["cited_by_count"])
    assert paper["in_set_citations"] >= 0
    assert any(a["name"] == "Thomas Kipf" for a in paper["authors"])
    assert len(paper["topics"]) >= 1


def test_get_paper_unknown_id_returns_none(worker):
    assert queries.get_paper(worker, "https://openalex.org/W0000000000") is None


def test_citation_path_finds_known_path(worker, seed_papers):
    gat = next(p for p in seed_papers if p["title"] == "Graph Attention Networks")
    gcn = next(
        p for p in seed_papers if p["title"] == "Semi-Supervised Classification with Graph Convolutional Networks"
    )
    result = queries.citation_path(worker, gat["id"], gcn["id"])
    assert result is not None
    assert result["path"][0]["id"] == gat["id"]
    assert result["path"][-1]["id"] == gcn["id"]
    assert len(result["path"]) >= 2
    assert result["reversed"] is False


def test_citation_path_tries_both_directions_and_keeps_shorter(worker, papers_csv):
    # LightRAG directly cites GraphRAG (1 hop), but GraphRAG (older) cannot
    # cite the newer LightRAG directly -- only a long indirect chain connects
    # that direction. Asking from=GraphRAG, to=LightRAG must still find the
    # short real citation and report it in its true (reversed) direction.
    graphrag = next(p for p in papers_csv if p["title"].startswith("From Local to Global"))
    lightrag = next(p for p in papers_csv if p["title"].startswith("LightRAG"))

    result = queries.citation_path(worker, graphrag["id"], lightrag["id"])
    assert result is not None
    assert result["directed"] is True
    assert result["reversed"] is True
    assert len(result["path"]) == 2
    assert result["path"][0]["id"] == lightrag["id"]
    assert result["path"][1]["id"] == graphrag["id"]


def test_citation_path_same_paper_returns_trivial_path(worker, seed_papers):
    gcn = next(p for p in seed_papers if "Graph Convolutional Networks" in p["title"])
    result = queries.citation_path(worker, gcn["id"], gcn["id"])
    assert result == {
        "path": [{"id": gcn["id"], "title": gcn["title"], "year": int(gcn["year"])}],
        "directed": True,
        "reversed": False,
    }


def test_citation_path_returns_none_for_unknown_paper(worker, seed_papers):
    gcn = next(p for p in seed_papers if "Graph Convolutional Networks" in p["title"])
    result = queries.citation_path(worker, gcn["id"], "https://openalex.org/W0000000000")
    assert result is None


def test_key_papers_in_topic_includes_seed(worker, seed_papers, has_topic_csv):
    gcn = next(
        p for p in seed_papers if p["title"] == "Semi-Supervised Classification with Graph Convolutional Networks"
    )
    topic_id = next(row["topic_id"] for row in has_topic_csv if row["paper_id"] == gcn["id"])
    results = queries.key_papers_in_topic(worker, topic_id)
    assert len(results) > 0
    assert any(r["id"] == gcn["id"] for r in results)
    in_set = [r["in_set_citations"] for r in results]
    assert in_set == sorted(in_set, reverse=True)


def test_author_papers_and_coauthors(worker, authored_csv, authors_csv):
    kipf_id = next(a["id"] for a in authors_csv if a["name"] == "Thomas Kipf")
    papers = queries.author_papers(worker, kipf_id)
    assert len(papers) >= 1

    coauthors = queries.author_coauthors(worker, kipf_id)
    assert any(c["name"] == "Max Welling" for c in coauthors)


def test_cites_edges_among_finds_known_edge(worker, cites_csv):
    known_edge = cites_csv[0]
    edges = queries.cites_edges_among(worker, [known_edge["from_id"], known_edge["to_id"]])
    assert any(
        e["from_id"] == known_edge["from_id"] and e["to_id"] == known_edge["to_id"] for e in edges
    )
    for e in edges:
        assert e["from_id"] in {known_edge["from_id"], known_edge["to_id"]}
        assert e["to_id"] in {known_edge["from_id"], known_edge["to_id"]}


def test_cites_edges_among_empty_for_disjoint_ids(worker):
    edges = queries.cites_edges_among(worker, ["https://openalex.org/W0000000000", "https://openalex.org/W0000000001"])
    assert edges == []


def test_dataset_stats_matches_csv_counts(worker, papers_csv, authors_csv, topics_csv, cites_csv):
    stats = queries.dataset_stats(worker)
    assert stats["papers"] == len(papers_csv)
    assert stats["authors"] == len(authors_csv)
    assert stats["topics"] == len(topics_csv)
    assert stats["edges"]["cites"] == len(cites_csv)
    years = [int(p["year"]) for p in papers_csv if p["year"]]
    assert stats["year_range"] == [min(years), max(years)]
