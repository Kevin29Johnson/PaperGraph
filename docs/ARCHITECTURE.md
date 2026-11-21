# PaperGraph — How it all works

This is a from-scratch audit of the codebase as it exists right now, written so
you (or anyone else) can understand every file, why it exists, and how a
single click in the browser turns into data on the screen.

Last audited: 2026-11-21. Tests at the time of writing: **43 passed, 0
failed** (`python -m pytest`).

---

## 1. The big picture

There are two completely separate phases, and it's important to keep them
apart in your head:

```
┌──────────────────────────────┐        ┌──────────────────────────────────┐
│   INGESTION (done once, by    │        │   RUNTIME (what judges/users run) │
│   me, on my machine)           │        │                                    │
│                                │        │                                    │
│  OpenAlex API (the internet)  │        │   No internet. No API key.        │
│        │                      │        │                                    │
│        ▼                      │        │   Browser ──HTTP──▶ FastAPI       │
│  ingest/fetch_openalex.py     │        │                        │          │
│        │                      │        │                        ▼          │
│        ▼                      │        │                  app/db.py        │
│  data/csv/*.csv  (7 files)    │        │                  (1 worker thread, │
│        │                      │        │                   read-only)      │
│        ▼                      │        │                        │          │
│  ingest/embed.py              │        │                        ▼          │
│        │                      │        │             data/papergraph.rl    │
│        ▼                      │        │             (the Relate file,     │
│  data/embeddings.npy          │        │              56.62 MB, committed) │
│        │                      │        │                        ▲          │
│        ▼                      │        │                        │          │
│  ingest/load.py               │        │   Agent ──MCP(stdio)──▶ relate    │
│        │                      │        │   (Claude Code,        serve      │
│        ▼                      │        │    read-only)       --read-only   │
│  data/papergraph.rl  ─────────┼────────────────▶ (this file is what both   │
│  (committed to the repo)      │        │          the app and the agent    │
└──────────────────────────────┘        │          read, never written to)  │
                                         └──────────────────────────────────┘
```

**Ingestion** touches the internet once, builds one file, and is never run
again by a judge or user. **Runtime** never touches the internet — it just
reads that one file and answers questions about it, whether the reader is
the browser (through FastAPI) or a coding agent (through `relate serve`'s MCP
server — see Section 5).

One credential distinction worth keeping straight: **ingestion needs a free
OpenAlex API key**, read from `OPENALEX_API_KEY` in a local `.env` file (never
hard-coded, never committed) — OpenAlex requires it for paging past the first
page of results. **The running app needs no key, no account, and no internet
at all**; it only ever reads the already-built `data/papergraph.rl` file.

---

## 2. Ingestion — building the dataset

### `ingest/seeds.yaml`
A plain list of DOIs (or, for the one seed that has no DOI in OpenAlex, an
`openalex:<id>` form instead), grouped under a field name (`graph-ml`). This
is the only hand-curated input in the whole project — 30 well-known papers
picked as starting points (GCN, GAT, TransE, GraphRAG, etc.). Everything else
is discovered automatically from these.

### `ingest/fetch_openalex.py`
This is the script that talks to the internet. Step by step:

1. **Resolve seeds** — looks up each of the 30 seed papers on OpenAlex, getting back
   the full paper record (title, year, citation count, authors, topic,
   reference list, etc.).
2. **Backward hop** — for every seed, OpenAlex gives its *reference list*
   (every paper it cites). This script fetches all of those too — this is how
   the dataset grows past the original 30.
3. **Forward hop** — for every seed, it asks OpenAlex "who cites this paper?"
   and takes the top 50 (by citation count). This is how the dataset includes
   newer papers that build on the seeds.
4. **Merge duplicates** — OpenAlex sometimes has two separate records for
   what's really the same paper (e.g. a conference version and a preprint, or
   a journal article and a book chapter with the exact same title and year).
   `dedupe_duplicate_papers()` groups papers by (normalized title, year),
   keeps whichever record has the most references, and redirects everything
   that pointed at the dropped one onto the surviving one. This found and
   merged 25 such pairs in the current data.
