"""Embed each paper's title + topic name(s) with a local sentence-transformers
model, and write the result to data/ so load.py can build the store without
needing the model installed at runtime.

Model: sentence-transformers/all-MiniLM-L6-v2 (Apache 2.0 license), run once
here, offline, on CPU. Embeddings are L2-normalized so cosine similarity
reduces to a plain dot product in Relate's vector search.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = PROJECT_ROOT / "data" / "csv"
DATA_DIR = PROJECT_ROOT / "data"

EMBEDDINGS_PATH = DATA_DIR / "embeddings.npy"
IDS_PATH = DATA_DIR / "embeddings_ids.json"


def load_paper_texts() -> tuple[list[str], list[str]]:
    with (CSV_DIR / "papers.csv").open(encoding="utf-8") as f:
        papers = list(csv.DictReader(f))

    topic_names_by_paper: dict[str, list[str]] = {}
    topic_name_by_id: dict[str, str] = {}
    with (CSV_DIR / "topics.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            topic_name_by_id[row["id"]] = row["name"]
    with (CSV_DIR / "has_topic.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = topic_name_by_id.get(row["topic_id"])
            if name:
                topic_names_by_paper.setdefault(row["paper_id"], []).append(name)

    ids: list[str] = []
    texts: list[str] = []
    for paper in papers:
        pid = paper["id"]
        title = paper["title"]
        topic_names = topic_names_by_paper.get(pid, [])
        text = title if not topic_names else title + ". " + " ".join(topic_names)
        ids.append(pid)
        texts.append(text)
    return ids, texts


def main() -> int:
    ids, texts = load_paper_texts()
    print(f"Embedding {len(texts)} papers with {MODEL_NAME} (CPU)...")

    model = SentenceTransformer(MODEL_NAME, device="cpu")
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMBEDDINGS_PATH, embeddings)
    IDS_PATH.write_text(json.dumps(ids), encoding="utf-8")

    file_size_mb = EMBEDDINGS_PATH.stat().st_size / (1024 * 1024)
    print("\n--- Summary ---")
    print(f"Papers embedded: {len(ids)}")
    print(f"Embedding dimension: {embeddings.shape[1]}")
    print(f"{EMBEDDINGS_PATH.name} size: {file_size_mb:.2f} MB")

    gcn_title = "Semi-Supervised Classification with Graph Convolutional Networks"
    gcn_index = next((i for i, t in enumerate(texts) if t.startswith(gcn_title)), None)
    if gcn_index is not None:
        id_to_title = {}
        with (CSV_DIR / "papers.csv").open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                id_to_title[row["id"]] = row["title"]

        sims = embeddings @ embeddings[gcn_index]
        order = np.argsort(-sims)
        print(f"\n5 nearest neighbours of {gcn_title!r}:")
        shown = 0
        for i in order:
            if i == gcn_index:
                continue
            print(f"  {sims[i]:.4f}  {id_to_title.get(ids[i], ids[i])}")
            shown += 1
            if shown == 5:
                break
    else:
        print("\n(GCN paper not found in this seed set; skipping sanity check)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
