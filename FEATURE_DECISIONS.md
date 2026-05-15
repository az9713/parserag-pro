# ParseRAG — Feature Decisions

This document records the five proposed features from `FEATURES.md`, every design decision made before implementation, and the pros and cons of each choice. Read alongside `DECISIONS.md` (which covers the five improvements).

---

## Feature 1 — Multi-turn Conversation with Session Memory

### Problem

Every query to `run_agent_stream` starts from a blank slate. A follow-up like "What about the dosage for that medication?" triggers a full re-search rather than building on what the agent already retrieved. This makes the UI feel like a search engine rather than a conversation.

### Decision

Store conversation state server-side in a **SQLite database** keyed by a **server-generated UUID session ID**.

**Session lifecycle:**
1. First query arrives with no `session_id` → server creates a new UUID, initialises a row in SQLite with the `messages` list, returns the UUID in the SSE `start` event.
2. Client stores the UUID in a JS variable and sends it on every subsequent request via `QueryRequest.session_id`.
3. Each response appends the new assistant turn to the stored `messages` list.
4. A "New Session" button in the UI clears the JS variable, causing the next query to start a fresh session.
5. Sessions expire after **30 minutes of inactivity** — a background cleanup pass removes stale rows.

The `messages` list (the raw Claude API message history) is serialised as JSON and stored in a single `TEXT` column. No ORM or migration tooling required.

### New files / changes
- `src/sessions.py` — `SessionStore` class wrapping SQLite (`sessions.db`)
- `src/agent.py` — `run_agent_stream` accepts `session_id: str | None`
- `app.py` — `QueryRequest.session_id`, session cleanup background task
- `static/index.html` — session ID state, "New Session" button

### Pros
- Conversation context carries over between queries — follow-up questions work naturally
- SQLite persists sessions across server restarts; a restart mid-conversation does not lose history
- No new runtime dependency — SQLite is part of the Python standard library
- Session isolation: multiple browser tabs get independent sessions
- 30-minute TTL prevents unbounded growth

### Cons / Trade-offs
- The `messages` list grows with every turn. A long session accumulates large context, eventually hitting Claude's context window limit. Mitigation: truncate messages beyond a configurable limit (keep the last N turns), or summarise older turns.
- Storing raw `messages` JSON in SQLite is simple but not queryable — you cannot search across sessions or replay a specific turn without deserialising.
- The 30-minute TTL is a server-side heuristic. A user who leaves the tab open for 35 minutes and comes back will silently start a new session. The UI should show session age or a warning.
- Session IDs are not authenticated — anyone who intercepts the UUID can read or continue the session. Acceptable for a local single-user tool; not acceptable for a networked multi-user deployment.

---

## Feature 2 — Structured Table Extraction Tool (`extract_table`)

### Problem

The agent currently receives tables as raw text — column alignment is approximated by spaces and can be garbled. A question like "Give me the drug interaction table as CSV" or "How many interactions are listed?" cannot be answered reliably from text layout alone. LiteParse was chosen specifically for its layout-awareness, but the agent never uses structured output.

### Finding

Inspection of the LiteParse API confirms that `ParsedPage` has **no native table field**. It exposes `textItems: List[TextItem]` — individual text fragments each with `x, y, width, height` pixel coordinates — but no table structure. Spatial reconstruction from coordinates is fragile for merged cells and multi-line rows.

### Decision

Add **`pdfplumber`** as a new dependency and implement `extract_table` using its native table-detection engine. `pdfplumber` analyses PDF vector geometry to identify table borders and cell boundaries, producing a clean `list[list[str | None]]` per table.

**Tool interface:**
```python
extract_table(file_path: str, page_num: int | None = None) -> str
```
Returns a JSON string:
```json
{
  "file": "report.pdf",
  "page": 3,
  "tables": [
    [["Drug", "Interaction", "Severity"], ["Warfarin", "Aspirin", "High"], ...]
  ]
}
```
If `page_num` is omitted, all pages are scanned and all tables returned.

### New files / changes
- `requirements.txt` — add `pdfplumber>=0.11`
- `src/fs.py` — `extract_table_from_pdf()` helper using pdfplumber
- `src/agent.py` — new `extract_table` tool definition + handler in `handle_tool_call`
- System prompt updated to describe when to use `extract_table`

### Pros
- Returns genuinely structured data — downstream aggregation, CSV conversion, and cell-level queries all become possible
- `pdfplumber` handles complex layouts (borders, shading, multi-line cells) more reliably than spatial heuristics
- Pure Python — `pip install pdfplumber` is sufficient, no system dependencies
- JSON output means the agent can reason over the structure in a tool result, not just read text

### Cons / Trade-offs
- New dependency (`pdfplumber`). It uses `pdfminer.six` internally, adding ~2 MB of package weight.
- `pdfplumber` and `LiteParse` both parse the same PDF file but through different engines. For a single `extract_table` call, pdfplumber opens the file independently. This is redundant work but unavoidable given LiteParse's lack of table output.
- Table detection is heuristic — borderless tables (text aligned by spaces with no visible grid lines) may not be detected or may be detected incorrectly. The agent should fall back to `parse_file` when `extract_table` returns no tables.
- `pdfplumber` is slower than LiteParse's text extraction for large documents. For a 200-page PDF with `page_num=None`, the call could take several seconds.

