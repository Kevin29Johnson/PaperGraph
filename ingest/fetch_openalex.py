"""Fetch seed papers from OpenAlex, hop backward (references) and forward
(top citing works), and write the ingestion CSVs.

Verified against OpenAlex's live API and help.openalex.org docs (Nov 2026):
- Auth: a query param `api_key`, read from the OPENALEX_API_KEY environment
  variable. The spec's OPENALEX_MAILTO "polite pool" scheme has been retired;
  OpenAlex now requires a free API key for cursor paging past the first page,
  so this script deviates from the spec here deliberately.
- Resolve a DOI directly: GET /works/doi:<doi>
- Resolve an OpenAlex ID directly: GET /works/<id>
- Works citing a given work: filter=cites:<id>
- Batch lookup by OpenAlex ID: filter=openalex:ID1|ID2|...  (up to 100 values)
- select=... limits returned fields (keeps abstracts etc. out entirely)
- referenced_works / related_works are lists of OpenAlex work IDs, not objects
- primary_topic is an object with id/display_name/score/subfield/field/domain
- authorships[i] has author.id, author.display_name, author_position
- primary_location.source.display_name is the venue name
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

API_BASE = "https://api.openalex.org"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "ingest" / "cache"
CSV_DIR = PROJECT_ROOT / "data" / "csv"
SEEDS_PATH = PROJECT_ROOT / "ingest" / "seeds.yaml"

WORK_SELECT = (
    "id,doi,display_name,publication_year,cited_by_count,primary_location,"
    "primary_topic,authorships,referenced_works,related_works,ids"
)

FORWARD_HOP_LIMIT = 50
REQUEST_DELAY_SECONDS = 0.1
MAX_RETRIES = 5

ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def load_dotenv_if_present() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def get_api_key() -> str:
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "OPENALEX_API_KEY is not set. Put it in a local .env file "
            "(OPENALEX_API_KEY=...) or export it in your shell."
        )
    return key


class OpenAlexClient:
    """Thin GET wrapper with a disk cache, so reruns don't re-hit the API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, cache_key: str) -> Path:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return CACHE_DIR / f"{digest}.json"

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        sorted_params = sorted((k, str(v)) for k, v in params.items())
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted_params)
        cache_path = self._cache_path(cache_key)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        request_params = dict(params)
        request_params["api_key"] = self.api_key

        delay = 1.0
        last_response = None
        for attempt in range(MAX_RETRIES):
            time.sleep(REQUEST_DELAY_SECONDS)
            response = self.session.get(API_BASE + path, params=request_params, timeout=30)
            last_response = response
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    response.raise_for_status()
                time.sleep(delay)
                delay *= 2
                continue
            response.raise_for_status()
            data = response.json()
            cache_path.write_text(json.dumps(data), encoding="utf-8")
            return data
        assert last_response is not None
        last_response.raise_for_status()
        raise RuntimeError(f"unreachable: {path}")


def load_seeds(path: Path) -> dict[str, list[str]]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    return config["fields"]