5. **Build the edges** — a citation only becomes a `CITES` edge in our graph
   if *both* papers ended up in our collected set (we don't keep edges to
   papers we never fetched).
6. **Write CSVs** — `papers.csv`, `authors.csv`, `authored.csv`, `topics.csv`,
   `has_topic.csv`, `cites.csv`, `openalex_related.csv` — all under
   `data/csv/`.

Every HTTP response is cached to disk (`ingest/cache/`, not committed) keyed
by a hash of the request, so re-running the script after a code change is
fast and doesn't re-hit the network.

**Current numbers:** 2,341 papers, 8,123 authors, 170 topics, 30,983 `CITES`
edges, 12,052 `AUTHORED` edges, 2,337 `HAS_TOPIC` edges.

### `ingest/embed.py`
For every paper, it builds a short text (title + its topic name) and feeds it
through a small AI model that runs entirely on your CPU
(`sentence-transformers/all-MiniLM-L6-v2`, Apache 2.0 licensed) — no internet
needed once the model itself is downloaded. The model turns each paper's text
into a list of 384 numbers (a vector). Papers "about similar things" end up
with similar vectors. These vectors are L2-normalized, which is what makes
their cosine similarity a very simple calculation later.

Output: `data/embeddings.npy` (the 2,341×384 matrix of numbers) and
`data/embeddings_ids.json` (which paper ID each row belongs to).

### `ingest/load.py`
Takes the CSVs + the embeddings and writes them all into one Relate database
file, `data/papergraph.rl`. This is the *only* script that ever writes to
that file, and it always deletes any existing copy and rebuilds from scratch
in a single transaction (nothing partially-written is ever left behind).
While building it, it also creates:
- Uniqueness constraints on `Paper.id`, `Author.id`, `Topic.id`
- A regular index on `Paper.title` (for fast title search)
- A **vector index** on `Paper.embedding` (so "find similar papers" can use
  Relate's built-in nearest-neighbor search instead of comparing every pair
  by hand)

---

## 3. The graph model (what's actually stored)

| Node | Properties |
|---|---|
| `(:Paper)` | id, doi, title, year, cited_by_count (worldwide), venue, arxiv_id, is_seed, fields, embedding (vector) |
| `(:Author)` | id, name |
| `(:Topic)` | id, name |

| Relationship | Meaning |
|---|---|
| `(:Paper)-[:CITES]->(:Paper)` | the first paper cites the second |
| `(:Author)-[:AUTHORED {position}]->(:Paper)` | position is "first"/"middle"/"last" |
| `(:Paper)-[:HAS_TOPIC {score}]->(:Topic)` | its one primary topic, with OpenAlex's own confidence score |

Everything in the app is built out of queries over this exact shape — there's
no other data anywhere.

---

## 4. Runtime — the Python backend

### `app/db.py` — the only thing that talks to the database file
Relate's rule (verified directly against its docs): a database connection
belongs to whichever thread opened it, and only one thing can write to a file
store at a time. Since this app never writes, that's not a problem by itself
— but FastAPI handles many requests concurrently on different threads, and
you can't just pass one connection around between threads.

The fix: `RelateWorker` starts **one dedicated background thread** the moment
the app starts. That thread opens the file **read-only** and then just sits
in a loop, waiting for queries. Every other part of the app that wants to run
a query doesn't touch the database directly — it drops a `(query, params)`
job onto a queue and waits for a `Future` to be filled in with the answer.
This keeps the "one connection, one thread" rule satisfied no matter how many
web requests come in at once.

### `app/queries.py` — every search/lookup the app can do
Plain functions, each one a parameterized Cypher query (user input is always
passed as a `$parameter`, never pasted into the query text — that's what
makes it immune to injection). The functions:
- `search_papers` — case-insensitive title search
- `get_paper` — one paper's full details, its authors, its topic
- `citation_path` — explained in detail just below
- `key_papers_in_topic` — ranked list of papers tagged with a topic
- `author_papers` / `author_coauthors` — an author's own papers, and who
  they've published with
- `cites_edges_among` — "of these specific papers, which ones cite each
  other" (used to draw real citation arrows in the graph picture)
- `dataset_stats` — the counts shown in the header

