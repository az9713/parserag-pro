# Claude agent and tool use

The Claude agent is the reasoning engine of ParseRAG. It receives a question, decides which tools to call, reads the results, and writes a cited answer. This document explains how tool use works, how the agent loop is structured, and how it is adapted for streaming.

---

## What "tool use" means

A standard AI chatbot takes a question and returns an answer based on its training data. Claude can do that, but it can also do something more powerful: **call functions**.

When ParseRAG asks Claude "what are the side effects of Warfarin?", Claude doesn't answer from memory. Instead it returns a structured message saying "I want to call the `search` function with this input: `{query: 'Warfarin side effects', limit: 3}`." ParseRAG's code then actually runs that search, gets the results, and sends them back to Claude. Claude reads the results and either asks for more (via another tool call) or writes the final answer.

This loop — Claude requests → code executes → Claude reads → repeat — is the **agent loop**.

---

## The 8 tools

Claude has access to 8 tools, split into two groups:

### Indexed search tools (Milvus)

| Tool | What it does |
|------|-------------|
| `search` | Vector similarity search over indexed document chunks. Fastest path to an answer. |
| `get_image` | Loads a page screenshot as a base64 image so Claude can look at tables and charts directly. |

### Filesystem exploration tools

| Tool | What it does |
|------|-------------|
| `scan_folder` | Previews all supported files in a folder in parallel (4 workers). Use first when exploring a new folder. |
| `preview_file` | First 3000 characters of a single file — a quick relevance check before a full parse. |
| `parse_file` | Full content of a PDF or text file. Uses LiteParse for PDFs. |
| `read_file` | Plain text read for `.txt`, `.md`, `.csv`, `.json` files. |
| `grep` | Regex pattern search within a file with surrounding context lines. |
| `glob` | Find files matching a name pattern (e.g. `*.pdf`) in a directory tree. |

---

## The agent loop in detail

The synchronous version (`run_agent` in `src/agent.py`) runs as follows. The async streaming version works identically except it yields events between steps.

### Initialization

```python
client = anthropic.Anthropic()
messages = [{"role": "user", "content": user_question}]
```

A conversation starts with one message: the user's question. If a folder was specified in the UI, the question is prefixed with `Working directory: {folder}\n\nTask: {question}` so Claude knows where to look.

### Iteration

Each pass through the `while True` loop calls the Claude API once:

```python
response = client.messages.create(
    model=CLAUDE_MODEL,
    max_tokens=4096,
    system=SYSTEM_PROMPT,
    tools=TOOLS,
    messages=messages,
)
```

Claude reads the full conversation (including all previous tool results) and returns a response.

### Termination check

```python
if response.stop_reason == "end_turn":
    # Extract text from response and return it
    return final_text
```

`stop_reason == "end_turn"` means Claude decided to write a final answer rather than call more tools. The loop exits.

### Tool execution

If `stop_reason == "tool_use"`, Claude's response contains one or more tool call blocks. For each one:

```python
for block in assistant_content:
    if block.type == "tool_use":
        result_content = handle_tool_call(block.name, block.input)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result_content,
        })
```

`handle_tool_call` dispatches to the appropriate function: `search()`, `scan_folder()`, etc. The results are collected into a `tool_results` list.

### Feeding results back

```python
messages.append({"role": "assistant", "content": assistant_content})
messages.append({"role": "user", "content": tool_results})
```

The assistant's response (including its tool call blocks) is appended to the conversation. Then the tool results are appended as a new user message. This is how the conversation grows — Claude sees its own tool calls and the results the code returned, and can reason over all of them.

### Full loop visualization

```
User question
    │
    ▼
Claude API call
    │
    ├── stop_reason == "end_turn" → return final text answer
    │
    └── stop_reason == "tool_use"
            │
            ├── Execute each tool call
            │       search() / get_image() / scan_folder() / ...
            │
            └── Append results to messages → back to top of loop
```

---

## The streaming variant

`run_agent_stream` (in `src/agent.py`) is an async generator — it runs the same loop but `yield`s events at each step so the web server can push them to the browser in real time.

```python
async def run_agent_stream(query, folder=".") -> AsyncGenerator[dict, None]:
    ...
    yield {"type": "start", "query": query}

    while True:
        response = await client.messages.create(...)
        ...
        for block in assistant_content:
            if block.type == "tool_use":
                result_content = await asyncio.to_thread(handle_tool_call, ...)
                yield {
                    "type": "tool_call",
                    "step": step,
                    "tool": block.name,
                    "input": block.input,
                    "preview": result_content[:300],
                }
        ...
        if response.stop_reason == "end_turn":
            yield {"type": "answer", "content": final_text, "stats": {...}}
            return
```

The tool calls use `asyncio.to_thread()` because `handle_tool_call` is synchronous (it calls Milvus and LiteParse, which are blocking). This keeps the async event loop unblocked so FastAPI can serve other requests while waiting.

---

## The system prompt

The system prompt (defined in `SYSTEM_PROMPT` in `src/agent.py`) is a large string sent to Claude before any user message. It specifies:

- Which tools are available and when to use each
- The three-phase exploration strategy for unindexed documents
- Citation format: `[Source: filename | Page N | screenshot_path]`
- Final answer structure: direct answer → details with citations → sources consulted

Claude reads the system prompt on every API call. It shapes Claude's reasoning for the entire session.

---

## Tool definitions

Each tool is described to Claude as a JSON schema. Claude reads these at inference time to understand what arguments each tool accepts:

```python
{
    "name": "search",
    "description": "Search the indexed knowledge base using vector similarity...",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query..."},
            "limit": {"type": "integer", "description": "Max chunks to return", "default": 3},
        },
        "required": ["query"],
    },
}
```

When Claude decides to call `search`, its response contains `block.name = "search"` and `block.input = {"query": "Warfarin side effects", "limit": 3}`. The code dispatches on `block.name` in `handle_tool_call`.

---

## How images are passed to Claude

When Claude calls `get_image`, the code reads the PNG file from disk and encodes it as base64:

```python
return [{
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": img_b64,
    },
}]
```

This image block is included in the tool result message. Claude's multimodal capabilities allow it to read tables and charts directly from the image.

---

## Gotchas

**No iteration limit.** The agent loop runs until Claude returns `stop_reason == "end_turn"`. There is no maximum number of iterations. For most queries this terminates in 2–6 tool calls. A poorly worded question or an edge case in the document set could theoretically cause more calls. If you are implementing your own loop, consider adding a guard like `if tool_call_count > 20: break`.

**Tool call ordering.** Claude may return multiple tool calls in a single response. ParseRAG executes them sequentially (not in parallel) and sends all results back in one `tool_result` message. The sequential execution is a limitation of the current implementation — parallel execution would be faster for independent tool calls.

**Context window.** Claude has a maximum context window. Long agent sessions with many tool results accumulate text quickly. If the conversation grows too large, Claude will return a context length error. The current implementation does not truncate older tool results.
