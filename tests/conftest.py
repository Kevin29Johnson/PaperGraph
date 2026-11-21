import csv
from pathlib import Path

import pytest

from app.db import RelateWorker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = PROJECT_ROOT / "data" / "csv"


@pytest.fixture(scope="session")
def worker():
    w = RelateWorker()
    yield w
    w.close()


def _read_csv(name: str) -> list[dict[str, str]]:
    with (CSV_DIR / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def papers_csv() -> list[dict[str, str]]:
    return _read_csv("papers.csv")


@pytest.fixture(scope="session")
def authors_csv() -> list[dict[str, str]]:
    return _read_csv("authors.csv")


@pytest.fixture(scope="session")
def authored_csv() -> list[dict[str, str]]:
    return _read_csv("authored.csv")


@pytest.fixture(scope="session")
def topics_csv() -> list[dict[str, str]]:
    return _read_csv("topics.csv")


@pytest.fixture(scope="session")
def has_topic_csv() -> list[dict[str, str]]:
    return _read_csv("has_topic.csv")


@pytest.fixture(scope="session")
def cites_csv() -> list[dict[str, str]]:
    return _read_csv("cites.csv")


@pytest.fixture(scope="session")
def seed_papers(papers_csv) -> list[dict[str, str]]:
    return [p for p in papers_csv if p["is_seed"] == "true"]
