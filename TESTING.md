# ParseRAG — Testing & Showcase Guide

Follow this guide from top to bottom. Every command is complete and ready to run.

---

## One-time setup

Do this once when you first clone the project.

### 1. Run the setup script

**bash / Git Bash:**
```bash
bash setup.sh
```

**PowerShell:**
```powershell
.\setup.ps1
```

This creates the virtual environment, installs all dependencies, and creates your `.env` file in one shot.

### 2. Add your API keys

Open `.env` in any text editor and fill in both keys:

```
ANTHROPIC_API_KEY=your-anthropic-api-key-here
GOOGLE_API_KEY=your-google-api-key-here
```

Both are required — Anthropic powers the Claude agent, Google powers the embeddings. Leave all other lines commented out.

---

## Starting the server

**bash / Git Bash:**
```bash
source .venv/Scripts/activate && python app.py
```

**PowerShell:**
```powershell
.venv\Scripts\Activate.ps1; python app.py
```

Wait until you see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Open **`http://localhost:8000`** in your browser. Keep this terminal open — the server must stay running while you test.

Open a **second terminal** and activate the environment:

**bash / Git Bash:**
```bash
source .venv/Scripts/activate
```

**PowerShell:**
```powershell
.venv\Scripts\Activate.ps1
```

---

## About the browser DevTools console

Several tests below use the browser console instead of curl. Press **F12** → **Console** tab. Paste the JavaScript snippet and press Enter.

---

## Part A — Five Improvements

---

### Improvement 1 — Security: Screenshot Path Traversal Protection

**What this proves:** The new `Path.resolve()` guard blocks path traversal attacks regardless of encoding.

#### Test A — Direct traversal (must be blocked)

**Via browser address bar:**
```
http://localhost:8000/api/screenshot/../app.py
```

**Via curl:**
```bash
curl -i "http://localhost:8000/api/screenshot/../app.py"
```

**Expected:** HTTP `404` or `403`. FastAPI normalises `../` in the URL before routing, so it returns 404. The file is never served.

---

#### Test B — Windows encoded traversal (must return 403)

**Via browser address bar:**
```
http://localhost:8000/api/screenshot/screenshots%5C..%5Capp.py
```

**Via curl:**
```bash
curl -i "http://localhost:8000/api/screenshot/screenshots%5C..%5Capp.py"
```

**Expected:**
```json
{"detail":"Access denied"}
```

---

#### Test C — Valid screenshot (must return 200)

Run this **after completing Feature 3** (which indexes the documents and creates screenshots):

**Via browser address bar:**
```
http://localhost:8000/api/screenshot/screenshots/01_NNMC_Medication_Side_Effects/page_1.png
```

**Via curl:**
```bash
curl -o page.png "http://localhost:8000/api/screenshot/screenshots/01_NNMC_Medication_Side_Effects/page_1.png"
```

**Expected:** Image renders in the browser, or `page.png` saves as a valid PNG.

---

### Improvement 2 — Performance: Singleton Clients

**What this proves:** Both clients return the same Python object on every call — no new connections opened after the first.

Run in the second terminal (server does not need to be running for this test):

```bash
python tests/showcase.py
```

Look for the **Improvement 2** section:

```
============================================================
Improvement 2: Performance — Singleton Clients
============================================================
  get_milvus_client() call 1 id: 2145678901234
  get_milvus_client() call 2 id: 2145678901234
  Same object? True

  get_genai_client() call 1 id: 2145678905678
  get_genai_client() call 2 id: 2145678905678
  Same object? True

  [PASS] Both clients are singletons — no redundant connections.
```

Both `id` values in each pair must be **identical**.

---

### Improvement 3 — Reliability: Agent Loop Cap

**What this proves:** Setting `max_steps` to a small number terminates the agent with a structured error instead of running indefinitely.

**Via browser DevTools console (F12 → Console):**
```javascript
fetch('/api/query', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ question: 'Give me a detailed summary of every document.', max_steps: 2 })
}).then(r => {
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  const read = () => reader.read().then(({ done, value }) => {
    if (done) return;
    console.log(dec.decode(value));
    read();
  });
  read();
});
```