---

## Feature 3 — Real-time Ingestion Progress with Streaming UI

### Problem

Indexing a directory of PDFs is a long-running, multi-step operation (parse → chunk → embed → store per file). The current UI shows only an indeterminate spinner for the entire duration. A user indexing 16 documents has no idea which file is being processed, whether anything has succeeded, or how much is left.

### Decision

Add a new **`/api/index/stream` SSE endpoint** that emits per-file progress events as the pipeline runs. The existing `/api/index` JSON endpoint is unchanged (CLI and script users continue to use it). The "Index All Documents" button in the UI switches to the streaming endpoint.

**Progress event types emitted:**
```json
{"type": "index_start",  "total": 16}
{"type": "file_start",   "file": "report.pdf", "index": 1, "total": 16}
{"type": "file_stage",   "file": "report.pdf", "stage": "parsing"}
{"type": "file_stage",   "file": "report.pdf", "stage": "chunking"}
{"type": "file_stage",   "file": "report.pdf", "stage": "embedding"}
{"type": "file_stage",   "file": "report.pdf", "stage": "storing"}
{"type": "file_done",    "file": "report.pdf", "chunks": 42}
{"type": "file_error",   "file": "corrupt.pdf", "error": "..."}
{"type": "index_done",   "files_processed": 15, "files_failed": 1, "chunks": 630}
```

**Implementation approach:** `pipeline_directory` gains an optional `progress_callback: Callable[[dict], None] | None` parameter. Each stage calls it with an event dict. The SSE endpoint wraps the callback in a thread-safe queue — `pipeline_directory` runs in `asyncio.to_thread` (blocking), puts events in the queue, and the async generator reads from the queue and yields SSE frames.

### New files / changes
- `src/processing.py` — `progress_callback` parameter on `pipeline` and `pipeline_directory`
- `app.py` — new `/api/index/stream` SSE route
- `static/index.html` — `indexAll()` switches to SSE reader; per-file status cards added to execution log

### Pros
- Users see exactly which file is being processed and at which stage — no more blind waiting
- Failures are visible immediately (the card turns red) rather than only at the end
- The streaming mechanism reuses the existing SSE infrastructure already used by `/api/query` — no new technology introduced
- The existing `/api/index` JSON endpoint is untouched — CLI tools and scripts are unaffected

### Cons / Trade-offs
- `pipeline_directory` gains a callback parameter — existing callers that pass positional arguments may need updating (mitigated by making `progress_callback` keyword-only with a default of `None`).
- The queue-based bridge between the blocking `pipeline_directory` thread and the async SSE generator adds a small amount of complexity. If the SSE client disconnects mid-index, the pipeline continues running in its thread until the current file finishes — there is no mid-file cancellation.
- Embedding calls (the slowest stage) are batched — the `embedding` stage card will appear to hang for several seconds per file while the Gemini API processes each batch. This is accurate but may look like a stall to the user. Adding estimated-time information would help but is out of scope for this feature.

---

## Feature 4 — Cross-Document Reference Graph

### Problem

When the agent follows cross-references during Phase 3 backtracking it discovers which documents are related. That knowledge is ephemeral — it dies with the query. Over many queries, this is a rich map of the corpus that is being thrown away. Persisting it as a graph would make the collection's structure visible and explorable.

### Finding

**Option A** (agent calls a `record_reference` tool) would capture genuine directional citations — A explicitly references B — but requires trusting the model to call the tool reliably every time it follows a reference. Model compliance is unpredictable and the system prompt would need to mandate the behaviour, adding prompt complexity.

**Option B** (post-answer co-citation parsing) is mechanical and deterministic: after every answer is generated, parse `[Source: filename | Page N | path]` tags from the response text and draw an undirected edge between every pair of documents that co-appeared. This does not require model cooperation.

### Decision

**Option B — co-citation parsing** stored in **SQLite**, visualised with **Cytoscape.js**.

**Graph schema (SQLite `graph.db`):**
```sql
CREATE TABLE edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    weight INTEGER DEFAULT 1,
    last_seen TEXT,
    PRIMARY KEY (source, target)
);
```
Each time source A and target B co-appear in an answer, `weight` is incremented (or the row is inserted). Edge weight reflects how often two documents are retrieved together, which is a useful proxy for topical proximity even without true directional citation data.

**New endpoint:** `GET /api/graph` returns:
```json
{
  "nodes": [{"id": "report.pdf"}, ...],
  "edges": [{"source": "report.pdf", "target": "guidelines.pdf", "weight": 5}, ...]
}
```

**UI:** A new "Document Graph" panel below the main grid renders the graph using **Cytoscape.js loaded from CDN**. Clicking a node pre-fills the query input with `"Summarise [filename]"` and focuses the query box — the user presses Enter to run it. Edge thickness is proportional to co-citation weight.

