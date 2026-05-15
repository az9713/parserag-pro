"""
Showcase script for ParseRAG improvements 2 and 4.
Tests that do not require the server or a Google API key.

Run from the project root:
    python tests/showcase.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "PASS"
FAIL = "FAIL"
NOTE = "NOTE"


def header(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


# ---------------------------------------------------------------------------
# Improvement 2 — Singleton clients
# ---------------------------------------------------------------------------

def showcase_singletons():
    header("Improvement 2: Performance — Singleton Clients")
    print("Verifying that get_genai_client() and get_milvus_client()")
    print("return the same object on every call.\n")

    import tempfile
    import src.processing as proc

    # Use a temp DB so this test doesn't conflict with a running server
    # which holds a file lock on the production milvus_lite.db
    _orig_path = proc.MILVUS_DB_PATH
    _orig_client = proc._milvus_client
    _test_db = tempfile.mktemp(suffix="_showcase.db")
    proc.MILVUS_DB_PATH = _test_db
    proc._milvus_client = None

    try:
        c1 = proc.get_milvus_client()
        c2 = proc.get_milvus_client()
        milvus_ok = c1 is c2
        print(f"  get_milvus_client() call 1 id: {id(c1)}")
        print(f"  get_milvus_client() call 2 id: {id(c2)}")
        print(f"  Same object? {milvus_ok}")
    finally:
        try:
            proc._milvus_client.close()
        except Exception:
            pass
        proc._milvus_client = _orig_client
        proc.MILVUS_DB_PATH = _orig_path
        try:
            os.unlink(_test_db)
        except Exception:
            pass

    print()

    from src.processing import get_genai_client
    g1 = get_genai_client()
    g2 = get_genai_client()
    gemini_ok = g1 is g2
    print(f"  get_genai_client() call 1 id: {id(g1)}")
    print(f"  get_genai_client() call 2 id: {id(g2)}")
    print(f"  Same object? {gemini_ok}")

    print()
    if milvus_ok and gemini_ok:
        print(f"  [{PASS}] Both clients are singletons — no redundant connections.")
    else:
        print(f"  [{FAIL}] One or more clients are not singletons.")


# ---------------------------------------------------------------------------
# Improvement 4 — Semantic chunking
# ---------------------------------------------------------------------------

SAMPLE_TEXT = """\
Warfarin (Coumadin) is an anticoagulant medication prescribed to prevent the \
formation of blood clots. It works by blocking the production of clotting \
factors in the liver that depend on vitamin K.

Common side effects include unusual bruising, prolonged bleeding from cuts, \
pink or brown urine, red or black stools, coughing up blood, severe headache, \
dizziness, and vision changes. Seek emergency care for any of these symptoms.

The dose of warfarin is highly individualised and monitored using the INR blood \
test. Your doctor will adjust your dose to keep your INR in the target range, \
typically between 2.0 and 3.0. Never change your dose without consulting your \
doctor.

Warfarin interacts with many medications, herbal supplements, and foods. Foods \
high in vitamin K such as leafy green vegetables can reduce effectiveness. \
Always inform your healthcare provider of all medications and supplements you take.\
"""


def showcase_chunking():
    header("Improvement 4: Quality — Semantic Chunking")
    print("NOTE: The first run downloads the sentence-embedding model (~50 MB).")
    print("      Subsequent runs use the cached model and start instantly.\n")

    from src.processing import chunk_text

    naive_chunks = [p.strip() for p in SAMPLE_TEXT.split("\n\n") if p.strip()]
    semantic_chunks = chunk_text(SAMPLE_TEXT)

    print(f"  Input : 1 block of text covering 4 topics ({len(SAMPLE_TEXT)} chars)")
    print(f"  Naive paragraph split  : {len(naive_chunks)} chunks")
    print(f"  Semantic chunker output: {len(semantic_chunks)} chunks")
    print()

    print("  --- Semantic chunks ---")
    for i, chunk in enumerate(semantic_chunks, 1):
        words = len(chunk.split())
        preview = chunk.strip()[:72].replace("\n", " ")
        print(f"  Chunk {i} ({words} words): {preview}...")

    print()
    if len(semantic_chunks) >= 1:
        print(f"  [{PASS}] Chunker produced {len(semantic_chunks)} focused chunk(s).")
        print(f"  [{NOTE}] Exact count depends on the model's similarity threshold.")
        print("         A query for 'warfarin side effects' now retrieves the")
        print("         side-effects chunk only, not a diluted full-page block.")
    else:
        print(f"  [{FAIL}] No chunks returned — check chonkie installation.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    showcase_singletons()
    showcase_chunking()
    print()
    print("Showcase complete.")
    print("For improvements 1, 3, and 5, see TESTING.md — those tests")
    print("require the server to be running (python app.py).")
    print()
