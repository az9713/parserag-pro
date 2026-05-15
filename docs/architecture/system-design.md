# System design

Full architecture of ParseRAG: components, data flows, and key design decisions.

---

## High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Browser (index.html)                     │
│  ┌────────────┐  POST /api/query  ┌────────────────────────────┐ │
│  │  Query UI  │ ─────────────────→│    SSE event stream        │ │
│  │  (input)   │                   │  (Execution Log + Answer)  │ │
│  └────────────┘ ←─────────────── │                            │ │
└──────────────────────────────────┴────────────────────────────┴─┘
                                              │ HTTP
                                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI  (app.py)                         │
│                                                                  │
│  GET /           → static/index.html                            │
│  POST /api/query → run_agent_stream() → SSE stream              │
│  POST /api/index → asyncio.to_thread(pipeline_directory())      │
│  GET /api/screenshot/{path} → serve PNG from disk               │
└──────────────┬──────────────────────────────────────────────────┘
               │ async generator events
               ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Claude Agent  (src/agent.py)                  │
│                                                                  │
│  run_agent_stream()                                              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  while True:                                              │   │
│  │    response = await claude.messages.create(...)           │   │
│  │    if stop_reason == "end_turn": yield answer; return     │   │
│  │    for tool_call in response:                             │   │
│  │      result = await asyncio.to_thread(handle_tool_call)  │   │
│  │      yield tool_call event                               │   │
│  │    append tool results to messages                        │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────┬──────────────────────┬──────────────────────────────────┘
       │                      │
       │ Anthropic API        │ Tool calls
       ↓                      ↓
┌─────────────┐   ┌───────────────────────────────────────────┐
│ Claude      │   │  Tool Handlers  (src/search.py, src/fs.py) │
│ (claude-    │   │                                             │
│ sonnet-4)   │   │  search()   → Gemini embed → Milvus query  │
│             │   │  get_image() → read PNG → base64           │
│             │   │  scan_folder() → parallel LiteParse        │
│             │   │  parse_file() → LiteParse full parse       │
│             │   │  grep() / glob() / read_file()             │
└─────────────┘   └──────────┬─────────────────────┬──────────┘
                             │                     │
                             ↓                     ↓
                  ┌──────────────────┐   ┌──────────────────┐
                  │  Milvus Lite     │   │  Filesystem       │
                  │  (milvus_lite.db)│   │  (data/, pdfs)   │
                  └──────────────────┘   └──────────────────┘

                  ┌──────────────────────────────────────────┐
                  │         Ingestion pipeline  (offline)     │
                  │                                           │
                  │  PDF → LiteParse → chunks → Gemini embed │
                  │     → MilvusClient.insert()               │
                  │  PDF → LiteParse.screenshot() → PNGs     │
                  └──────────────────────────────────────────┘
