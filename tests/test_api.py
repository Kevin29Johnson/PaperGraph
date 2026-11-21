import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_returns_200_and_valid_json(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stats"]["papers"] > 0
    assert "query_ms" in body


def test_search_returns_200(client):
    resp = client.get("/api/search", params={"q": "graph convolutional"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) > 0
    assert all(len(r["id"]) > 0 and "/" not in r["id"] for r in body["results"])


def test_search_quote_character_does_not_break(client):
    resp = client.get("/api/search", params={"q": "o'brien\" OR 1=1"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_long_query_is_truncated_not_rejected(client):
    resp = client.get("/api/search", params={"q": "x" * 500})
    assert resp.status_code == 200


def test_get_paper_valid_id(client):
    resp = client.get("/api/paper/W2519887557")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Semi-Supervised Classification with Graph Convolutional Networks"
    assert body["id"] == "W2519887557"


def test_get_paper_invalid_id_returns_400(client):
    resp = client.get("/api/paper/not-an-id")
    assert resp.status_code == 400


def test_get_paper_unknown_id_returns_404(client):
    resp = client.get("/api/paper/W0000000000")
    assert resp.status_code == 404


def test_related_valid_id(client):
    resp = client.get("/api/related/W2519887557")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) > 0
    assert all("/" not in r["id"] for r in results)


def test_related_invalid_id_returns_400(client):
    resp = client.get("/api/related/<script>")
    assert resp.status_code == 400


def test_path_valid_ids(client):
    resp = client.get("/api/path", params={"from": "W2766453196", "to": "W2519887557"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["directed"] is True
    assert body["path"][0]["id"] == "W2766453196"


def test_path_invalid_id_returns_400(client):
    resp = client.get("/api/path", params={"from": "garbage", "to": "W2519887557"})
    assert resp.status_code == 400


def test_key_papers_valid_topic(client):
    resp = client.get("/api/topic/T11273/key-papers")
    assert resp.status_code == 200
    assert len(resp.json()["results"]) > 0


def test_key_papers_invalid_topic_returns_400(client):
    resp = client.get("/api/topic/not-a-topic/key-papers")
    assert resp.status_code == 400


def test_author_valid_id(client):
    resp = client.get("/api/author/A5025691113")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["papers"]) > 0


def test_author_invalid_id_returns_400(client):
    resp = client.get("/api/author/bad")
    assert resp.status_code == 400


def test_index_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
