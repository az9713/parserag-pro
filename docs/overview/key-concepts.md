# Key Concepts

Definitions for every term used in the ParseRAG docs. Start here if you are new to any of the technologies.

---

## Embedding

A way of converting text into a list of numbers so that a computer can measure how similar two pieces of text are.

Example: the sentence "Warfarin is a blood thinner" becomes an array of 3072 decimal numbers like `[0.021, -0.134, 0.882, ...]`. The sentence "anticoagulant medication" produces a *different* array, but one that is mathematically close to the first — because the words carry similar meaning. Sentences about unrelated topics (e.g., "the weather today") produce arrays that are far away.

ParseRAG uses Google Gemini (`gemini-embedding-001`) to generate 3072-dimensional embeddings. The dimension count (3072) is how many numbers are in each array.

See [Milvus and Vector Search](../concepts/milvus-and-search.md) for how embeddings are used during search.

---

## Vector

A list of numbers that represents a piece of data. In ParseRAG, every text chunk from a document is converted into a vector (via embedding) before being stored in Milvus. A "3072-dimensional vector" is just an array of 3072 floating-point numbers.

---

## Vector database

A database optimized for storing and searching vectors. Unlike a regular database (which finds rows by exact field matches), a vector database finds rows whose vectors are *closest* to a query vector. This is called **similarity search** or **nearest-neighbor search**.

Milvus is the vector database used in ParseRAG. See [Milvus and Vector Search](../concepts/milvus-and-search.md).

---

## Cosine similarity

The metric used to measure how similar two vectors are. Two identical vectors have cosine similarity of 1.0; completely unrelated vectors score close to 0. ParseRAG searches for the chunks with the highest cosine similarity to a query embedding.

---

## Chunk

A portion of a document's text, usually a few paragraphs long. ParseRAG splits each PDF page into chunks of up to 4096 characters, splitting at paragraph boundaries. Each chunk gets its own embedding and is stored as one row in Milvus.

Chunking is necessary because embedding models have input size limits, and smaller chunks produce more precise search matches.

---

## LiteParse

The PDF parsing library used by ParseRAG. It extracts text while preserving layout — tables stay as tables, multi-column text is read in reading order (left-to-right, not top-to-bottom across columns). It also renders each page as a PNG screenshot.

Standard PDF text extraction ignores layout, which scrambles tables and columns. LiteParse uses spatial grid projection to preserve structure.

---

## Milvus / Milvus Lite

**Milvus** is an open-source vector database. **Milvus Lite** is a stripped-down version that runs as a Python library with no server to install — it stores everything in a single `.db` file on your disk. ParseRAG uses Milvus Lite by default (`./milvus_lite.db`).

The same `pymilvus` API works with Milvus Lite (local), Milvus Standalone (Docker), and Zilliz Cloud (managed) — so you can graduate to larger deployments without changing code.

See [Milvus and Vector Search](../concepts/milvus-and-search.md).

---

## FastAPI

A Python web framework for building HTTP APIs. FastAPI handles incoming HTTP requests (from the browser or CLI), validates inputs, calls the appropriate logic, and returns responses.

In ParseRAG, FastAPI:
- Serves the HTML page at `/`
- Provides REST endpoints for processing PDFs (`/api/process`, `/api/index`)
- Streams agent execution events to the browser (`/api/query`)
- Serves page screenshots (`/api/screenshot/{path}`)

See [FastAPI and SSE Streaming](../concepts/fastapi-and-sse.md).

---

## SSE (Server-Sent Events)

A protocol where the server pushes data to the browser over a single, long-lived HTTP connection. The browser opens the connection once; the server sends lines of text whenever it has something new to say.

Each SSE message looks like:
```
data: {"type": "tool_call", "tool": "search", "step": 1}\n\n
```

The browser's JavaScript reads this stream and updates the UI in real time.

ParseRAG uses SSE (not WebSockets) for the `/api/query` endpoint. SSE is sufficient because only the server needs to push data to the browser — the browser doesn't need to send messages while the stream is open. See [FastAPI and SSE Streaming](../concepts/fastapi-and-sse.md).

---

## WebSocket

A two-way persistent communication channel between a browser and a server. Both sides can send messages at any time.

ParseRAG does **not** use WebSockets. The live update stream uses SSE instead, which is simpler and sufficient for a one-directional server-to-browser push.

---

## Anthropic SDK

The official Python library for the Claude API, published by Anthropic. In ParseRAG, it is imported as `anthropic` and used to send messages to Claude and receive responses.

The key classes used:
- `anthropic.Anthropic()` — synchronous client (used by the CLI)
- `anthropic.AsyncAnthropic()` — async client (used by the web server)

---

## Claude

The AI model made by Anthropic. In ParseRAG, Claude acts as an **agent** — it receives the user's question, decides which tools to call, reads the results, and writes the final answer. Claude Sonnet 4 is the default model (`claude-sonnet-4-20250514`).

---

## Tool use

A feature of Claude where the AI can call predefined functions (tools) as part of answering a question. Instead of answering from memory, Claude calls `search("Warfarin side effects")`, receives the results, and incorporates them into its answer.

ParseRAG gives Claude 8 tools: `search`, `get_image`, `scan_folder`, `preview_file`, `parse_file`, `read_file`, `grep`, and `glob`.

See [Claude Agent and Tool Use](../concepts/claude-agent.md).

---

## Agent loop

The iterative cycle in which Claude repeatedly calls tools until it has enough information to answer. Each iteration:
1. Claude receives the current conversation (question + all previous tool results).
2. Claude returns either more tool calls or a final text answer.
3. If tool calls: execute them, add results to the conversation, go to step 1.
4. If final answer: end the loop.

See [Claude Agent and Tool Use](../concepts/claude-agent.md).

---

## Async / asyncio

Python's built-in concurrency system. `async def` functions and `await` expressions allow the web server to handle multiple requests at the same time without blocking. When the server is waiting for Claude's response (which takes seconds), it can simultaneously serve other requests.

ParseRAG uses `asyncio.to_thread()` to run synchronous code (like Milvus queries and LiteParse parsing) in a background thread without blocking the async event loop.

---

## System prompt

A set of instructions sent to Claude before any user message. It shapes Claude's behavior for the entire session. ParseRAG's system prompt (in `src/agent.py`) tells Claude how to use its tools, how to cite sources, and how to structure its final answer.

---

## Collection (Milvus)

The Milvus equivalent of a database table. ParseRAG uses a single collection named `parserag`. Each row in the collection stores one text chunk with its embedding vector, source filename, page number, and screenshot path.

---

## COSINE index / FLAT index

Milvus index types. ParseRAG uses a `FLAT` index with `COSINE` metric. FLAT does an exhaustive scan of all vectors for each query — accurate but linear in time. For the document counts ParseRAG targets, this is fast enough. Larger deployments would use IVF_FLAT or HNSW for sub-linear search.

---

## Ingestion pipeline

The process that converts a raw PDF into searchable data in Milvus: parse the PDF → split text into chunks → generate embeddings → store chunks + embeddings in Milvus. Also generates page screenshots alongside the text.

See [Ingestion Pipeline](../concepts/ingestion-pipeline.md).

---

## Three-phase exploration strategy

When searching documents that have not been indexed in Milvus, Claude follows a structured approach:
1. **Scan** — use `scan_folder` to preview all documents at once and classify each as RELEVANT, MAYBE, or SKIP.
2. **Deep dive** — use `parse_file` on RELEVANT documents.
3. **Backtrack** — if a document references another that was skipped, go back and parse the referenced document.

See [Claude Agent and Tool Use](../concepts/claude-agent.md).