```

---

## Component breakdown

### Browser (`static/index.html`)

A single HTML file — no build step, no framework. Vanilla JavaScript handles:
- POST to `/api/query` and reading the SSE response body with a `ReadableStream`
- Parsing `data: {...}\n\n` lines and dispatching to `handleEvent()`
- Rendering tool call cards and the final answer with clickable citations
- POST to `/api/index` for batch document indexing

### FastAPI (`app.py`)

The HTTP layer. Key responsibilities:
- Serve static files from `static/`
- Validate request bodies via Pydantic models (`QueryRequest`, `ProcessRequest`, etc.)
- Bridge between async HTTP and synchronous processing code via `asyncio.to_thread()`
- Stream agent events via `StreamingResponse` with `media_type="text/event-stream"`
- Enforce a path-prefix check on screenshot serving to prevent directory traversal

### Claude agent (`src/agent.py`)

The reasoning engine. Manages the Claude API conversation, dispatches tool calls, and yields structured events for the SSE stream. Contains:
- `SYSTEM_PROMPT` — instructions for Claude on tool use, citation format, and answer structure
- `TOOLS` — JSON schema definitions for all 8 tools
- `handle_tool_call()` — dispatcher from tool name to Python function
- `run_agent()` — synchronous version for CLI use
- `run_agent_stream()` — async generator version for the web server

### Vector search (`src/search.py`)

Thin wrapper over Milvus and Gemini:
- `embed_query()` — call Gemini to embed a single query string
- `search()` — embed query, run Milvus cosine similarity search, return results
- `get_image_base64()` — read a PNG file from disk and return base64-encoded bytes

### Filesystem tools (`src/fs.py`)

Six functions that let Claude explore unindexed documents:
- `scan_folder()` — parallel preview of all documents in a folder using `ThreadPoolExecutor(max_workers=4)`
- `preview_file()` — first 3000 characters of a single document
- `parse_file_content()` — full document content (calls LiteParse for PDFs)
- `read_text_file()` — raw read for non-PDF text files
- `grep_in_file()` — regex search with line numbers and 1-line context
- `glob_search()` — recursive file pattern matching

All PDF parsing results are cached in memory keyed by `{abs_path}:{mtime}` to avoid re-parsing the same file within a session.

### Ingestion pipeline (`src/processing.py`)

Offline batch processing. Converts PDFs into Milvus rows:
- `parse_pdf()` — extract layout-aware text per page via LiteParse
- `take_screenshots()` — render page PNGs via LiteParse
- `chunk_text()` — split page text into ≤4096-character chunks at paragraph boundaries
- `embed_texts()` — batch embed chunks via Gemini (batches of 5)
- `insert_data()` — write chunks + embeddings into Milvus
- `pipeline()` — orchestrates all steps for one PDF
- `pipeline_directory()` — run `pipeline()` for every PDF in a directory

### External services

| Service | Used by | Purpose |
|---------|---------|---------|
| Anthropic API | `src/agent.py` | Claude inference for reasoning and tool-use decisions |
| Google Gemini API | `src/processing.py`, `src/search.py` | Text embeddings (model: `gemini-embedding-001`, dimensions: 3072) |

---

## Data flows

### Ingestion (offline)

```
PDF file
  → LiteParse.parse()         → {page_num: text}
  → LiteParse.screenshot()    → {page_num: "screenshots/.../page_N.png"}
  → chunk_text()              → ["chunk1", "chunk2", ...]
  → Gemini embed_content()    → [[float x 3072], ...]
  → MilvusClient.insert()     → rows in "parserag" collection
```

### Query (online, SSE stream)

```
Browser POST /api/query
  → FastAPI StreamingResponse
      → run_agent_stream() [async generator]
          → yield {"type": "start"}
          → claude.messages.create()
              → Claude returns tool_use blocks
          → for each tool_use:
              → asyncio.to_thread(handle_tool_call())
                  search: embed_query() → MilvusClient.search()
                  get_image: read PNG → base64
                  filesystem tools: LiteParse / os / re
              → yield {"type": "tool_call", ...}
              → append result to messages
          → claude.messages.create() [next iteration]
          ...
          → yield {"type": "answer", ...}
  → Browser reads SSE stream, renders cards + answer
```

---

## Key design decisions

### SSE instead of WebSocket

The agent-to-browser communication is strictly one-directional: the server pushes progress events; the browser only reads them. SSE is sufficient and simpler to implement in FastAPI (`StreamingResponse`) and to consume in the browser (fetch + ReadableStream). WebSocket would add bidirectional complexity with no benefit here.

### Dual retrieval: indexed + filesystem

Many RAG systems only search an index. ParseRAG also lets Claude read the filesystem directly. This covers two cases: (1) documents not yet indexed, and (2) queries that need cross-document context that vector search misses. The agent decides which mode to use per query.

### Milvus Lite for local development

Milvus Lite runs as a Python package with no separate process. The same `pymilvus` API works unchanged against full Milvus and Zilliz Cloud. This lets the project work with zero infrastructure while preserving a clear upgrade path.

### Synchronous tool execution inside async FastAPI

`handle_tool_call` is synchronous because Milvus and LiteParse are blocking libraries. Using `asyncio.to_thread()` runs these in the thread pool without blocking the async event loop. The alternative (making the tool handlers truly async) would require async-compatible wrappers for both libraries, adding complexity for unclear gain at this scale.

### Page screenshots as parallel evidence

Every indexed chunk stores the path to its page screenshot. When Claude retrieves a chunk, it can immediately view the original page layout by calling `get_image`. This is valuable for tables: the extracted text may lose column alignment, but the screenshot preserves it perfectly.

---

## Scaling considerations

| Concern | Current behavior | How to scale |
|---------|-----------------|-------------|
| Milvus storage | Milvus Lite (local `.db` file) | Move to Milvus Standalone or Zilliz Cloud |
| Search performance | FLAT index (exhaustive scan) | Switch to IVF_FLAT or HNSW for larger collections |
| Embedding throughput | Sequential Gemini calls, batch size 5 | Increase batch size; add retry/backoff for rate limits |
| Concurrent queries | Single FastAPI process | Deploy with multiple uvicorn workers or behind a load balancer |
| Agent loop safety | No iteration limit | Add a `max_tool_calls` guard to `run_agent_stream` |
