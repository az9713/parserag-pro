"""
FastAPI backend for ParseRAG Web UI.
Serves the frontend and provides SSE streaming for agent execution.
"""
import json
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # must run before src.* imports read os.getenv at module level

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, Response as FastAPIResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent import run_agent_stream
from src.processing import (
    pipeline,
    pipeline_directory,
    get_milvus_client,
    COLLECTION_NAME,
)
from src.sessions import new_session, purge_expired
from src.graph import get_graph
from src.export import to_markdown, to_pdf


@asynccontextmanager
async def lifespan(app: FastAPI):
    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            purge_expired()
    task = asyncio.create_task(_cleanup())
    yield
    task.cancel()


app = FastAPI(title="ParseRAG", lifespan=lifespan)

# Only files inside this directory are served by /api/screenshot.
SCREENSHOTS_ROOT = (Path(__file__).parent / "screenshots").resolve()

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Default PDF path
DEFAULT_PDF = "data/Medication_Side_Effect_Flyer.pdf"


# --- Models ---

class QueryRequest(BaseModel):
    question: str
    folder: str = ""
    max_steps: int = 20
    session_id: str | None = None


class ProcessRequest(BaseModel):
    file: str = DEFAULT_PDF


class IndexRequest(BaseModel):
    directory: str = "data/documents"
    reset: bool = True


# --- Routes ---

@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/status")
async def status():
    """Check if the Milvus collection has data."""
    try:
        client = get_milvus_client()
        if client.has_collection(COLLECTION_NAME):
            stats = client.get_collection_stats(COLLECTION_NAME)
            count = stats.get("row_count", 0)
            return {"processed": count > 0, "chunk_count": count}
    except Exception:
        pass
    return {"processed": False, "chunk_count": 0}


@app.post("/api/process")
async def process_pdf(req: ProcessRequest):
    """Run the PDF processing pipeline."""
    file_path = Path(req.file)
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {req.file}")
    try:
        await asyncio.to_thread(pipeline, str(file_path))
        client = get_milvus_client()
        stats = client.get_collection_stats(COLLECTION_NAME)
        count = stats.get("row_count", 0)
        return {"ok": True, "chunks": count}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/index")
async def index_directory(req: IndexRequest):
    """Index all PDFs in a directory."""
    dir_path = Path(req.directory)
    if not dir_path.exists():
        raise HTTPException(404, f"Directory not found: {req.directory}")
    try:
        result = await asyncio.to_thread(pipeline_directory, str(dir_path), "screenshots", req.reset)
        client = get_milvus_client()
        stats = client.get_collection_stats(COLLECTION_NAME)
        count = stats.get("row_count", 0)
        return {
            "ok": True,
            "files_processed": len(result["ok"]),
            "files_failed": len(result["errors"]),
            "files": len(result["ok"]),  # backward-compat alias
            "errors": result["errors"],
            "chunks": count,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/query")
async def query_agent(req: QueryRequest):
    """Stream agent execution steps via SSE."""
    if not req.question.strip():
        raise HTTPException(400, "Question is required")

    folder = req.folder.strip() if req.folder else "."
    effective_sid = req.session_id or new_session()

    async def event_stream():
        async for event in run_agent_stream(req.question, folder=folder, max_steps=req.max_steps, session_id=effective_sid):
            if event.get("type") == "start":
                event = {**event, "session_id": effective_sid}
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/folders")
async def list_folders(path: str = "."):
    """List folders in the given path for the folder browser."""
    try:
        base = Path(path).resolve()
        if not base.exists() or not base.is_dir():
            raise HTTPException(404, "Path not found")
        folders = sorted(
            f.name for f in base.iterdir()
            if f.is_dir() and not f.name.startswith(".")
        )
        parent = str(base.parent) if base != base.parent else None
        file_count = len([f for f in base.iterdir() if f.is_file()])
        return {
            "current": str(base),
            "parent": parent,
            "folders": folders,
            "files_count": file_count,
        }
    except PermissionError:
        raise HTTPException(403, "Permission denied")


@app.get("/api/screenshot/{path:path}")
async def get_screenshot(path: str):
    """Serve screenshot images."""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(SCREENSHOTS_ROOT):
        raise HTTPException(403, "Access denied")
    if not resolved.exists():
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(str(resolved), media_type="image/png")


@app.post("/api/index/stream")
async def index_directory_stream(req: IndexRequest):
    """Stream per-file indexing progress via SSE."""
    dir_path = Path(req.directory)
    if not dir_path.exists():
        raise HTTPException(404, f"Directory not found: {req.directory}")

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def callback(event: dict):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def event_stream():
        pipeline_task = asyncio.create_task(
            asyncio.to_thread(
                lambda: pipeline_directory(str(dir_path), "screenshots", req.reset, progress_callback=callback)
            )
        )
        while not pipeline_task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                continue
        # Drain any remaining events after task completes
        while not queue.empty():
            yield f"data: {json.dumps(queue.get_nowait())}\n\n"
        try:
            pipeline_task.result()
            # index_done already emitted by pipeline_directory via callback
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/graph")
async def document_graph():
    """Return the co-citation graph as nodes and edges."""
    return get_graph()


class ExportRequest(BaseModel):
    format: str = "md"
    content: str


@app.post("/api/export")
async def export_answer(req: ExportRequest):
    """Export an answer to Markdown or PDF."""
    if req.format == "pdf":
        data = await asyncio.to_thread(to_pdf, req.content)
        return FastAPIResponse(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=parserag-report.pdf"},
        )
    else:
        data = to_markdown(req.content)
        return FastAPIResponse(
            content=data.encode(),
            media_type="text/markdown",
            headers={"Content-Disposition": "attachment; filename=parserag-report.md"},
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
