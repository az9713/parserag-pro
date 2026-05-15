# ParseRAG — Implementation Decisions

This document records the five improvements from `IMPROVEMENTS.md`, the decisions made before implementation, and the pros and cons of each choice. It serves as the authoritative reference for the implementation phase.

---

## 1. Security: Screenshot Endpoint Path Traversal

### Problem

`app.py:154` — `get_screenshot` uses a fragile string-prefix check to restrict file serving:

```python
if not str(file_path).startswith("screenshots/"):
    raise HTTPException(403, "Access denied")
```

On Windows this is bypassable with a crafted path such as `screenshots\..\secrets\config.env`. The check operates on the raw (un-resolved) string, so symlinks and relative components are not normalised before the guard runs.

**Note on `/api/folders`:** `IMPROVEMENTS.md` also flagged the folder browser endpoint as a path-traversal risk. After review, the folder browser is intentionally free-roaming (users need to navigate arbitrary corpus directories), so no restriction is applied there. Only the file-serving endpoint is fixed.

### Decision

Replace the string-prefix check with a `Path.resolve()` comparison against a fixed `SCREENSHOTS_ROOT`:

```python
SCREENSHOTS_ROOT = Path("screenshots").resolve()

resolved = Path(path).resolve()
if not resolved.is_relative_to(SCREENSHOTS_ROOT):
    raise HTTPException(403, "Access denied")
```

`Path.is_relative_to()` is available from Python 3.9 onward and operates on the fully-resolved absolute path, defeating symlink and relative-component attacks on all platforms.

### Pros

- Eliminates the Windows path-separator bypass (`\` vs `/`)
- Handles symlink traversal — the resolved path must be inside `screenshots/`
- No change to user-facing behaviour for valid requests
- Simple, idiomatic Python — no regex or manual string manipulation

### Cons / Trade-offs

- Requires Python 3.9+. If the environment runs 3.8, `is_relative_to` is unavailable and a fallback (e.g., `str(resolved).startswith(str(SCREENSHOTS_ROOT))`) must be used — which reintroduces the prefix-collision risk (`screenshots_extra/`). **Assumed: Python 3.9+ is in use.**
- `SCREENSHOTS_ROOT` is resolved at module import time relative to the process working directory. If the server is started from a different directory, the root changes. Mitigation: anchor to `Path(__file__).parent / "screenshots"` instead of a bare relative path.
- The folder browser (`/api/folders`) remains unrestricted by design. A misconfigured or compromised client could still enumerate the host filesystem through that endpoint. Acceptable for single-user local deployment; would need revisiting for a multi-user or networked deployment.

---

## 2. Performance: Module-Level Singleton Clients

### Problem

`processing.py:61` — a new `genai.Client()` is constructed on every call to `embed_texts`, which is called once per PDF processing run and once per embedding batch. `get_milvus_client()` (`processing.py:74`) opens a new `MilvusClient` (and therefore a new file handle to the Milvus-Lite SQLite database) on every invocation. `search.py:18` creates its own independent `genai.Client()` on every query.

The overhead is connection setup and (for Milvus-Lite) file-open cost, not network latency — but it accumulates across a multi-document indexing run.

### Decision

Introduce module-level singletons in `processing.py` and reuse them in `search.py`:

```python
# processing.py
_genai_client: genai.Client | None = None
_milvus_client: MilvusClient | None = None

def get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client

def get_milvus_client() -> MilvusClient:
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(uri=MILVUS_DB_PATH)
    return _milvus_client
```

`search.py` imports and calls `get_genai_client()` and `get_milvus_client()` from `processing` rather than constructing its own instances.

**Thread-safety note (documented risk):** The web server runs async handlers that offload blocking work via `asyncio.to_thread()`. Multiple threads could therefore call `get_milvus_client()` concurrently before the singleton is initialised. The lazy-init pattern above has a race condition: two threads could both observe `_milvus_client is None` and each construct a client. The second assignment wins and the first client is leaked. For the intended use case — single-user local deployment with low concurrency — this race is harmless in practice. A `threading.Lock` would eliminate it but adds complexity that is unnecessary here.

### Pros

- Eliminates repeated connection setup on every search and embed call
- `genai.Client` uses `httpx` with connection pooling — the singleton keeps the pool alive across requests, reducing TLS handshake overhead
- Milvus-Lite holds the database file open once rather than opening and closing it per call
- Reduces code duplication: `search.py` no longer needs its own `genai` import for client construction

### Cons / Trade-offs

- **Race condition (accepted risk):** Two concurrent threads may each construct a client before the singleton is set. Safe for single-user use; not safe for high-concurrency multi-user deployment without a lock.
- Module-level state makes unit testing harder — tests must explicitly reset the singletons between runs to avoid cross-test pollution.
- If `MILVUS_DB_PATH` or the Google API key changes at runtime (e.g., via environment variable reload), the singleton will not reflect the new value without a process restart.

---

## 3. Reliability: Agent Loop Iteration Cap

### Problem

`agent.py:318` and `agent.py:389` — both `run_agent` and `run_agent_stream` contain unbounded `while True` loops. A misbehaving model, a tool that consistently returns noisy or empty results, or an API error that keeps generating tool calls will run indefinitely, silently draining API quota.

### Decision

Add a `MAX_STEPS` environment variable (defaulting to `20`) that caps the total number of tool calls across both functions. Apply the cap identically to `run_agent` and `run_agent_stream`. Expose `max_steps` as an optional field on `QueryRequest` so callers can override it per-query:

```python
# agent.py
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "20"))

