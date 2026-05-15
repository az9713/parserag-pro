"""
Processing pipeline: Parse PDF → Chunk → Screenshot → Embed → Store in Milvus
"""
import os
from pathlib import Path

from chonkie import SemanticChunker
from google import genai
from liteparse import LiteParse
from pymilvus import MilvusClient, DataType

SCREENSHOT_DIR = "screenshots"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "3072"))
COLLECTION_NAME = "parserag"
MILVUS_DB_PATH = os.getenv("MILVUS_DB_PATH", "./milvus_lite.db")

# On Windows, Python subprocess cannot run .CMD files by name — resolve the
# full path to npx.CMD so LiteParse can find it via subprocess.
import shutil as _shutil
_npx = _shutil.which("npx") or "npx"
_parser = LiteParse(cli_path=f"{_npx} @llamaindex/liteparse")
# SemanticChunker downloads a sentence-embedding model (~50 MB) on first use.
# The model is cached in the Hugging Face hub cache after the initial download.
_chunker = SemanticChunker(chunk_size=512)

# Module-level singletons — initialised once, reused across all calls.
# Note: lazy init has a benign race condition under concurrent asyncio.to_thread
# use. Safe for single-user local deployment; add threading.Lock for multi-user.
_genai_client: genai.Client | None = None
_milvus_client: MilvusClient | None = None


def get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


def _reset_collection() -> None:
    """Drop and cleanly recreate the Milvus collection on Windows.

    drop_collection() leaves a locked manifest.json that causes WinError 183
    when create_collection() tries to write the same file. Closing the client
    releases the file lock so the collection directory can be deleted safely.
    """
    import shutil
    global _milvus_client
    if _milvus_client is not None:
        try:
            _milvus_client.close()
        except Exception:
            pass
        _milvus_client = None
    # Delete only the collection directory — not the whole database.
    collection_dir = Path(MILVUS_DB_PATH) / "collections" / COLLECTION_NAME
    if collection_dir.exists():
        shutil.rmtree(collection_dir, ignore_errors=True)
    print("Reset: collection cleared.")


def get_milvus_client() -> MilvusClient:
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(uri=MILVUS_DB_PATH)
    return _milvus_client


def parse_pdf(file_path: str) -> dict[int, str]:
    """Extract layout-aware text per page using LiteParse."""
    result = _parser.parse(file_path)
    pages = {}
    for page in result.pages:
        pages[page.pageNum] = page.text
    return pages


def take_screenshots(file_path: str, out_dir: str = SCREENSHOT_DIR) -> dict[int, str]:
    """Render page screenshots using LiteParse."""
    stem = Path(file_path).stem
    doc_dir = os.path.join(out_dir, stem)
    os.makedirs(doc_dir, exist_ok=True)
    result = _parser.screenshot(file_path, output_dir=doc_dir)
    screenshot_paths = {}
    for r in result.screenshots:
        screenshot_paths[r.page_num] = r.image_path
    return screenshot_paths


def chunk_text(text: str) -> list[str]:
    """Split text into semantically coherent chunks."""
    chunks = _chunker.chunk(text)
    return [c.text for c in chunks] if chunks else [text]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts using Google Gemini embedding model."""
    client = get_genai_client()
    embeddings = []
    for i in range(0, len(texts), 5):
        batch = [t if t.strip() else "empty page" for t in texts[i:i+5]]
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
        )
        for emb in result.embeddings:
            embeddings.append(emb.values)
    return embeddings


def ensure_collection(client: MilvusClient):
    """Create the parserag collection if it doesn't exist."""
    if client.has_collection(COLLECTION_NAME):
        return

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("source_file", DataType.VARCHAR, max_length=1024)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("image_path", DataType.VARCHAR, max_length=1024)
    schema.add_field("page_num", DataType.INT64)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="FLAT",
        metric_type="COSINE",
    )

    client.create_collection(
        collection_name=COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )
    print(f"  Created collection '{COLLECTION_NAME}'.")


def insert_data(
    client: MilvusClient,
    source_file: str,
    chunks: list[str],
    embeddings: list[list[float]],
    image_paths: list[str],
    page_nums: list[int],
):
    """Insert chunk data into Milvus."""
    data = []
    for i in range(len(chunks)):
        data.append({
            "source_file": source_file,
            "text": chunks[i],
            "image_path": image_paths[i],
            "page_num": page_nums[i],
            "vector": embeddings[i],
        })
    result = client.insert(collection_name=COLLECTION_NAME, data=data)
    print(f"  Inserted {result['insert_count']} records.")