**Via curl:**
```bash
curl -N -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"Give me a detailed summary of every document.\", \"max_steps\": 2}"
```

**Expected output:**
```
data: {"type": "start", "query": "Give me a detailed summary of every document.", "session_id": "..."}

data: {"type": "thinking", "step": 1, ...}

data: {"type": "tool_call", "step": 2, "tool": "...", ...}

data: {"type": "error", "message": "Agent exceeded 2 tool calls without finishing."}

data: [DONE]
```

The stream ends with an error after 2 tool calls.

---

### Improvement 4 — Quality: Semantic Chunking

**What this proves:** A multi-topic text block is split into focused per-topic chunks.

Run in the second terminal:

```bash
python tests/showcase.py
```

> **First run only:** A ~50 MB sentence-embedding model downloads automatically and is cached. You will see a download progress bar.

Look for the **Improvement 4** section:

```
============================================================
Improvement 4: Quality — Semantic Chunking
============================================================
  Input : 1 block of text covering 4 topics (914 chars)
  Naive paragraph split  : 4 chunks
  Semantic chunker output: 3 chunks

  --- Semantic chunks ---
  Chunk 1 (58 words): Warfarin (Coumadin) is an anticoagulant medication prescribed...
  Chunk 2 (8 words): Seek emergency care for any of these symptoms....
  Chunk 3 (75 words): The dose of warfarin is highly individualised and monitored...

  [PASS] Chunker produced 3 focused chunk(s).
```

---

### Improvement 5 — Observability: Structured Error Reporting

**What this proves:** Corrupt files are named explicitly in the API response instead of being silently dropped.

#### Step 1 — Create a mixed test directory

**bash / Git Bash:**
```bash
mkdir -p data/test_mixed
echo "this is not a valid PDF" > data/test_mixed/corrupt.pdf
cp data/Medication_Side_Effect_Flyer.pdf data/test_mixed/
```

**PowerShell:**
```powershell
New-Item -ItemType Directory -Force "data\test_mixed"
"this is not a valid PDF" | Out-File "data\test_mixed\corrupt.pdf" -Encoding utf8
Copy-Item "data\Medication_Side_Effect_Flyer.pdf" "data\test_mixed\"
```

#### Step 2 — Index and inspect the response

**Via browser DevTools console (F12 → Console):**
```javascript
fetch('/api/index', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ directory: 'data/test_mixed', reset: false })
}).then(r => r.json()).then(data => console.log(JSON.stringify(data, null, 2)));
```

**Via curl:**
```bash
curl -X POST http://localhost:8000/api/index \
  -H "Content-Type: application/json" \
  -d "{\"directory\": \"data/test_mixed\", \"reset\": false}"
```

**Expected:**
```json
{
  "ok": true,
  "files_processed": 1,
  "files_failed": 1,
  "files": 1,
  "errors": {
    "corrupt.pdf": "Parsing failed with exit code 1"
  },
  "chunks": 35
}
```

`corrupt.pdf` is named in `errors`. The valid PDF is processed and its chunk count appears in `chunks`.

#### Step 3 — Clean up

**bash / Git Bash:**
```bash
rm -rf data/test_mixed
```

**PowerShell:**
```powershell
Remove-Item -Recurse -Force "data\test_mixed"
```

---

## Part B — Five New Features

> **Run Feature 3 first.** It indexes all documents. Features 1, 2, and 4 require an indexed corpus to work.

---

### Feature 3 — Real-time Ingestion Progress with Streaming UI

**What this proves:** Indexing emits live per-file progress events as each stage completes.

#### Via the app UI

1. Go to **`http://localhost:8000`**
2. Click **Index All Documents**
3. Watch the **Execution Log** panel — cards appear one by one as each file moves through its stages:

```
#1  index_start   → Found 16 files
#2  file_start    → 01_NNMC_Medication_Side_Effects.pdf (1 of 16)
#3  file_stage    → parsing
#4  file_stage    → chunking
#5  file_stage    → embedding    ← longest stage (Gemini API call)
#6  file_stage    → storing
#7  file_done     → 29 chunks
#8  file_start    → 02_Huntington_Medication_Tip_Sheet.pdf (2 of 16)
    ...
    index_done    → 16 processed, 0 failed
```

