# Ingestion pipeline

The ingestion pipeline converts raw PDF files into searchable data in Milvus. It runs before any query — you must index documents first. The pipeline is defined in `src/processing.py`.

---

## What it produces

For each PDF, the pipeline produces:
- **Text chunks** in the Milvus collection — each chunk is a few paragraphs of extracted text tagged with its source file and page number.
- **PNG screenshots** on disk at `screenshots/{pdf_name}/page_N.png` — one image per page, used for visual citation when the agent calls `get_image`.

---

## The five steps

### Step 1: Parse the PDF with LiteParse

```python
pages = parse_pdf(file_path)
# Returns: {1: "page 1 text", 2: "page 2 text", ...}
```

LiteParse reads the PDF and returns a dictionary mapping page numbers to extracted text. It uses **spatial grid projection** to handle multi-column layouts and tables — characters are placed on a grid according to their coordinates on the page, then read left-to-right. This preserves table columns and reading order that standard PDF parsers scramble.

Simultaneously, LiteParse renders each page as a PNG:
```python
screenshot_paths = take_screenshots(file_path, screenshot_dir)
# Returns: {1: "screenshots/myfile/page_1.png", ...}
```

### Step 2: Split text into chunks

```python
chunks = chunk_text(page_text, chunk_size=4096)
```

Each page's text is split into chunks of up to 4096 characters, splitting at paragraph (`\n\n`) boundaries. This ensures no chunk ends mid-sentence.

Why chunk at all? Embedding models have a maximum input length. Smaller chunks also produce more precise search matches — a 300-word chunk about Warfarin dosage matches a query about Warfarin dosage better than a 3000-word chunk that mentions Warfarin once.

Each chunk remembers which page it came from (for the citation) and which screenshot corresponds to that page.

### Step 3: Generate embeddings

```python
embeddings = embed_texts(chunks)
# Returns: [[0.021, -0.134, ...], [0.044, 0.211, ...], ...]
```

The Gemini embedding model (`gemini-embedding-001`) converts each chunk's text into a 3072-dimensional vector. These vectors are sent in batches of 5 to stay within API rate limits.

The resulting list has one vector per chunk. Vector position 0 corresponds to chunk position 0.

### Step 4: Store in Milvus

```python
insert_data(client, source_file, chunks, embeddings, image_paths, page_nums)
```

Each chunk is inserted as one row in the `parserag` Milvus collection:

| Field | Value |
|-------|-------|
| `source_file` | `"01_NNMC_Medication_Side_Effects.pdf"` |
| `text` | The raw chunk text |
| `vector` | `[0.021, -0.134, ...]` (3072 floats) |
| `image_path` | `"screenshots/01_NNMC_.../page_1.png"` |
| `page_num` | `1` |

The `id` field is assigned automatically by Milvus.

### Step 5: Milvus builds the index

After insert, Milvus indexes the new vectors so they can be searched. ParseRAG uses a `FLAT` index with `COSINE` metric — all vectors are searched exhaustively on each query. For typical document collections (hundreds to low thousands of chunks), this is fast enough.

---

## Running the pipeline

```bash
# Single PDF
python main.py process data/Medication_Side_Effect_Flyer.pdf

# All PDFs in a directory (resets the collection first by default)
python main.py index data/documents
```

The batch command (`index`) processes PDFs one at a time and accumulates all chunks into the same collection. A progress log shows each file and chunk count.

---

## What is NOT in the pipeline

The `chonkie[semantic]` package appears in `requirements.txt` but is not used. Text chunking is handled by the hand-rolled `chunk_text` function in `processing.py`, which splits on paragraph boundaries at a fixed character limit.

---

## Key data flows

```
PDF file
  │
  ├── LiteParse.parse() ──────────────────────────→ {page_num: text, ...}
  │                                                          │
  ├── LiteParse.screenshot() ──→ screenshots/name/page_N.png │
  │                                                          ↓
  │                                              chunk_text() per page
  │                                                          │
  │                                                          ↓
  │                                          Gemini embed_content()
  │                                                          │
  │                                                          ↓
  └─────────────────────────────────────── MilvusClient.insert()
```

---

## Common issues

**"No text extracted from page N"** — LiteParse couldn't extract text from a scanned or image-only page. The chunk will contain the empty-page placeholder text `"empty page"` and contribute a near-zero embedding. This chunk will rarely surface in search results.

**Embedding API rate limits** — chunks are sent to Gemini in batches of 5. If you see `ResourceExhausted` errors, the Gemini API is rate-limiting. The code does not retry; re-run the failed file.

**Collection already exists** — by default, `python main.py index` drops and recreates the collection. Use `--no-reset` to append new documents to an existing collection instead.