def parse_identifier(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if raw.startswith("openalex:"):
        return "openalex", raw[len("openalex:"):].strip()
    return "doi", raw


def short_id(full_id: str) -> str:
    return full_id.rsplit("/", 1)[-1]


def resolve_work(client: OpenAlexClient, raw_identifier: str) -> dict[str, Any]:
    kind, value = parse_identifier(raw_identifier)
    path = f"/works/doi:{value}" if kind == "doi" else f"/works/{value}"
    return client.get(path, {"select": WORK_SELECT})


def fetch_forward_citations(client: OpenAlexClient, work_id: str) -> list[dict[str, Any]]:
    data = client.get(
        "/works",
        {
            "filter": f"cites:{short_id(work_id)}",
            "sort": "cited_by_count:desc",
            "per_page": FORWARD_HOP_LIMIT,
            "select": WORK_SELECT,
        },
    )
    return data.get("results", [])


def fetch_works_by_ids(client: OpenAlexClient, ids: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    short_ids = [short_id(i) for i in ids]
    for start in range(0, len(short_ids), 100):
        batch = short_ids[start : start + 100]
        data = client.get(
            "/works",
            {
                "filter": "openalex:" + "|".join(batch),
                "per_page": 100,
                "select": WORK_SELECT,
            },
        )
        results.extend(data.get("results", []))
    return results


TITLE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return TITLE_NORMALIZE_RE.sub(" ", title.lower()).strip()


def dedupe_duplicate_papers(
    collected: dict[str, dict[str, Any]],
    is_seed: set[str],
    paper_fields: dict[str, set[str]],
    seed_related: list[tuple[str, int, str]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """OpenAlex sometimes has more than one record for what is really the
    same paper (e.g. a journal-article version and a book version of the
    same title/year). Group papers by (normalized title, year); for any
    group with more than one record, keep the one with the most references
    and redirect the rest to it, combining both records' reference lists so
    no citation edges are lost. Returns (old_id -> canonical_id, a report of
    each merge for printing)."""
    groups: dict[tuple[str, int | None], list[str]] = {}
    for wid, work in collected.items():
        key = (normalize_title(work.get("display_name") or ""), work.get("publication_year"))
        groups.setdefault(key, []).append(wid)

    redirect: dict[str, str] = {}
    report: list[dict[str, Any]] = []
    for ids in groups.values():
        if len(ids) < 2:
            continue
        ids_sorted = sorted(
            ids, key=lambda i: len(collected[i].get("referenced_works") or []), reverse=True
        )
        canonical = ids_sorted[0]
        dropped = ids_sorted[1:]
        combined_refs = set(collected[canonical].get("referenced_works") or [])
        for dup_id in dropped:
            combined_refs.update(collected[dup_id].get("referenced_works") or [])
            redirect[dup_id] = canonical
        collected[canonical]["referenced_works"] = sorted(combined_refs)
        report.append(
            {
                "title": collected[canonical].get("display_name"),
                "year": collected[canonical].get("publication_year"),
                "kept": canonical,
                "dropped": dropped,
            }
        )

    if not redirect:
        return redirect, report

    for dup_id in redirect:
        del collected[dup_id]
        if dup_id in is_seed:
            is_seed.discard(dup_id)
            is_seed.add(redirect[dup_id])
        if dup_id in paper_fields:
            paper_fields.setdefault(redirect[dup_id], set()).update(paper_fields.pop(dup_id))

    # Rewrite every remaining paper's own reference list so a reference that
    # pointed at a now-removed duplicate id points at its canonical id instead.
    for work in collected.values():
        refs = work.get("referenced_works") or []
        work["referenced_works"] = [redirect.get(r, r) for r in refs]

    seed_related[:] = [
        (redirect.get(sid, sid), rank, redirect.get(rid, rid)) for sid, rank, rid in seed_related
    ]

    return redirect, report


def extract_arxiv_id(doi: str | None) -> str | None:
    if not doi:
        return None
    match = ARXIV_DOI_RE.search(doi)
    return match.group(1) if match else None


def venue_name(work: dict[str, Any]) -> str | None:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return source.get("display_name")


def main() -> int:
    load_dotenv_if_present()
    api_key = get_api_key()
    client = OpenAlexClient(api_key)

    fields = load_seeds(SEEDS_PATH)

    collected: dict[str, dict[str, Any]] = {}
    is_seed: set[str] = set()
    paper_fields: dict[str, set[str]] = {}
    seed_related: list[tuple[str, int, str]] = []

    def add_paper(work: dict[str, Any], field: str) -> None:
        wid = work["id"]
        collected.setdefault(wid, work)
        paper_fields.setdefault(wid, set()).add(field)

    print("Resolving seeds...")
    seed_records: list[tuple[str, dict[str, Any]]] = []
    for field, identifiers in fields.items():
        for raw_identifier in identifiers:
            work = resolve_work(client, raw_identifier)
            seed_records.append((field, work))
            add_paper(work, field)
            is_seed.add(work["id"])
            print(f"  [{field}] {work.get('display_name')!r} ({work['id']})")

    print(f"\nResolved {len(seed_records)} seed entries "
          f"({len(set(w['id'] for _, w in seed_records))} unique papers).")

    print("\nBackward hop (references of each seed)...")
    backward_ids: set[str] = set()
    for _field, work in seed_records:
        for ref_id in work.get("referenced_works") or []:
            if ref_id not in collected:
                backward_ids.add(ref_id)
        for rank, related_id in enumerate(work.get("related_works") or [], start=1):
            seed_related.append((work["id"], rank, related_id))

    backward_works = fetch_works_by_ids(client, sorted(backward_ids))
    backward_lookup = {w["id"]: w for w in backward_works}
    print(f"  fetched {len(backward_works)} backward-hop works")
    for field, work in seed_records:
        for ref_id in work.get("referenced_works") or []:
            match = backward_lookup.get(ref_id)
            if match is not None:
                add_paper(match, field)

    print("\nForward hop (top citing works per seed)...")
    for field, work in seed_records:
        citing_works = fetch_forward_citations(client, work["id"])
        for citing in citing_works:
            add_paper(citing, field)
        print(f"  [{field}] {work.get('display_name')!r}: {len(citing_works)} citing works")

    print(f"\nTotal papers in set (before dedup): {len(collected)}")

    print("\nMerging duplicate OpenAlex records (same normalized title + year)...")
    redirect, merge_report = dedupe_duplicate_papers(collected, is_seed, paper_fields, seed_related)
    if merge_report:
        for merge in merge_report:
            print(
                f"  merged {len(merge['dropped'])} duplicate(s) of "
                f"{merge['title']!r} ({merge['year']}) into {merge['kept']}"
            )
    print(f"  {len(redirect)} duplicate record(s) merged across {len(merge_report)} group(s)")
    print(f"Total papers in set (after dedup): {len(collected)}")

    cites_rows: set[tuple[str, str]] = set()
    dropped_refs = 0
    for wid, work in collected.items():
        for ref_id in work.get("referenced_works") or []:
            if ref_id == wid:
                continue
            if ref_id in collected:
                cites_rows.add((wid, ref_id))
            else:
                dropped_refs += 1

    authors: dict[str, str] = {}
    authored_rows: list[tuple[str, str, str]] = []
    topics: dict[str, str] = {}
    has_topic_rows: list[tuple[str, str, Any]] = []

    for wid, work in collected.items():
        for authorship in work.get("authorships") or []:
            author = authorship.get("author") or {}
            author_id = author.get("id")
            if not author_id:
                continue
            authors[author_id] = author.get("display_name") or ""
            authored_rows.append((author_id, wid, authorship.get("author_position") or ""))

        primary_topic = work.get("primary_topic")
        if primary_topic and primary_topic.get("id"):
            topic_id = primary_topic["id"]
            topics[topic_id] = primary_topic.get("display_name") or ""
            score = primary_topic.get("score")
            has_topic_rows.append((wid, topic_id, "" if score is None else score))

    CSV_DIR.mkdir(parents=True, exist_ok=True)

    with (CSV_DIR / "papers.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["id", "doi", "title", "year", "cited_by_count", "venue", "arxiv_id", "is_seed", "fields"]
        )
        for wid, work in sorted(collected.items()):
            writer.writerow(
                [
                    wid,
                    work.get("doi") or "",
                    work.get("display_name") or "",
                    work.get("publication_year") or "",
                    work.get("cited_by_count") or 0,
                    venue_name(work) or "",
                    extract_arxiv_id(work.get("doi")) or "",
                    "true" if wid in is_seed else "false",
                    "|".join(sorted(paper_fields.get(wid, []))),
                ]
            )

    with (CSV_DIR / "authors.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name"])
        for author_id, name in sorted(authors.items()):
            writer.writerow([author_id, name])

    with (CSV_DIR / "authored.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["author_id", "paper_id", "position"])
        writer.writerows(authored_rows)

    with (CSV_DIR / "topics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name"])
        for topic_id, name in sorted(topics.items()):
            writer.writerow([topic_id, name])

    with (CSV_DIR / "has_topic.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["paper_id", "topic_id", "score"])
        writer.writerows(has_topic_rows)

    with (CSV_DIR / "cites.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["from_id", "to_id"])
        writer.writerows(sorted(cites_rows))

    with (CSV_DIR / "openalex_related.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["seed_id", "rank", "related_id"])
        writer.writerows(seed_related)

    print("\n--- Summary ---")
    print(f"Papers:  {len(collected)}")
    print(f"Authors: {len(authors)}")
    print(f"Topics:  {len(topics)}")
    print(f"CITES edges (in-set): {len(cites_rows)}")
    print(f"References dropped (outside set): {dropped_refs}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