### New files / changes
- `src/graph.py` — `GraphStore` class, `record_co_citations(docs: list[str])`, `get_graph() -> dict`
- `src/agent.py` — call `record_co_citations` after each `answer` event is built
- `app.py` — `GET /api/graph` route
- `static/index.html` — Cytoscape.js CDN script, graph panel, node-click handler

### Pros
- Purely mechanical — no model compliance required, no system prompt changes
- Incrementally useful from the first query; the graph grows richer with each session
- Co-citation weight is a meaningful signal for a retrieval system: high-weight edges reliably identify documents that are retrieved together
- SQLite requires no new runtime dependency
- Cytoscape.js is loaded from CDN — no build tooling or bundling required

### Cons / Trade-offs
- Co-citation ≠ cross-reference. Two documents retrieved in the same answer because they share a topic are not the same as one document explicitly citing another. The graph represents retrieval co-occurrence, not document provenance. This should be clearly labelled in the UI ("Documents retrieved together").
- Edges are undirected and aggregated across all sessions. There is no way to ask "which queries produced this edge?" without a more complex schema.
- If a query returns only one source document, no edge is recorded — single-document answers contribute nothing to the graph.
- Cytoscape.js (CDN) requires internet access. An air-gapped deployment would need a locally bundled copy.
- The graph panel adds visual weight to an already-dense UI. On small screens it may need to be collapsible or moved to a separate page.

---

## Feature 5 — Answer Export as Formatted Report

### Problem

Useful answers — with inline citations, source document names, and page references — exist only in the browser tab. For medical, legal, or compliance use cases, the output needs to be shareable, printable, and auditable outside the app.

### Decision

A one-click **Export** button appears beneath the answer once it is rendered. It offers two formats:

**Markdown (`.md`):** Server-side generation. The raw answer text is cleaned up (citation tags converted to readable footnotes), then returned as a file download with `Content-Disposition: attachment; filename=report.md`.

**PDF (`.pdf`):** Server-side generation using **`fpdf2`** (pure Python, `pip install fpdf2`). The PDF includes:
- Title and timestamp header
- Answer text with inline citation references converted to footnote markers
- Embedded page screenshot thumbnails (fetched from disk) with captions showing filename, page number, and path
- A "Sources" section listing all cited documents

**Export API:**
```
POST /api/export
{
  "format": "md" | "pdf",
  "content": "<raw answer text with [Source: ...] tags>",
  "session_id": "<optional, for metadata>"
}
→ Returns file download
```

The button in the UI collects `resultContent`'s raw text (stored in a JS variable after the answer event) and POSTs it to `/api/export`.

### New files / changes
- `requirements.txt` — add `fpdf2>=2.8`
- `src/export.py` — `to_markdown(text) -> str`, `to_pdf(text) -> bytes`
- `app.py` — `POST /api/export` route
- `static/index.html` — Export button (hidden until answer received), raw answer stored in JS variable

### Pros
- Markdown export has zero new dependencies and is trivially fast
- `fpdf2` is pure Python — `pip install fpdf2` only, no system tools required (no LibreOffice, Ghostscript, or wkhtmltopdf)
- PDF includes embedded screenshots — the exported report is self-contained and can be shared without access to the server
- A proper file download (not a browser print dialog) — the user gets a named file immediately
- The export endpoint is stateless — it receives the content to export and returns the file; no session lookup required

### Cons / Trade-offs
- New dependency (`fpdf2`). Lightweight (~1 MB) but it is another package to maintain.
- Citation tag parsing (`[Source: filename | Page N | path]`) must be done in server-side Python using a regex. If the model emits a malformed citation, that citation is silently dropped from the report rather than causing an error.
- Screenshot embedding in PDF requires fetching image files from disk at export time. If the screenshots directory has been deleted or the paths changed since indexing, those images are omitted from the PDF with a placeholder.
- `fpdf2` has limited font and layout control. The PDF will be clean and readable but not typographically rich. For a professionally designed report, a more capable library (WeasyPrint + CSS) would be needed — but WeasyPrint has significant system dependencies on Windows.
- The export captures the answer text as a plain string. Markdown formatting (`**bold**`, bullets) is stripped in the PDF (text is rendered literally). Full Markdown-to-PDF rendering would require an intermediate HTML step outside `fpdf2`'s scope.

---

## Summary Table

| # | Feature | Key Decisions | New Dependencies |
|---|---|---|---|
| 1 | Session Memory | SQLite storage, server-generated UUID, 30-min TTL | None (sqlite3 is stdlib) |
| 2 | Table Extraction | `pdfplumber` for detection, JSON output, `page_num` optional | `pdfplumber>=0.11` |
| 3 | Streaming Ingestion | New `/api/index/stream` SSE endpoint, callback-based pipeline | None |
| 4 | Reference Graph | Co-citation parsing, SQLite edges, Cytoscape.js via CDN | None (CDN for JS) |
| 5 | Export Report | `fpdf2` for PDF, Markdown trivial, `/api/export` endpoint | `fpdf2>=2.8` |