# QueryRequest (app.py)
class QueryRequest(BaseModel):
    question: str
    folder: str = ""
    max_steps: int = 20
```

When the cap is hit, `run_agent` raises a `RuntimeError` and `run_agent_stream` yields an `{"type": "error", ...}` event before returning, so the UI surfaces the failure rather than hanging.

`MAX_STEPS` counts **tool calls**, not loop iterations. One loop iteration can contain multiple tool calls if the model emits several `tool_use` blocks in a single response turn.

### Pros

- Hard upper bound on API cost for any single query
- Failure is explicit: the UI shows an error rather than a spinner that never resolves
- Per-request override means power users can raise the cap for complex multi-document queries without changing the global default
- Environment variable override means operators can tune the cap without code changes

### Cons / Trade-offs

- A cap of 20 tool calls may be too low for large corpus scans (e.g., `scan_folder` on a 50-document directory, followed by multiple `parse_file` calls). Users hitting the cap on legitimate complex queries will need to either raise `max_steps` in their request or increase `AGENT_MAX_STEPS`.
- Counting tool calls rather than loop iterations means the cap's effective "depth" depends on how many tools the model issues per turn, which varies. A model that consistently issues 3 tools per turn hits the cap after ~7 iterations; one that issues 1 per turn gets 20 iterations.
- `run_agent` (synchronous, used for CLI/testing) and `run_agent_stream` (async, used by the web UI) must be kept in sync manually — two places to update if the logic changes.

---

## 4. Quality: Semantic Chunking

### Problem

`processing.py:41` — `chunk_text` splits on blank lines with a hard 4096-character cap. This produces coarse chunks: a 4 KB chunk spans multiple topics, diluting the embedding and reducing retrieval precision. Splits at paragraph boundaries can also sever a semantically coherent passage mid-thought. `chonkie[semantic]` is listed in `requirements.txt` but is never imported or used.

### Decision

Replace `chunk_text` with `chonkie`'s `SemanticChunker` at `chunk_size=512` tokens, **without explicit overlap**:

```python
from chonkie import SemanticChunker

_chunker = SemanticChunker(chunk_size=512)

def chunk_text(text: str) -> list[str]:
    chunks = _chunker.chunk(text)
    return [c.text for c in chunks] if chunks else [text]
