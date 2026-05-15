# ParseRAG — Proposed Features

## 1. Multi-turn Conversation with Session Memory

**What:** Persistent chat sessions where the agent carries context between queries — follow-ups like "What about the dosage for that medication?" work without re-searching. The agent accumulates a "what I've found" working memory per session, avoiding redundant tool calls.

**Why compelling:** The current agent resets on every query. This is the single biggest UX gap — real document workflows are iterative, not one-shot.

---

## 2. Structured Table Extraction Tool (`extract_table`)

**What:** A new tool that returns a table from a PDF as JSON or CSV (not raw text). The agent can then answer "Give me the drug interaction table as a CSV" or do aggregations on structured table data rather than wrestling with text layout.

**Why compelling:** The README explicitly calls out tables as the reason for LiteParse — but the agent still gets text. Structured output closes that loop and enables downstream data use.

---

## 3. Real-time Ingestion Progress with Streaming UI

**What:** Extend the SSE streaming to the `/api/index` endpoint — per-file status cards (parsing → chunking → embedding → stored) rendered in the same execution log UI. Add a collection health dashboard showing index coverage per folder.

**Why compelling:** Currently indexing a batch of 16 PDFs is a blind wait. This makes ingestion observable and makes the two-mode (indexed vs. filesystem) distinction visually concrete.

---

## 4. Cross-Document Reference Graph

**What:** When the agent follows cross-references during Phase 3 backtracking, persist those document→document edges. Render an interactive graph in the UI showing which documents cite which. Clicking a node queries "What does this document say about X?"

**Why compelling:** The three-phase strategy already discovers cross-references — this makes that knowledge persistent and explorable rather than ephemeral per-query.

---

## 5. Answer Export as Formatted Report

**What:** A one-click "Export" button that packages the final answer, inline citations, and referenced page screenshots into a self-contained PDF or Markdown report. Screenshot thumbnails become embedded figures with captions.

**Why compelling:** Right now useful answers live and die in the browser tab. For medical/legal/compliance use cases, the output needs to be shareable and auditable.
