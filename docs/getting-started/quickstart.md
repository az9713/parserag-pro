# Quickstart

Get ParseRAG running and answer your first question in about 10 minutes.

Prerequisites: Python 3.12, a Google Gemini API key, and an Anthropic API key.

---

## 1. Create a virtual environment and install dependencies

```bash
python3.12 -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install fastapi uvicorn
pip install 'setuptools<81'        # required for milvus-lite compatibility
```

Verify the install:

```bash
python -c "import pymilvus, anthropic, liteparse; print('OK')"
```

Expected output: `OK`

---

## 2. Set your API keys

```bash
export GOOGLE_API_KEY="your-gemini-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

On Windows (PowerShell):

```powershell
$env:GOOGLE_API_KEY = "your-gemini-api-key"
$env:ANTHROPIC_API_KEY = "your-anthropic-api-key"
```

Both keys must be set before any command that calls Claude or generates embeddings.

---

## 3. Index the sample documents

```bash
python main.py index data/documents
```

Expected output (abbreviated):

```
Found 16 PDF files in data/documents
[1/16] 01_NNMC_Medication_Side_Effects.pdf
  Parsing PDF with LiteParse...
  Extracted text from 4 pages.
  ...
  Inserted 12 records.
...
Indexing complete!
  Files processed: 16
  Total chunks: 214
  Collection rows: 214
```

This runs the ingestion pipeline for all 16 medical PDFs, storing 200+ searchable chunks in `milvus_lite.db`.

---

## 4. Ask a question via CLI

```bash
python main.py agent "What are the side effects of Warfarin?"
```

Expected output (abbreviated):

```
============================================================
Query: What are the side effects of Warfarin?
============================================================

  Tool call #1: search
  Input: {"query": "Warfarin side effects", "limit": 3}
  Result: SOURCE: 01_NNMC_Medication_Side_Effects.pdf...

Final Answer:
Warfarin (Coumadin) side effects include unusual bleeding or bruising,
blood in urine or stool... [Source: 01_NNMC_Medication_Side_Effects.pdf | Page 1 | ...]

Total tool calls: 2
```

---

## 5. Launch the web UI

```bash
uvicorn app:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

- The status badge should show **Indexed (214 chunks)** — confirming Milvus data is loaded.
- Type a question in the query box and press **Execute** (or Enter).
- Watch the Execution Log fill with tool call cards in real time.
- Click any citation in the answer to view the source page screenshot.

---

## What just happened

When you pressed Execute:

1. The browser sent your question to `POST /api/query`.
2. FastAPI opened an SSE stream and started the Claude agent.
3. Claude called the `search` tool; ParseRAG ran a vector similarity search in Milvus.
4. Claude received the matching text chunks and wrote a cited answer.
5. FastAPI streamed each step to the browser as a JSON event.
6. The browser rendered the events in real time.

See [What is ParseRAG?](../overview/what-is-this.md) for the complete step-by-step trace.

---

## Next steps

- **Index your own PDFs:** `python main.py index /path/to/your/pdfs`
- **Single PDF:** `python main.py process myfile.pdf`
- **Direct vector search (no agent):** `python main.py search "query" -l 5`
- **Run the evaluation suite:** `python main.py eval --output results.json`
- **Understand the architecture:** [System Design](../architecture/system-design.md)