**`citation_path` in detail.** Given a "from" paper and a "to" paper, it
doesn't just search one direction:
1. It tries the shortest directed path **both ways** — from → to, and
   to → from — since the paper typed into "from" isn't necessarily the
   citing one. Whichever direction actually has a (shorter, or the only)
   real path wins.
2. The returned path is always ordered **citing → cited**, regardless of
   which paper the caller put in "from" vs "to" — so the chips in the UI
   always read correctly top-to-bottom. A `reversed` flag in the result says
   whether that ended up being the opposite of what was typed.
3. If neither direction has a directed path within 6 hops, it falls back to
   an **undirected** path (citation edges followed in either direction) and
   labels the result `directed: false`, so the UI can say so honestly instead
   of implying a real citation chain that isn't there.
4. Asking for a path from a paper to itself returns that one paper as a
   trivial one-node path, rather than erroring (Relate's own `shortestPath`
   rejects equal start/end nodes).

One thing `citation_path` itself does **not** compute: if a step in the
returned path goes from an older paper to a newer one, that's flagged
client-side, in `app.js`, by comparing the `year` values already present on
each returned path node — not something the Cypher query or this function
decides.

### `app/related.py` — the "why are these related" scoring
This is the heart of the explainability pitch. For a given paper, it finds
candidates three different ways and scores each one on three signals:

