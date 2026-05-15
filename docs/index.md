# ParseRAG Documentation

ParseRAG is a document question-answering system that finds answers inside PDF collections. It combines fast vector search (Milvus) with an AI agent (Claude) that can also explore documents directly when needed.

---

## Documentation

| Section | What's inside |
|---------|--------------|
| [What is ParseRAG?](overview/what-is-this.md) | Mental model, end-to-end query trace, how all pieces fit |
| [Key Concepts](overview/key-concepts.md) | Definitions for embeddings, SSE, tool use, Milvus, and more |
| [Getting Started](getting-started/quickstart.md) | Install, index documents, ask a question |
| [Ingestion Pipeline](concepts/ingestion-pipeline.md) | How PDFs become searchable: parse → chunk → embed → store |
| [FastAPI and SSE Streaming](concepts/fastapi-and-sse.md) | How the server streams real-time updates to the browser |
| [Claude Agent and Tool Use](concepts/claude-agent.md) | How the AI agent decides what to do and calls tools |
| [Milvus and Vector Search](concepts/milvus-and-search.md) | How similarity search finds meaning, not just keywords |
| [System Design](architecture/system-design.md) | Full architecture diagram, data flows, component breakdown |

> **New here?** Start with [What is ParseRAG?](overview/what-is-this.md) for the mental model, then follow the learning path at the bottom of that page.
