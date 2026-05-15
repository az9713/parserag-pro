# Milvus and vector search

Milvus is the database that stores parsed document text and finds relevant chunks when a question is asked. This document explains what makes it different from a regular database, how ParseRAG uses it, and how to interpret search results.

---

## Why a vector database instead of a regular one

A traditional database finds rows by exact value: `WHERE medication_name = 'Warfarin'`. This fails if the document uses different words for the same concept — "anticoagulant therapy" wouldn't match.

A vector database finds rows by **similarity of meaning**. You convert your query into a vector (an array of numbers), and the database finds stored vectors that are mathematically close to it. Text that expresses similar ideas produces vectors that are numerically close, even if the words don't overlap.

This is why you can ask "What medications thin the blood?" and find text that says "anticoagulants reduce clotting factors" — the concepts are similar, so the vectors are similar.

---

## How vectors encode meaning

Consider these two sentences:
- "Warfarin is a blood thinner used to prevent clots."
- "Anticoagulants inhibit the coagulation cascade."

A human knows these are about the same topic. A keyword search sees zero shared words and returns no match. An embedding model converts each sentence into a 3072-dimensional vector. The two vectors will be numerically close because the model learned, during training, that these concepts relate.

The mathematical measure is **cosine similarity** — the angle between two vectors in 3072-dimensional space. Identical vectors have cosine similarity of 1.0. Unrelated concepts score near 0.

---

## Milvus Lite vs full Milvus

ParseRAG uses **Milvus Lite** by default. It runs as a Python library — no server process, no Docker, no network configuration. All data is stored in a single file:

```python
client = MilvusClient(uri="./milvus_lite.db")
```

The same `pymilvus` API works across all deployment tiers:

| Tier | Connection string | Good for |
|------|-----------------|----------|
| Milvus Lite | `./milvus_lite.db` | Development, small collections |
| Milvus Standalone | `http://localhost:19530` | Production on a single server |
| Zilliz Cloud | `https://your-cluster.zillizcloud.com` | Managed cloud, large scale |

You can move from Lite to a larger deployment by changing one line (`uri`) with no other code changes.

---

## The collection schema

ParseRAG uses one Milvus collection named `parserag`. Its schema:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | INT64 (auto) | Primary key, assigned by Milvus |
| `source_file` | VARCHAR(1024) | Filename of the source PDF |
| `text` | VARCHAR(65535) | The text chunk |
| `vector` | FLOAT_VECTOR[3072] | Embedding of the text chunk |
| `image_path` | VARCHAR(1024) | Path to the page screenshot PNG |
| `page_num` | INT64 | Page number this chunk came from |

The `vector` field is the indexed field — Milvus builds a search index over it. All other fields are returned as output fields when a search matches.

---

## How a search works step by step

```python
# src/search.py
def search(query: str, limit: int = 3) -> list[dict]:
    embedding = embed_query(query)        # 1. Query → vector
    client = get_milvus_client()

    results = client.search(              # 2. Vector → nearest neighbors
        collection_name=COLLECTION_NAME,
        data=[embedding],
        limit=limit,
        output_fields=["text", "image_path", "page_num", "source_file"],
        search_params={"metric_type": "COSINE"},
    )
    ...
```

**Step 1: Embed the query.** The query string is sent to Gemini's embedding model, which returns a 3072-float vector.

**Step 2: Nearest-neighbor search.** Milvus compares the query vector against every stored chunk vector using cosine similarity. It returns the top `limit` chunks with the highest similarity scores.

**Step 3: Return results.** Each result includes the chunk text, screenshot path, page number, source file, and the similarity `distance` (0.0–1.0, higher is more similar).

---

## What the agent does with search results

When Claude calls the `search` tool, `handle_tool_call` formats each result into a text block:

```
SOURCE: 01_NNMC_Medication_Side_Effects.pdf
PAGE SCREENSHOT PATH: screenshots/01_NNMC_.../page_1.png
PAGE: 1
SIMILARITY: 0.8742

CONTENT:
Warfarin (Coumadin) — Common side effects: unusual bleeding or bruising,
blood in urine or stool, prolonged bleeding from cuts...
```

Claude reads all returned blocks. If the text is enough, it writes the answer. If the text is ambiguous or involves a table, it may call `get_image` to view the screenshot directly.

---

## Understanding the similarity score

The `distance` field returned by Milvus is the cosine similarity between the query vector and the chunk vector:

| Score range | Interpretation |
|-------------|---------------|
| 0.85–1.00 | Strong match — the chunk is clearly relevant |
| 0.70–0.84 | Moderate match — likely relevant, may require context |
| 0.50–0.69 | Weak match — topic overlap but not a precise answer |
| Below 0.50 | Poor match — often off-topic |

These ranges are approximate and depend on the embedding model and document collection. The scores in `python main.py search <query>` CLI output show you what to expect for your documents.

---

## Index type

ParseRAG uses a `FLAT` index:

```python
index_params.add_index(
    field_name="vector",
    index_type="FLAT",
    metric_type="COSINE",
)
```

`FLAT` performs an exhaustive scan — it compares the query vector against every stored vector. This guarantees the best possible results but takes time proportional to the number of stored vectors.

For the document counts ParseRAG targets (hundreds to a few thousand chunks), FLAT is fast enough — typically under 100ms. For larger collections, Milvus supports approximate indexes (IVF_FLAT, HNSW) that trade a small accuracy loss for sub-linear query time.

---

## Managing the collection

```python
# Check if collection exists and how many chunks it has
client.has_collection(COLLECTION_NAME)
client.get_collection_stats(COLLECTION_NAME)  # returns {"row_count": N}

# Drop and recreate (used by python main.py index)
client.drop_collection(COLLECTION_NAME)

# The /api/status endpoint exposes chunk count to the UI
```

The `row_count` is what the status badge in the UI shows as "N chunks".

---

## Common issues

**"No results found"** — The collection is empty. Run `python main.py index data/documents` to index documents first.

**Very low similarity scores (< 0.4) on everything** — Your query might use vocabulary that doesn't appear in the documents, or the documents might not contain information related to your query. Try rephrasing the query to use words likely to appear in the documents.

**`MilvusException: collection not found`** — The collection was dropped (e.g., by a `--reset` index run) and not recreated. Run the ingestion pipeline again.
