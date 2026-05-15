# ParseRAG — 5 Suggested Improvements

---

## 1. Security: Sandbox the `/api/folders` Path Traversal

**Problem**  
`app.py:127` — the `/api/folders` endpoint accepts any `path` query parameter and resolves it with `Path(path).resolve()`, allowing a caller to browse any directory on the host filesystem (e.g. `C:\Users\...` or `/etc`). The screenshot endpoint (`app.py:154`) uses a fragile `startswith("screenshots/")` check that can be bypassed with a crafted path on Windows (`screenshots\..\secrets`).

**Fix**  
Introduce a configurable `DATA_ROOT` (defaulting to `./data`) and reject any resolved path that doesn't fall within it:

```python
DATA_ROOT = Path(os.getenv("DATA_ROOT", "data")).resolve()

base = Path(path).resolve()
if not str(base).startswith(str(DATA_ROOT)):
    raise HTTPException(403, "Access outside data root is not allowed")
```

Apply the same guard to `get_screenshot` using `Path.resolve()` comparison instead of a string prefix check.

**Why this is better**
The original check `str(file_path).startswith("screenshots/")` operates on the raw, unresolved string the caller supplies. On Windows, the path separator is `\`, so a request for `screenshots\..\secrets\config.env` passes the check (it starts with `screenshots`) but resolves to a completely different directory. `Path.resolve()` normalises the path — collapsing `..`, following symlinks, and making it absolute — *before* the comparison happens. After resolution, a traversal attempt produces an absolute path that cannot start with the screenshots root, so the 403 is guaranteed regardless of how the path was crafted.

---

## 2. Performance: Use Module-Level Singleton Clients

**Problem**  
`search.py:18` and `processing.py:61` — a new `genai.Client()` is instantiated on **every** embedding call, and `get_milvus_client()` opens a new `MilvusClient` connection on every invocation. This adds unnecessary connection overhead on each search or insert operation.

**Fix**  
Create module-level singletons that are initialised once and reused:

```python
# processing.py
_genai_client = genai.Client()
_milvus_client: MilvusClient | None = None

def get_milvus_client() -> MilvusClient:
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(uri=MILVUS_DB_PATH)
    return _milvus_client
```

Do the same in `search.py` — import and reuse the singleton from `processing` rather than creating a second client.

**Why this is better**
Every call to `embed_texts` or `embed_query` in the original code creates a fresh `genai.Client`, which internally sets up an `httpx` session and TLS connection. During a batch indexing run over 20 PDFs that produces 200 chunks, that is 40+ client constructions — each with its own connection overhead — that serve no purpose. With the singleton, the `httpx` connection pool is created once and kept alive, so subsequent embedding calls reuse the same HTTPS connection. The improvement is most visible during `pipeline_directory`: the original opens and closes the Milvus-Lite database file on every `pipeline` call inside the loop; the singleton holds the file handle open for the duration of the entire run, avoiding repeated file-open and WAL-initialisation overhead on every document.

---

## 3. Reliability: Guard the Agent Loop Against Runaway Iteration

**Problem**  
`agent.py:318` — the `while True` agent loop has no upper bound. A misbehaving model, a tool that always returns noisy results, or an API error that keeps producing tool calls will run indefinitely, exhausting the API budget silently. The `run_agent_stream` function has the same issue.

**Fix**  
Add a `max_steps` cap (defaulting to 20) and yield/raise an explicit error when it is hit:

```python
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))

while tool_call_count < MAX_STEPS:
    ...

