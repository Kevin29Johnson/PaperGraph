# Benchmarks

Each of the 6 core operations run 100 times (after 5 warm-up runs),
directly against the store through the same `RelateWorker`/`queries.py`/
`related.py` code the API uses -- no HTTP overhead included, matching
what the API's own `query_ms` measures.

## Machine

- CPU: Intel64 Family 6 Model 126 Stepping 5, GenuineIntel
- RAM: 7.8 GB
- OS: Windows 11 (Windows-11-10.0.26200-SP0)
- Python: 3.12.10
- relate-db (Python library): 1.0.1

## Results

| Operation | Median (ms) | p95 (ms) |
|---|---|---|
| search | 35.22 | 54.35 |
| paper | 4.69 | 7.98 |
| related | 239.29 | 343.68 |
| path | 1.05 | 2.05 |
| topic | 66.22 | 101.77 |
| author | 1.49 | 2.72 |

`related` is the slowest by a wide margin because it's the only operation that runs many queries per call: target-paper degrees, three 50-candidate pools (vector search, coupling, co-citation), four batched detail queries over the combined candidate set, then one evidence lookup per returned result. Every other operation is 1-3 queries.