Each card appears the moment that stage begins, not all at once at the end.

---

#### Via curl

```bash
curl -N -X POST http://localhost:8000/api/index/stream \
  -H "Content-Type: application/json" \
  -d "{\"directory\": \"data/documents\", \"reset\": true}"
```

**Expected stream (showing first file, then 15 more follow the same pattern):**
```
data: {"type": "index_start", "total": 16}
data: {"type": "file_start", "file": "01_NNMC_Medication_Side_Effects.pdf", "index": 1, "total": 16}
data: {"type": "file_stage", "file": "01_NNMC_Medication_Side_Effects.pdf", "stage": "parsing"}
data: {"type": "file_stage", "file": "01_NNMC_Medication_Side_Effects.pdf", "stage": "chunking"}
data: {"type": "file_stage", "file": "01_NNMC_Medication_Side_Effects.pdf", "stage": "embedding"}
data: {"type": "file_stage", "file": "01_NNMC_Medication_Side_Effects.pdf", "stage": "storing"}
data: {"type": "file_done", "file": "01_NNMC_Medication_Side_Effects.pdf", "chunks": 29}
... 15 more files ...
data: {"type": "index_done", "files_processed": 16, "files_failed": 0, "chunks": 1384}
data: [DONE]
```

This also runs **Improvement 1 Test C** — screenshots are now on disk. Test it now:

```bash
curl -o page.png "http://localhost:8000/api/screenshot/screenshots/01_NNMC_Medication_Side_Effects/page_1.png"
```

**Expected:** `page.png` saves successfully (HTTP 200).

---

### Feature 1 — Multi-turn Conversation with Session Memory

**What this proves:** The agent carries context between queries. A follow-up works without re-searching from scratch.

#### Via the app UI

1. Go to **`http://localhost:8000`**
2. Type: `What are the side effects of Warfarin?` → click **Execute**
3. Wait for the answer. The session indicator below the folder input shows `Session: a1b2c3d4…`
4. Type: `What is the typical dose?` → click **Execute** again
5. The agent answers without calling `search` again — the Execution Log shows 0 tool calls on the follow-up
6. Click **New Session** (top right) — the indicator resets. The next query starts fresh

---

#### Via curl

```bash
# First query — note the session_id in the first line of output
curl -N -s -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"What are the side effects of Warfarin?\"}" | head -2
```

The first line shows:
```
data: {"type": "start", "query": "...", "session_id": "a1b2c3d4-..."}
```

Copy that `session_id`, then:

```bash
curl -N -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"What is the typical dose?\", \"session_id\": \"PASTE-SESSION-ID-HERE\"}"
```

**Expected:** The follow-up answer arrives with fewer tool calls than the first query.

---

### Feature 2 — Structured Table Extraction (`extract_table`)

**What this proves:** The agent extracts PDF tables as structured JSON rows instead of raw text.

#### Via the app UI

1. Go to **`http://localhost:8000`**
2. Type: `Extract any tables from the medications PDF as structured data and list the rows.`
3. Click **Execute**
4. Watch the **Execution Log** — a card labelled **`extract_table`** appears with a JSON preview showing column headers and data rows

---

#### Via curl

```bash
curl -N -s -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"Extract any tables from the medications PDF as structured data.\", \"folder\": \"data/documents\"}" \
  | grep extract_table
```

**Expected:** At least one line containing `"tool": "extract_table"` with a `preview` containing JSON rows with column headers and data.

---

### Feature 4 — Cross-Document Reference Graph

**What this proves:** Documents retrieved together in answers accumulate weighted co-citation edges, stored in `graph.db` and visualised as an interactive graph.

#### Step 1 — Build edges

Run this query — it retrieves documents from multiple PDFs in one answer:

**Via curl:**
```bash
curl -N -s -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"What side effects do gabapentin and warfarin have?\", \"max_steps\": 12}" \
  | grep '"type": "answer"' | head -1
```