**Finding candidates** (the pool to score):
1. Top 50 most similar by embedding vector (Relate's vector index)
2. Top 50 by **bibliographic coupling** (papers that share the most
   references with the target)
3. Top 50 by **co-citation** (papers that the same later papers cite
   alongside the target)

These three lists are combined (a paper only needs to appear on *one* of
them to be considered), and then **all three signals are computed for every
candidate**, not just the list(s) it originally came from — so a paper that's
a strong citation-match but a mediocre similarity-match still gets a fair
combined score.

**Scoring each candidate** (all three end up on a 0–1 scale):
- **Similarity**: `vector.similarity.cosine(target, candidate)`, then
  rescaled so the *closest* candidate in this specific pool becomes 1.0 and
  the *furthest* becomes 0.0 (otherwise real similarity scores all cluster
  tightly around 0.8–0.95 and barely differentiate anything).
- **Coupling** and **co-citation**: both use **Salton's cosine measure**, the
  standard bibliometric formula for exactly this —
  `shared_count / sqrt(degree_target × degree_candidate)`. For coupling,
  "degree" is each paper's own in-set reference count; for co-citation, it's
  each paper's own in-set citation count. Dividing by both papers' degrees
  stops highly connected papers, such as large surveys, from appearing
  related to everything just because they cite or are cited so much.

The three scores are averaged (currently equal weight, ⅓ each — easy to
retune in one spot) into one overall score, and the top 10 are returned with
**evidence attached**: the actual shared reference papers and the actual
co-citing papers, so you can click through and check the claim yourself.

### `app/main.py` — the web server
A FastAPI app with 7 endpoints (`/api/search`, `/api/paper/{id}`,
`/api/related/{id}`, `/api/path`, `/api/topic/{id}/key-papers`,
`/api/author/{id}`, `/api/health`), plus it serves the actual website files.
A few things worth knowing:
- Every ID coming in from the browser is a short form like `W2519887557`.
  `full_id()` checks it against a strict pattern (`^[AWT]\d+$`) and rejects
  anything else with an HTTP 400 *before* it ever reaches a database query.
  Internally the database stores the full `https://openalex.org/W2519887557`
  form; `strip_full_ids()` converts every ID in a response back to the short
  form on the way out, so the browser never has to deal with long URLs.
- Every response includes `query_ms` — literally how long that request's
  database work took, measured with `time.perf_counter()`.
- There is **no endpoint that accepts raw Cypher** — the only things the
  outside world can ask for are the 7 fixed operations above.
- No key, no login, no internet access of any kind is required to run this
  server or use the website — see the credential note in Section 1.

---

## 5. Agent access (MCP)

The same `data/papergraph.rl` file can be served read-only over MCP, so a
coding agent can query the graph directly instead of going through the web
app:

```bash
relate serve --database data/papergraph.rl --mcp stdio --read-only
```

This exposes two tools: `get_schema` (introspects the labels, properties, and
relationship types — used to write the schema summary in `docs/MCP.md`
without guessing) and `read_cypher` (runs one read-only Cypher statement).
Read-only is enforced **twice over**: the `--read-only` flag refuses every
write at the connection level, and both tools are separately marked
`readOnlyHint: true` and reject write/schema statements of their own accord.

A portable `.mcp.json` at the repo root wires this up for Claude Code
(`"command": "relate"`, a relative `data/papergraph.rl` path — no
machine-specific paths in it).

This was tested running **at the same time** as the FastAPI app, both
reading the same file: no conflict. Relate allows any number of simultaneous
read-only openers of a store; only a write lease is exclusive, and nothing
on either path ever writes.

Full details, the exact schema it reports, and five example questions with
real Cypher and real results: **[docs/MCP.md](MCP.md)**.

---

## 6. Runtime — the website (what you actually see)

Three files, no build step, no framework:

### `app/static/index.html`
The skeleton: a search box, a panel for the open paper + its graph, a panel
for the ranked related-papers list, and a few overlay popups (citation path
finder, author view, topic view) that are hidden until opened.

### `app/static/app.css`
All the visual styling, including light/dark theme support (it follows your
OS setting automatically via `prefers-color-scheme`), the score-bar colors,
and the graph's line/node styling.

### `app/static/app.js`
One file, no framework, built around a few ideas:
- **`el(tag, props, children)`** — a tiny helper used everywhere instead of
  `innerHTML`, so nothing from the server can ever be interpreted as HTML
  (every piece of text goes in through `.textContent`, never through markup).
- **Hash-based routing** — opening a paper sets the URL to
  `#paper-W2519887557`. A `hashchange` listener (`handleHashChange`) reacts to
  that by calling the API and rendering the result. This is why the browser's
  Back/Forward buttons and a plain refresh both correctly reopen the same
  paper, and why you can copy the URL and send it to someone.
- **The graph** is drawn with Cytoscape.js (a real open-source graph-drawing
  library, vendored locally so it works offline — its MIT license is at
  `app/static/vendor/LICENSE`). Each paper becomes a node; "related" links are
  drawn as thick neutral lines sized by score, and real citations among the
  shown papers are drawn separately as thin grey arrows — both can be told
  apart, and citations can be toggled off entirely.
- **One shared, plain-object application state**: a single `state` object at
  the top of the file (which paper is open, its related list, the current
  graph instance) is the single source of truth the whole file reads from.
  There's no framework managing it and no hidden copies of it — every
  `render...()` function reads directly from that one object, so the UI
  can't drift out of sync with itself.

---

## 7. One request, start to finish

Here's exactly what happens when you click the **GCN** preset chip:

1. `renderPresets()`'s click handler calls `navigateTo("W2519887557")`, which
   just sets `window.location.hash = "#paper-W2519887557"`.
2. The browser fires a `hashchange` event. `handleHashChange()` sees the hash
   matches `#paper-(.+)` and calls `loadPaper("W2519887557")`.
3. `loadPaper` shows a loading skeleton, then calls
   `fetch("/api/paper/W2519887557")`.
4. That HTTP request hits **FastAPI** (`app/main.py`). `full_id()` validates
   the shape of `"W2519887557"`, turns it into
   `"https://openalex.org/W2519887557"`.
5. `queries.get_paper(worker, full_id)` is called. It doesn't touch the
   database itself — it builds a `(query, params)` job and hands it to
   `RelateWorker.query()`, which puts it on a queue and blocks on a `Future`.
6. The **one dedicated database thread** (already running since the server
   started) picks the job off the queue, runs the actual Cypher against the
   read-only connection, and fills in the `Future` with the rows.
7. `get_paper` also runs two more such round-trips (one for authors, one for
   topics) and assembles one dict.
8. Back in `main.py`, `strip_full_ids()` walks that dict and shortens every
   OpenAlex URL back to `W2519887557`-style IDs, and the response goes out as
   JSON with a `query_ms` timing attached.
9. Back in the browser, `loadPaper` gets that JSON, calls `renderPaper()` to
   fill in the title/authors/topics/links, then immediately fires a second
   request to `/api/related/W2519887557`.
10. That one runs the whole three-signal scoring described above (several
    more round-trips through the same worker thread), comes back with the
    ranked list plus which papers actually cite each other among them.
11. `renderRelated()` builds the ranked cards on the right; `renderGraph()`
    builds the Cytoscape graph in the center.

Every other feature (search-as-you-type, the citation path finder, author/
topic popups) follows this same shape: browser event → `fetch()` to one of
the 7 endpoints → `queries.py`/`related.py` function → `RelateWorker` →
database thread → JSON back → a `render...()` function updates the DOM.

---

## 8. Tests (`tests/`)

43 tests, four files:
- `test_db.py` — the store actually opens read-only, and its counts match
  what the CSVs say they should be (a direct check that nothing was lost or
  duplicated between ingestion and the final database).
- `test_queries.py` — each function in `queries.py`, including edge cases
  like searching with a literal quote character in it, asking for a
  citation path between a paper and itself, and the bidirectional
  citation-path search finding the real short path regardless of which
  paper was typed into "from".
- `test_related.py` — the scoring function on real seed papers: results are
  sorted correctly, every score is within 0–1, a paper never relates to
  itself.
- `test_api.py` — every HTTP endpoint, using FastAPI's `TestClient` (no real
  network, no real server process) — correct status codes (200/400/404),
  and that bad input (quotes, garbage IDs) never breaks anything.