```

**Why no overlap?** Overlap is the correct remedy for fixed-size sliding-window chunkers, where a sentence can be arbitrarily split at a chunk boundary. `SemanticChunker` groups sentences by semantic similarity before deciding where to cut — splits occur at *topic transitions*, not at fixed character counts. Key sentences stay intact within a chunk by design, making overlap redundant. Adding overlap via a different chunker (e.g., `TokenChunker`) would trade semantic coherence for boundary safety — the wrong trade-off for a retrieval system over structured medical documents.

**Runtime note:** `SemanticChunker` downloads a sentence-embedding model (a minishlab/model2vec variant, ~50 MB) on first use. This is a one-time cost cached in the user's Hugging Face hub cache directory. On a machine without internet access or behind a proxy, this download will fail silently or raise a connection error.

**Deviation from `IMPROVEMENTS.md`:** The doc shows `SemanticChunker(chunk_size=512, chunk_overlap=64)`. `chunk_overlap` is not a valid parameter for `SemanticChunker` in chonkie 1.x — it would either be silently ignored or raise a `TypeError`. The `chunk_overlap` parameter belongs to `SlidingWindowChunker` / `TokenChunker`. The corrected constructor is `SemanticChunker(chunk_size=512)`.

### Pros

- Chunks align with semantic units (a drug's side effects, a dosing section) rather than arbitrary character counts — significantly improves embedding quality
- 512-token chunks are ~8× smaller than the current 4096-char ceiling — retrieval returns focused, relevant passages rather than entire pages
- Uses the library already declared in `requirements.txt` — no new dependency
- Drop-in replacement: `chunk_text` signature is unchanged; all callers work without modification
- Handles edge cases (empty text, single-sentence documents) more gracefully than the manual splitter

### Cons / Trade-offs

- **Hidden model download on first use.** The sentence-embedding model (~50 MB) is fetched from Hugging Face Hub at runtime. Fails in air-gapped environments. No user-visible progress indicator during download.
- **Two embedding models in the system.** The semantic chunker uses its own internal model (for deciding where to split); Gemini `gemini-embedding-001` is used for retrieval embeddings. These are independent — the chunker's model is never used for search. This is correct behaviour but may confuse future maintainers.
- **Non-deterministic chunk boundaries.** The chunker's internal similarity threshold can produce different chunk counts for the same document across library versions. Re-indexing after a chonkie upgrade may produce subtly different chunks, requiring a full collection reset.
- **No overlap.** If a key fact genuinely spans a semantic boundary (rare but possible), it may be split. The risk is lower than with naive chunking but not zero.
- `SemanticChunker` is slower than the naive splitter — it runs inference for every sentence. For a 100-page document this adds a few seconds to the indexing pipeline.

---

## 5. Observability: Structured Error Reporting from Batch Indexing

### Problem

`processing.py:193` — `pipeline_directory` catches per-file exceptions with `print(f" ERROR: {e}")` and continues silently. `app.py:86` (`/api/index`) returns only `{"ok": True, "files": N, "chunks": N}`. A caller that indexes 20 documents has no way to know whether 5 of them failed without reading server logs.

### Decision

Refactor `pipeline_directory` to return a structured result dict and surface it through `/api/index`:

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

# app.py — /api/index response
return {
    "ok": True,
    "files_processed": len(result["ok"]),
    "files_failed": len(result["errors"]),
    "errors": result["errors"],
    "chunks": count,
}
```

Chunk count in the API response continues to come from `client.get_collection_stats()` (the source of truth in Milvus) rather than summing `result["ok"]`, keeping it consistent with the `/api/status` and `/api/process` endpoints.

### Pros

- Failures are visible to the caller without log access — the web UI can display a per-file error table
- The function return type is explicit and testable — unit tests can assert on `result["errors"]` directly
- Partial success is clearly distinguished from total failure: `files_processed=15, files_failed=5` is unambiguous
- Backward-compatible response shape: `ok`, `files`, and `chunks` keys are preserved; new keys (`files_processed`, `files_failed`, `errors`) are additive

### Cons / Trade-offs

- Error messages in `result["errors"]` are raw Python exception strings. These may contain internal file paths or stack details that are unhelpful or noisy for end users. A production system would normalise them to user-friendly messages.
- `pipeline_directory` currently returns `None`; any code that calls it and ignores the return value (e.g., CLI scripts) is unaffected, but code that checks the return value for truthiness (`if pipeline_directory(...)`) would now always branch true. Audit call sites before merging.
- The API response uses `files_processed` while the old response used `files`. This is a breaking change for any client that reads the `files` key. The old key should be retained as an alias during transition if clients exist.
- Silent failures are replaced by visible failures — operators who were unaware of dropped files may now see a noisier dashboard. This is a feature, not a bug, but worth communicating to users.

---

## Summary Table

| # | Area | Decision | Key Trade-off Accepted |
|---|------|----------|----------------------|
| 1 | Security | Fix screenshot endpoint with `is_relative_to`; leave folder browser unrestricted | Folder browser remains a read-only enumeration risk on LAN |
| 2 | Performance | Module-level singletons; lazy init; no lock | Race condition on cold start under concurrent load (acceptable for single-user) |
| 3 | Reliability | `MAX_STEPS=20` env var; per-request override via `QueryRequest.max_steps` | Cap may be too low for large corpus queries; two loops must stay in sync |
| 4 | Quality | `SemanticChunker(chunk_size=512)`; no overlap | Hidden model download on first use; two embedding models in the system |
| 5 | Observability | Structured `ok`/`errors` return; chunk count from Milvus stats | `files` → `files_processed` is a breaking key rename |