# after the loop
yield {"type": "error", "message": f"Agent exceeded {MAX_STEPS} tool calls without finishing."}
```

Expose `max_steps` as an optional field on `QueryRequest` so the web UI can override it per query.

**Why this is better**
The original `while True` loop has no exit path except `stop_reason == "end_turn"`. If the model enters a pattern where it keeps issuing tool calls without converging — because a tool consistently returns empty results, because the query is ambiguous, or because of an upstream API quirk — the loop runs indefinitely and silently burns through API quota. The caller (web UI or CLI) has no way to detect this; the spinner just never stops. With the cap in place, the loop terminates after at most 20 tool calls and yields a structured error event that the UI can display. The `max_steps` override on `QueryRequest` means a user who genuinely needs more calls for a complex multi-document query can raise the limit without touching server configuration.

---

## 4. Quality: Replace Naive Chunking with Semantic Chunking + Overlap

**Problem**  
`processing.py:41` — `chunk_text` splits on blank lines with a hard 4096-character cap. This produces chunks that are too coarse for precise retrieval (a 4 KB chunk contains far more text than is needed to answer a narrow question), and paragraph splits can sever semantically coherent passages mid-thought. `chonkie[semantic]` is already listed in `requirements.txt` but is never used.

**Fix**  
Replace the manual chunker with `chonkie`'s `SemanticChunker`, which groups sentences into coherent chunks and supports overlap:

```python
from chonkie import SemanticChunker

_chunker = SemanticChunker(chunk_size=512, chunk_overlap=64)

def chunk_text(text: str) -> list[str]:
    return [c.text for c in _chunker.chunk(text)]
```

The `SemanticChunker` groups sentences by meaning before deciding where to cut, so splits occur at topic transitions rather than at arbitrary character counts. This makes overlap unnecessary: the key risk that overlap guards against — a sentence split mid-thought across two chunks — does not arise when the chunker itself ensures splits happen between coherent ideas.

**Why this is better**
The original chunker splits on blank lines with a 4096-character ceiling. A single medication page typically fits within that ceiling, so the chunk often contains an entire page: drug name, mechanism, side effects, dosing instructions, and contraindications all in one embedding. When a user asks "what are the side effects of Warfarin?", the retrieved chunk contains the answer buried alongside unrelated content, making the embedding less precise and the retrieval ranking less reliable. A 512-token semantic chunk covers one coherent topic — for example, the side-effects section of one drug — so its embedding represents exactly that concept. The result is higher similarity scores for relevant passages and lower noise from unrelated content in the same chunk.

---

## 5. Observability: Surface Per-File Errors from Batch Indexing

**Problem**  
`processing.py:193` — `pipeline_directory` catches exceptions per file with a bare `print(f" ERROR: {e}")` and moves on. The `/api/index` endpoint (`app.py:86`) returns only a total chunk count; callers have no way to know which files failed or why. In a 16-document corpus a silent failure on 4 files is easy to miss.

**Fix**  
Return a structured result from `pipeline_directory` and surface it through the API:

```python
# processing.py
def pipeline_directory(...) -> dict:
    results = {"ok": [], "errors": {}}
    for pdf in pdf_files:
        try:
            n = pipeline(str(pdf), screenshot_dir, reset=False)
            results["ok"].append({"file": pdf.name, "chunks": n})
        except Exception as e:
            results["errors"][pdf.name] = str(e)
    return results
```

```python
# app.py — /api/index response
return {
    "ok": True,
    "files_processed": len(result["ok"]),
    "files_failed": len(result["errors"]),
    "errors": result["errors"],
    "chunks": count,
}
```

This makes failures visible to the web UI and any downstream automation without requiring log access.

**Why this is better**
In the original code, a PDF that fails to parse — because it is corrupt, encrypted, or uses an unsupported encoding — produces a single `print` line on the server and is otherwise invisible. The `/api/index` response always returns `"ok": true` with a total chunk count, so a caller that indexed 16 documents but received chunks for only 12 has no way to know 4 files were silently dropped without tailing the server log. The structured return makes the same situation explicit in the API response: `"files_processed": 12, "files_failed": 4, "errors": {"report_Q4.pdf": "...", ...}`. The web UI can surface this directly — a banner, a failed-files list, or a retry button — without any server-side log access required.
