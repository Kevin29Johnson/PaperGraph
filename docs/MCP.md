# Connecting an agent to PaperGraph (MCP)

PaperGraph's store is a plain Relate file (`data/papergraph.rl`). The `relate`
CLI can serve it read-only over MCP so a coding agent can explore the graph
and verify citations directly — without going through the web app at all.

Everything below was run and verified against:
- **Relate CLI**: `1.2.0` (`relate --version`)
- **relate-db (Python library, used by the app itself)**: `1.0.1`

on this machine, with the FastAPI app also running against the same file at
the same time.

## 1. The serve command

```bash
relate serve --database data/papergraph.rl --mcp stdio --read-only
```

Confirmed from `relate serve --help` on this binary: `--mcp stdio` serves the
stdio transport (newline-delimited JSON-RPC on stdin/stdout) in the
foreground until the host closes stdin. `--read-only` opens the store with
no writer lease and never creates it — every write attempt fails. The two
tools it exposes (`read_cypher`, `get_schema`) are additionally marked
`readOnlyHint: true` and reject write/schema statements on their own, so
read-only is enforced at both the CLI flag and the tool level.

**Conflict check (asked for explicitly):** started the FastAPI app
(`uvicorn app.main:app`, which opens its own read-only Python connection to
the same file) and then ran the command above while it was still running.
No conflict — Relate allows any number of simultaneous read-only openers of
a `.rl` file; only a *write* lease is exclusive, and nothing here ever
writes. Confirmed with a real query (see §4) run through the MCP server
while the API was live and serving its own requests.

## 2. Claude Code config

A portable `.mcp.json` is already committed at the repo root:

```json
{
  "mcpServers": {
    "papergraph": {
      "command": "relate",
      "args": ["serve", "--database", "data/papergraph.rl", "--mcp", "stdio", "--read-only"]
    }
  }
}
```

No personal paths in it — it relies on two things, both verified separately:
- **`data/papergraph.rl` resolves correctly** when Claude Code's working
  directory is the repo root (confirmed directly: ran the exact `relate
  serve --database data/papergraph.rl ...` command with the repo root as the
  current directory and it opened the store fine).
- **`relate` resolves on `PATH` with no full path needed** — confirmed in a
  real terminal (`relate --version` → `Relate v1.2.0`, run with no path at
  all). I could not get my own tool session in this conversation to pick
  that up (a stale-PATH quirk of this specific harness, not of your system —
  a live read of the actual Windows user `PATH` registry value confirms the
  `relate` install directory is genuinely on it), but a real terminal or a
  freshly-started Claude Code process reads the live `PATH`, which is what
  matters here.

**If Claude Code reports it can't find `relate`** (for example, if whatever
process launched VS Code/Claude Code was itself started before you ran the
installer, so it's still holding an old `PATH`): replace `"relate"` in
`.mcp.json` with the full path to the binary, e.g.
`"<path-to>\\relate.exe"` (find yours with, in PowerShell,
`(Get-Command relate).Source`). Restarting VS Code/Claude Code first is the
simpler fix and should make the plain `"relate"` form work.

Claude Code also supports adding this from the command line via
`claude mcp add` — I didn't have the `claude` CLI available in this terminal
session to confirm its exact flags directly, so treat that route as
unverified until you've checked `claude mcp add --help` yourself; the
`.mcp.json` above is the form I actually ran and confirmed.

After it's picked up (restart Claude Code if you just added or edited
`.mcp.json`), it should show up when you ask it what MCP tools it has.

## 3. Schema (from Relate's own `get_schema` tool, not hand-written)

```
Paper   (2,341 nodes)
  properties: id, doi, title, year, cited_by_count, venue, arxiv_id,
              is_seed, fields (list), embedding (vector)
  -[:CITES]->          Paper
  -[:HAS_TOPIC {score}]-> Topic

Author  (8,123 nodes)
  properties: id, name
  -[:AUTHORED {position}]-> Paper

Topic   (170 nodes)
  properties: id, name
```

`CITES` only ever points between two papers already in this dataset — it's
never a dangling reference to a paper that wasn't fetched. `score` on
`HAS_TOPIC` is OpenAlex's own topic-confidence value, not something computed
here.

## 4. Five example questions

Each of these was actually run against the live store through the MCP
server while writing this doc — the rows shown are real.

**1. "Does LightRAG cite GraphRAG?"**
```cypher
MATCH (a:Paper)-[:CITES]->(b:Paper)
WHERE a.title CONTAINS "LightRAG" AND b.title CONTAINS "From Local to Global"
RETURN a.title AS citing, b.title AS cited
```
→ `LightRAG: Simple and Fast Retrieval-Augmented Generation` cites
`From Local to Global: A Graph RAG Approach to Query-Focused Summarization`.

**2. "Which papers are related to GraphRAG and why?"** (a simplified,
single-signal version of what the app's `/api/related` endpoint computes with
all three signals combined — this one just surfaces shared references):
```cypher
MATCH (t:Paper {title: "From Local to Global: A Graph RAG Approach to Query-Focused Summarization"})
      -[:CITES]->(r:Paper)<-[:CITES]-(c:Paper)
WHERE c <> t
WITH c, count(DISTINCT r) AS shared_refs
RETURN c.title AS related_paper, shared_refs
ORDER BY shared_refs DESC
LIMIT 5
```
→ top result: *"Retrieval-Augmented Generation for Large Language Models: A
Survey"* with 12 shared references.

**3. "Who are the most-cited authors in this dataset?"** (by the total
worldwide citation count of their papers in the set):
```cypher
MATCH (a:Author)-[:AUTHORED]->(p:Paper)
RETURN a.name AS author, sum(p.cited_by_count) AS total_citations, count(p) AS papers
ORDER BY total_citations DESC
LIMIT 5
```
→ top result: Kaiming He, 5 papers, 344,900 total citations.

**4. "What's the shortest citation path from Graph Attention Networks to
GCN?"**
```cypher
MATCH p = shortestPath(
  (a:Paper {id: "https://openalex.org/W2766453196"})-[:CITES*1..6]->
  (b:Paper {id: "https://openalex.org/W2519887557"})
)
RETURN [n IN nodes(p) | n.title] AS path
```
→ `Graph Attention Networks` → `Geometric deep learning on graphs and
manifolds using mixture model CNNs` → `Semi-Supervised Classification with
Graph Convolutional Networks` (2 hops; there's no direct edge between them in
OpenAlex's own reference data for GAT — see Limitations in the README).

**5. "What are the key papers in a given topic?"**
```cypher
MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic {name: "Advanced Graph Neural Networks"})
OPTIONAL MATCH (c:Paper)-[:CITES]->(p)
WITH p, count(c) AS in_set_citations
RETURN p.title AS title, p.year AS year, in_set_citations
ORDER BY in_set_citations DESC
LIMIT 5
```

## 5. Relate's one-command skill install

`relate skill list` on this machine reports:
```
claude-code: not installed (~/.claude/skills/relate)
copilot: not installed (~/.copilot/skills/relate)
```

So yes, this applies — Claude Code is a detected target. The command is:

```bash
relate skill install --agent claude-code
```

**Optional, and skipped here.** This would copy Relate's own bundled
skill/guidance into `~/.claude/skills/relate` globally for your user
account — every Claude Code session on this machine, not scoped to
PaperGraph. It doesn't affect this project or judging either way, so it was
left alone. `relate skill install --agent claude-code` installs it and
`relate skill remove --agent claude-code` removes it, whenever/if you want
it, independent of this project.