def pipeline(file_path: str, screenshot_dir: str = SCREENSHOT_DIR, reset: bool = False, *, progress_callback=None):
    """Process a single PDF: parse → chunk → screenshot → embed → store."""
    print(f"Processing: {file_path}")
    file_name = Path(file_path).name

    if reset:
        _reset_collection()
    client = get_milvus_client()
    ensure_collection(client)

    # Step 1: Parse
    if progress_callback:
        progress_callback({"type": "file_stage", "file": file_name, "stage": "parsing"})
    print("  Parsing PDF with LiteParse...")
    pages = parse_pdf(file_path)
    print(f"  Extracted text from {len(pages)} pages.")

    # Step 2: Screenshots (internal — no separate progress event)
    print("  Taking page screenshots...")
    screenshot_paths = take_screenshots(file_path, screenshot_dir)
    print(f"  Saved {len(screenshot_paths)} screenshots.")

    # Step 3: Chunk
    if progress_callback:
        progress_callback({"type": "file_stage", "file": file_name, "stage": "chunking"})
    all_chunks = []
    all_image_paths = []
    all_page_nums = []
    for page_num, text in pages.items():
        chunks = chunk_text(text)
        img_path = screenshot_paths.get(page_num, "")
        for chunk in chunks:
            all_chunks.append(chunk)
            all_image_paths.append(img_path)
            all_page_nums.append(page_num)
    print(f"  Created {len(all_chunks)} chunks.")

    # Step 4: Embed
    if progress_callback:
        progress_callback({"type": "file_stage", "file": file_name, "stage": "embedding"})
    print("  Generating embeddings...")
    embeddings = embed_texts(all_chunks)
    print(f"  Generated {len(embeddings)} embeddings.")

    # Step 5: Store
    if progress_callback:
        progress_callback({"type": "file_stage", "file": file_name, "stage": "storing"})
    source = Path(file_path).name
    insert_data(client, source, all_chunks, embeddings, all_image_paths, all_page_nums)
    print(f"  Done: {source}")

    return len(all_chunks)


def pipeline_directory(directory: str, screenshot_dir: str = SCREENSHOT_DIR, reset: bool = True, *, progress_callback=None) -> dict:
    """Process all PDFs in a directory. Returns {"ok": [...], "errors": {...}}."""
    pdf_files = sorted(Path(directory).glob("**/*.pdf"))
    results: dict = {"ok": [], "errors": {}}

    if not pdf_files:
        print(f"No PDF files found in {directory}")
        if progress_callback:
            progress_callback({"type": "index_done", "files_processed": 0, "files_failed": 0, "chunks": 0})
        return results

    print(f"Found {len(pdf_files)} PDF files in {directory}")
    if progress_callback:
        progress_callback({"type": "index_start", "total": len(pdf_files)})

    if reset:
        _reset_collection()
    client = get_milvus_client()
    ensure_collection(client)

    for i, pdf in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] {pdf.name}")
        if progress_callback:
            progress_callback({"type": "file_start", "file": pdf.name, "index": i, "total": len(pdf_files)})
        try:
            n = pipeline(str(pdf), screenshot_dir, reset=False, progress_callback=progress_callback)
            results["ok"].append({"file": pdf.name, "chunks": n})
            if progress_callback:
                progress_callback({"type": "file_done", "file": pdf.name, "chunks": n})
        except Exception as e:
            print(f"  ERROR: {e}")
            results["errors"][pdf.name] = str(e)
            if progress_callback:
                progress_callback({"type": "file_error", "file": pdf.name, "error": str(e)})

    total_chunks = sum(f["chunks"] for f in results["ok"])
    stats = client.get_collection_stats(COLLECTION_NAME)
    print(f"\nIndexing complete!")
    print(f"  Files processed: {len(results['ok'])}")
    print(f"  Files failed: {len(results['errors'])}")
    print(f"  Total chunks: {total_chunks}")
    print(f"  Collection rows: {stats.get('row_count', 0)}")

    if progress_callback:
        progress_callback({
            "type": "index_done",
            "files_processed": len(results["ok"]),
            "files_failed": len(results["errors"]),
            "chunks": total_chunks,
        })

    return results
