"""FastAPI app: serves the static UI and six read-only JSON endpoints.

Security, per spec:
- Every id path/query param is validated against the OpenAlex id pattern
  before it reaches a query; anything else is rejected with 400.
- Search input is trimmed and capped at 200 characters before use.
- The Relate connection (app/db.py) is read-only; there is no endpoint that
  runs arbitrary Cypher.
- Binds to 127.0.0.1 by default (see the run command in the README).
"""

from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import relate
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import queries, related
from app.db import DEFAULT_STORE_PATH, RelateWorker

STATIC_DIR = Path(__file__).resolve().parent / "static"
OPENALEX_BARE_ID_RE = re.compile(r"^[AWT]\d+$")


def full_id(bare_id: str) -> str:
    """Validate a bare OpenAlex id (e.g. 'W123') and expand it to the full
    'https://openalex.org/W123' form the store uses. Anything that doesn't
    match the pattern is rejected before it ever reaches a query."""
    if not OPENALEX_BARE_ID_RE.match(bare_id):
        raise HTTPException(status_code=400, detail=f"invalid id: {bare_id!r}")
    return f"https://openalex.org/{bare_id}"


def bare_id(full: str) -> str:
    return full.rsplit("/", 1)[-1]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.worker = RelateWorker()
    app.state.last_query_time = None
    yield
    app.state.worker.close()


app = FastAPI(title="PaperGraph", lifespan=lifespan)


def timed(request: Request, fn: Callable, *args, **kwargs) -> tuple[Any, float]:
    """Run fn against the shared worker, recording query_ms and last_query_time."""
    start = time.perf_counter()
    result = fn(request.app.state.worker, *args, **kwargs)
    query_ms = round((time.perf_counter() - start) * 1000, 2)
    request.app.state.last_query_time = datetime.now(timezone.utc).isoformat()
    return result, query_ms


OPENALEX_URL_PREFIX = "https://openalex.org/"


def strip_full_ids(obj: Any) -> Any:
    """Rewrite every full OpenAlex URL value in a response (under any key --
    'id', 'from_id', 'to_id', ...) to the bare form the API and UI use
    everywhere else."""
    if isinstance(obj, dict):
        return {key: strip_full_ids(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [strip_full_ids(item) for item in obj]
    if isinstance(obj, str) and obj.startswith(OPENALEX_URL_PREFIX):
        return bare_id(obj)
    return obj


@app.get("/api/search")
def api_search(request: Request, q: str = Query("")):
    results, query_ms = timed(request, queries.search_papers, q)
    return {"results": strip_full_ids(results), "query_ms": query_ms}


@app.get("/api/paper/{paper_id}")
def api_get_paper(request: Request, paper_id: str):
    pid = full_id(paper_id)
    paper, query_ms = timed(request, queries.get_paper, pid)
    if paper is None:
        raise HTTPException(status_code=404, detail="paper not found")
    return {**strip_full_ids(paper), "query_ms": query_ms}


@app.get("/api/related/{paper_id}")
def api_related(request: Request, paper_id: str):
    pid = full_id(paper_id)
    results, ms1 = timed(request, related.get_related_papers, pid)
    subgraph_ids = [pid] + [r["id"] for r in results]
    cites_edges, ms2 = timed(request, queries.cites_edges_among, subgraph_ids)
    return {
        "results": strip_full_ids(results),
        "cites_edges": strip_full_ids(cites_edges),
        "query_ms": round(ms1 + ms2, 2),
    }


@app.get("/api/path")
def api_path(request: Request, from_: str = Query(..., alias="from"), to: str = Query(...)):
    from_full = full_id(from_)
    to_full = full_id(to)
    result, query_ms = timed(request, queries.citation_path, from_full, to_full)
    if result is None:
        return {"path": None, "directed": None, "query_ms": query_ms}
    return {**strip_full_ids(result), "query_ms": query_ms}


@app.get("/api/topic/{topic_id}/key-papers")
def api_key_papers(request: Request, topic_id: str):
    tid = full_id(topic_id)
    results, query_ms = timed(request, queries.key_papers_in_topic, tid)
    return {"results": strip_full_ids(results), "query_ms": query_ms}


@app.get("/api/author/{author_id}")
def api_author(request: Request, author_id: str):
    aid = full_id(author_id)
    papers, ms1 = timed(request, queries.author_papers, aid)
    coauthors, ms2 = timed(request, queries.author_coauthors, aid)
    return {
        "papers": strip_full_ids(papers),
        "co_authors": strip_full_ids(coauthors),
        "query_ms": round(ms1 + ms2, 2),
    }


@app.get("/api/health")
def api_health(request: Request):
    stats, query_ms = timed(request, queries.dataset_stats)
    return {
        "store_path": str(DEFAULT_STORE_PATH),
        "relate_version": relate.__version__,
        "stats": stats,
        "last_query_time": request.app.state.last_query_time,
        "query_ms": query_ms,
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