Run them with (venv active): `python -m pytest`

---

## 9. What's NOT in this project (on purpose)

- No LLM calls anywhere at runtime — the "similarity" signal is a pre-computed
  embedding, not a live AI call.
- No arbitrary-Cypher endpoint on the web API (the agent-facing MCP tool
  does run arbitrary *read-only* Cypher, by design — see Section 5 — but it
  is a separate, explicitly read-only surface, not part of `app/main.py`).
- No abstracts, full text, or affiliations stored — only what's needed for
  the features, by design.
- No writes at runtime — `data/papergraph.rl` is only ever touched by
  `ingest/load.py`, run by me, offline.

---

## 10. Quick file map

```
PaperGraph/
├── .env.example            template for OPENALEX_API_KEY (no real value)
├── .mcp.json                portable MCP config for Claude Code
├── .gitignore
├── LICENSE                  project license (MIT)
├── README.md
├── ingest/
│   ├── seeds.yaml          the only hand-picked input (30 seed papers)
│   ├── fetch_openalex.py   internet → CSVs (needs OPENALEX_API_KEY in .env)
│   ├── embed.py            CSVs → embeddings.npy
│   └── load.py             CSVs + embeddings → papergraph.rl
├── data/
│   ├── csv/                the 7 CSVs (committed, human-readable)
│   ├── embeddings.npy      the AI "fingerprints" (committed)
│   ├── embeddings_ids.json which paper ID each embeddings.npy row is (committed)
│   └── papergraph.rl       the actual database (committed, 56.62 MB)
├── app/
│   ├── db.py               the one database connection + worker thread
│   ├── queries.py          every Cypher query (search, paper, path, ...)
│   ├── related.py          the "why related" scoring
│   ├── main.py             FastAPI app, 7 endpoints (no key/internet needed)
│   └── static/
│       ├── index.html      page structure
│       ├── app.css         styling, light/dark theme
│       ├── app.js          all browser-side logic
│       └── vendor/
│           ├── cytoscape.min.js   the graph-drawing library
│           └── LICENSE            Cytoscape's MIT license
├── scripts/
│   ├── smoke_relate.py     the very first sanity test from Milestone 1
│   └── bench.py            measures the 6 core operations, writes docs/benchmarks.md
├── tests/                  43 automated tests
├── docs/
│   ├── ARCHITECTURE.md     this file
│   ├── MCP.md              agent setup, schema, example questions
│   ├── benchmarks.md       latency results + machine info
│   └── images/             screenshots used in README.md
├── requirements.txt        what the running app needs
├── requirements-ingest.txt what only the one-time ingestion needs
└── requirements-dev.txt    what only running the tests needs
```
