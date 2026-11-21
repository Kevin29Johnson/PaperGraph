"""Benchmark each core app operation directly against the store (no HTTP),
measuring the same database work the API's query_ms reports.

Runs each operation 100 times after a short warm-up and writes median and
p95 latency (ms) to docs/benchmarks.md, along with the machine it ran on.
"""

from __future__ import annotations

import csv
import platform
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import relate  # noqa: E402

from app import queries, related  # noqa: E402
from app.db import RelateWorker  # noqa: E402

RUNS = 100
WARMUP_RUNS = 5


def read_csv(name: str) -> list[dict[str, str]]:
    with (PROJECT_ROOT / "data" / "csv" / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_benchmark_inputs() -> dict[str, str]:
    papers = read_csv("papers.csv")
    seeds_by_title = {p["title"]: p for p in papers if p["is_seed"] == "true"}
    gcn = seeds_by_title["Semi-Supervised Classification with Graph Convolutional Networks"]
    gat = seeds_by_title["Graph Attention Networks"]

    has_topic = read_csv("has_topic.csv")
    topic_id = next(row["topic_id"] for row in has_topic if row["paper_id"] == gcn["id"])

    authored = read_csv("authors.csv")
    kipf = next(a["id"] for a in authored if a["name"] == "Thomas Kipf")

    return {
        "paper_id": gcn["id"],
        "related_id": gcn["id"],
        "path_from": gat["id"],
        "path_to": gcn["id"],
        "topic_id": topic_id,
        "author_id": kipf,
    }


def time_runs(fn, *args) -> list[float]:
    for _ in range(WARMUP_RUNS):
        fn(*args)
    timings = []
    for _ in range(RUNS):
        start = time.perf_counter()
        fn(*args)
        timings.append((time.perf_counter() - start) * 1000)
    return timings


def percentile(values: list[float], pct: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def get_memory_gb() -> str:
    try:
        if platform.system() == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return f"{stat.ullTotalPhys / (1024 ** 3):.1f} GB"
    except Exception:
        pass
    return "unknown"


def main() -> int:
    inputs = pick_benchmark_inputs()
    worker = RelateWorker()
    try:
        operations = [
            ("search", lambda: queries.search_papers(worker, "graph")),
            ("paper", lambda: queries.get_paper(worker, inputs["paper_id"])),
            ("related", lambda: related.get_related_papers(worker, inputs["related_id"])),
            ("path", lambda: queries.citation_path(worker, inputs["path_from"], inputs["path_to"])),
            ("topic", lambda: queries.key_papers_in_topic(worker, inputs["topic_id"])),
            ("author", lambda: (queries.author_papers(worker, inputs["author_id"]),
                                 queries.author_coauthors(worker, inputs["author_id"]))),
        ]

        results = []
        for name, fn in operations:
            print(f"Benchmarking {name}...")
            timings = time_runs(fn)
            median = statistics.median(timings)
            p95 = percentile(timings, 95)
            results.append((name, median, p95))
            print(f"  median={median:.2f} ms  p95={p95:.2f} ms")
    finally:
        worker.close()

    uname = platform.uname()
    lines = [
        "# Benchmarks",
        "",
        "Each of the 6 core operations run 100 times (after 5 warm-up runs),",
        "directly against the store through the same `RelateWorker`/`queries.py`/",
        "`related.py` code the API uses -- no HTTP overhead included, matching",
        "what the API's own `query_ms` measures.",
        "",
        "## Machine",
        "",
        f"- CPU: {uname.processor or platform.machine()}",
        f"- RAM: {get_memory_gb()}",
        f"- OS: {uname.system} {uname.release} ({platform.platform()})",
        f"- Python: {platform.python_version()}",
        f"- relate-db (Python library): {relate.__version__}",
        "",
        "## Results",
        "",
        "| Operation | Median (ms) | p95 (ms) |",
        "|---|---|---|",
    ]
    for name, median, p95 in results:
        lines.append(f"| {name} | {median:.2f} | {p95:.2f} |")
    lines.append("")
    lines.append(
        "`related` is the slowest by a wide margin because it's the only "
        "operation that runs many queries per call: target-paper degrees, "
        "three 50-candidate pools (vector search, coupling, co-citation), "
        "four batched detail queries over the combined candidate set, then "
        "one evidence lookup per returned result. Every other operation is "
        "1-3 queries."
    )
    lines.append("")

    out_path = PROJECT_ROOT / "docs" / "benchmarks.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
