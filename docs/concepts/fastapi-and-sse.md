# FastAPI and SSE streaming

FastAPI is the web framework that connects the browser to all of ParseRAG's subsystems. The most interesting part is how it streams real-time updates to the browser while the Claude agent is working — using a protocol called Server-Sent Events (SSE).

---

## What FastAPI does in ParseRAG

FastAPI (`app.py`) handles five responsibilities:

1. **Serves the UI** — `GET /` returns the single HTML file at `static/index.html`.
2. **Triggers PDF processing** — `POST /api/process` and `POST /api/index` run the ingestion pipeline in a background thread.
3. **Streams agent execution** — `POST /api/query` opens a streaming connection and pushes events as the agent works.
4. **Lists folders** — `GET /api/folders` lets the browser browse directory structure.
5. **Serves screenshots** — `GET /api/screenshot/{path}` sends PNG files from the `screenshots/` directory.

---

## SSE explained from scratch

When you press Execute, the browser needs to show you progress in real time: "Claude is thinking... calling search... found 3 results..." These updates arrive one by one over several seconds while Claude works.

The naive approach would be **polling** — the browser asks "are you done yet?" every second. That wastes bandwidth and adds 1-second delays.

A better approach is **Server-Sent Events (SSE)**. The browser makes one HTTP request and keeps the connection open. The server sends lines of text whenever it has news. When it's done, it sends a final termination signal.

Each SSE message is a line starting with `data: ` followed by JSON, then two newlines:

```
data: {"type": "start", "query": "What are the side effects of Warfarin?"}\n\n
data: {"type": "tool_call", "step": 1, "tool": "search", "input": {...}}\n\n
data: {"type": "thinking", "step": 2, "content": "I found 3 relevant chunks..."}\n\n
data: {"type": "answer", "content": "Warfarin side effects include..."}\n\n
data: [DONE]\n\n
```

The browser reads these lines and updates the UI as each one arrives.

### SSE vs WebSocket

People often use these terms interchangeably when they mean "live updates from the server." They work differently:

| | SSE | WebSocket |
|---|---|---|
| Direction | Server → browser only | Both ways |
| Transport | Plain HTTP | Upgraded TCP connection |
| Browser support | `EventSource` API or `fetch` | `WebSocket` API |
| Good for | Server pushing status updates | Chat, games, collaborative editing |

ParseRAG only needs the server to push updates to the browser — the browser doesn't need to send messages while the stream is open. SSE is the simpler fit.

---

## How the streaming endpoint works

```python
# app.py
@app.post("/api/query")
async def query_agent(req: QueryRequest):
    async def event_stream():
        async for event in run_agent_stream(req.question, folder=folder):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

Breaking this down:

1. `run_agent_stream` is an **async generator** — a function that produces values one at a time using `yield`. Each value is a dict event: `{"type": "tool_call", ...}`.
2. `event_stream()` wraps each event dict into a properly formatted SSE line: `data: {...}\n\n`.
3. `StreamingResponse` tells FastAPI to send the response body incrementally as the generator yields, rather than buffering everything and sending at once.
4. `media_type="text/event-stream"` tells the browser this is an SSE stream.
5. `X-Accel-Buffering: no` prevents nginx (if present) from buffering the response, which would delay the live updates.

---

## How the browser reads the stream

The browser uses `fetch()` with a `ReadableStream` reader instead of the traditional `EventSource` API. This gives more control over error handling:

```javascript
// static/index.html (simplified)
const response = await fetch('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, folder }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();                      // keep the partial last line

    for (const line of lines) {
        if (line.startsWith('data: ')) {
            const raw = line.slice(6);
            if (raw === '[DONE]') continue;
            handleEvent(JSON.parse(raw));       // update the UI
        }
    }
}
```

The browser accumulates bytes into a text buffer, splits on newlines, extracts `data: ` lines, parses the JSON, and calls `handleEvent()` for each event.

---

## Event types

The agent stream produces these event types:

| Type | When emitted | Key fields |
|------|-------------|------------|
| `start` | Immediately when the query begins | `query` |
| `thinking` | When Claude returns a text block mid-stream (not a tool call) | `step`, `content` (first 500 chars) |
| `tool_call` | Each time Claude calls a tool | `step`, `tool`, `input`, `preview` |
| `answer` | When Claude finishes | `content` (full answer), `stats` |
| `error` | If an exception is raised | `message` |

---

## Running synchronous code inside async FastAPI

Milvus queries and LiteParse parsing are synchronous (blocking) operations. Running them directly inside an `async def` function would freeze the entire FastAPI server while they execute.

ParseRAG solves this with `asyncio.to_thread()`:

```python
# Inside the async run_agent_stream generator
result_content = await asyncio.to_thread(
    handle_tool_call, block.name, block.input
)
```

`asyncio.to_thread()` runs `handle_tool_call` in a separate thread from Python's thread pool. The async event loop is free to handle other requests while the thread is blocked waiting for Milvus or LiteParse to finish.

The same pattern is used in `/api/process` and `/api/index`:

```python
await asyncio.to_thread(pipeline, str(file_path))
```

---

## Screenshot serving and security

The screenshot endpoint has a simple path traversal guard:

```python
@app.get("/api/screenshot/{path:path}")
async def get_screenshot(path: str):
    if not str(file_path).startswith("screenshots/"):
        raise HTTPException(403, "Access denied")
```

Any path that doesn't start with `screenshots/` is rejected, preventing a caller from reading arbitrary files by passing something like `../../secrets.env`.
