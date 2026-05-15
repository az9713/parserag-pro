# What is ParseRAG?

ParseRAG is a system that lets you ask plain-language questions about PDF documents and get cited answers — even when those PDFs contain tables, multi-column layouts, or content spread across many files.

---

## The problem it solves

Searching PDFs with a keyword search misses context. If a document says "anticoagulant therapy" but you search for "blood thinner", you get nothing. And when content lives in a table with merged cells or a two-column layout, naive text extraction scrambles the order and makes the text unreadable.

ParseRAG solves both problems: it converts document text into mathematical vectors that capture *meaning* (so similar concepts find each other), and it uses a layout-aware parser that preserves table structure.

---

## The four moving parts

Before tracing a query end-to-end, here are the four components and what each one does:

| Component | Role |
|-----------|------|
| **LiteParse** | Reads PDFs and extracts text that respects tables and columns |
| **Milvus** | Stores document text as vectors and finds similar content by meaning |
| **Claude** | The AI agent that decides how to search, reads results, and writes the answer |
| **FastAPI** | The web server that connects the browser to all of the above and streams progress live |

---

## How a query flows end-to-end

When you type "What are the side effects of Warfarin?" and press Execute, here is every step that happens, in order.

### Step 1 — Browser sends the question to FastAPI

The browser calls `POST /api/query` with a JSON body:
```json
{ "question": "What are the side effects of Warfarin?", "folder": "data/documents" }
```
FastAPI receives this and opens a **streaming response** — a long-lived HTTP connection that it will push updates down as the agent works. You see these updates appear in the Execution Log in real time. (This protocol is called SSE — see the note below.)

> **SSE, not WebSocket.** The live update stream in ParseRAG uses Server-Sent Events (SSE), not WebSockets. Both give you real-time updates from the server, which is why people use the terms interchangeably. The difference: WebSocket is a two-way channel the browser and server both write to; SSE is one-way (server → browser only). ParseRAG only needs the server to push updates to the browser, so SSE is the simpler fit. See [FastAPI and SSE Streaming](../concepts/fastapi-and-sse.md) for the full explanation.

### Step 2 — FastAPI hands the question to the Claude agent

`run_agent_stream` (in `src/agent.py`) starts an **agent loop**. It sends the question to Claude via the Anthropic SDK along with a list of 8 tools Claude can call. Claude reads the question and decides what to do first.

### Step 3 — Claude calls a tool

Claude doesn't answer directly. It reasons: "I should search the indexed knowledge base first." It returns a **tool call**:
```
tool: search
input: { "query": "Warfarin side effects", "limit": 3 }
```
FastAPI streams a `tool_call` event to the browser, which appears as a card in the Execution Log.

### Step 4 — ParseRAG executes the search in Milvus

The `search` function in `src/search.py`:
1. Converts "Warfarin side effects" into a 3072-number vector using the Google Gemini embedding model.
2. Sends that vector to Milvus, which finds the stored document chunks with the closest vectors.
3. Returns the top 3 matching text chunks, each tagged with its source file, page number, and path to a page screenshot.

### Step 5 — Results go back to Claude

The tool results are appended to the conversation and sent back to Claude. Claude now reads: "Here are 3 text chunks about Warfarin side effects from the indexed documents."

### Step 6 — Claude may call more tools

If the first search result contains a table, Claude may call `get_image` to see the page screenshot directly — it can read images too. If results are incomplete, it may call `scan_folder` to look through unindexed documents. This loop continues until Claude has enough information.

### Step 7 — Claude writes the final answer

When Claude is satisfied, it returns a text answer with inline citations in the format `[Source: filename | Page N | path/to/screenshot.png]`. The agent loop ends (`stop_reason = "end_turn"`).

### Step 8 — FastAPI streams the final answer to the browser

An `answer` event is pushed down the SSE stream. The browser renders the answer text, converts citation tags into clickable buttons, and displays the stats bar (steps, tool calls, searches, images, filesystem explores).

### Step 9 — You click a citation

Clicking `01_NNMC_Medication_Side_Effects.pdf p.3` calls `GET /api/screenshot/screenshots/01_NNMC.../page_3.png`. FastAPI serves the PNG file. A modal opens showing the original PDF page.

---

## What ParseRAG is NOT

- **Not a general search engine.** It only searches documents you have indexed locally.
- **Not a WebSocket server.** Real-time streaming uses SSE (one-way push), which is simpler and sufficient.
- **Not a cloud service.** Everything runs locally. Milvus Lite stores data in a file on your machine.
- **Not a document editor.** It reads and answers; it does not modify documents.

---

## Learning path

1. Read [Key Concepts](key-concepts.md) — understand embeddings, SSE, tool use, and Milvus before going deeper.
2. Read [Ingestion Pipeline](../concepts/ingestion-pipeline.md) — understand how PDFs become searchable.
3. Read [Claude Agent and Tool Use](../concepts/claude-agent.md) — understand the agent loop in detail.
4. Read [FastAPI and SSE Streaming](../concepts/fastapi-and-sse.md) — understand how the server streams live updates.
5. Read [Milvus and Vector Search](../concepts/milvus-and-search.md) — understand similarity search.
6. Read [System Design](../architecture/system-design.md) — see the full architecture.