**Via the app UI:** Type `What side effects do gabapentin and warfarin have?` and click **Execute**.

**Expected:** The answer cites documents from both `01_NNMC_Medication_Side_Effects.pdf`, `02_Huntington_Medication_Tip_Sheet.pdf`, and `06_VA_Gabapentin_Factsheet.pdf`.

---

#### Step 2 — View the graph

**Via browser address bar:**
```
http://localhost:8000/api/graph
```

**Via curl:**
```bash
curl http://localhost:8000/api/graph
```

**Expected:**
```json
{
  "nodes": [
    {"id": "01_NNMC_Medication_Side_Effects.pdf"},
    {"id": "02_Huntington_Medication_Tip_Sheet.pdf"},
    {"id": "06_VA_Gabapentin_Factsheet.pdf"}
  ],
  "edges": [
    {"source": "01_NNMC_Medication_Side_Effects.pdf", "target": "02_Huntington_Medication_Tip_Sheet.pdf", "weight": 1},
    {"source": "01_NNMC_Medication_Side_Effects.pdf", "target": "06_VA_Gabapentin_Factsheet.pdf", "weight": 1},
    {"source": "02_Huntington_Medication_Tip_Sheet.pdf", "target": "06_VA_Gabapentin_Factsheet.pdf", "weight": 1}
  ]
}
```

**Via the app UI:** Scroll below the stats bar to the **Document Graph** panel. Nodes and edges appear. Click any node to pre-fill the query box. Click **Refresh** to reload after new queries.

---

#### Step 3 — Verify persistence across restarts

Stop the server (`Ctrl+C`). Restart it:

```bash
source .venv/Scripts/activate && python app.py
```

Open `http://localhost:8000/api/graph` — all edges are still there because they are stored in `graph.db`.

---

### Feature 5 — Answer Export as Formatted Report

**What this proves:** One click downloads the answer, citations, and screenshots as a self-contained Markdown or PDF file.

#### Via the app UI

1. Run any question and wait for the answer
2. Two buttons appear below the response panel: **Export MD** and **Export PDF**
3. Click either — the file downloads immediately

---

#### Via curl

```bash
# Markdown
curl -X POST http://localhost:8000/api/export \
  -H "Content-Type: application/json" \
  -d "{\"format\": \"md\", \"content\": \"Warfarin is a blood thinner [Source: med.pdf | Page 1 | screenshots/med/page_1.png].\"}" \
  -o report.md && cat report.md
```

```bash
# PDF — verify it is a valid PDF file
curl -X POST http://localhost:8000/api/export \
  -H "Content-Type: application/json" \
  -d "{\"format\": \"pdf\", \"content\": \"Warfarin is a blood thinner [Source: med.pdf | Page 1 | screenshots/med/page_1.png].\"}" \
  -o ./report.pdf && python -c "print('Valid PDF:', open('./report.pdf','rb').read(4) == b'%PDF')"
```

**Expected Markdown:**
```markdown
# ParseRAG Export

_Generated: 2026-05-15 ..._

---

Warfarin is a blood thinner [1].

---

## Sources

[1] **med.pdf** — Page 1
    Path: `screenshots/med/page_1.png`
```

**Expected PDF check:**
```
Valid PDF: True
```

---

## Summary

| # | Item | Quickest way to test | API key needed? |
|---|---|---|---|
| I-1 | Security | Browser address bar: `.../api/screenshot/../app.py` | No |
| I-2 | Performance | `python tests/showcase.py` | No |
| I-3 | Reliability | curl with `"max_steps": 2` | Yes |
| I-4 | Quality | `python tests/showcase.py` | No |
| I-5 | Observability | curl `/api/index` with mixed directory | Yes |
| F-3 | Streaming Index | curl `/api/index/stream` or click Index All Documents | Yes |
| F-1 | Session Memory | Two queries back to back in the UI | Yes |
| F-2 | Table Extraction | Ask agent to extract a table | Yes |
| F-4 | Reference Graph | Ask about gabapentin and warfarin, then check `/api/graph` | Yes |
| F-5 | Export | curl `/api/export` or Export buttons in UI | No |
