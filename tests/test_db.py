def test_store_opens_read_only(worker):
    rows = worker.query("MATCH (p:Paper) RETURN count(p) AS n")
    assert rows[0]["n"] > 0


def test_counts_match_ingestion_summary(
    worker, papers_csv, authors_csv, topics_csv, authored_csv, has_topic_csv, cites_csv
):
    assert worker.query("MATCH (p:Paper) RETURN count(p) AS n")[0]["n"] == len(papers_csv)
    assert worker.query("MATCH (a:Author) RETURN count(a) AS n")[0]["n"] == len(authors_csv)
    assert worker.query("MATCH (t:Topic) RETURN count(t) AS n")[0]["n"] == len(topics_csv)
    assert (
        worker.query("MATCH ()-[r:AUTHORED]->() RETURN count(r) AS n")[0]["n"]
        == len(authored_csv)
    )
    assert (
        worker.query("MATCH ()-[r:HAS_TOPIC]->() RETURN count(r) AS n")[0]["n"]
        == len(has_topic_csv)
    )
    assert worker.query("MATCH ()-[r:CITES]->() RETURN count(r) AS n")[0]["n"] == len(cites_csv)


def test_query_is_parameterized_against_quote_characters(worker):
    # A quote in the input must not break the query -- it's bound as a
    # parameter value, never formatted into the Cypher string.
    rows = worker.query(
        "MATCH (p:Paper) WHERE toLower(p.title) CONTAINS toLower($q) RETURN count(p) AS n",
        {"q": "o'brien\" ; DROP"},
    )
    assert rows[0]["n"] == 0
