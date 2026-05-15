"""
Export agent answers to Markdown or PDF.
Citations in [Source: file | Page N | path] format are extracted and
rendered as footnotes (Markdown) or embedded screenshots (PDF).
"""
import re
from datetime import datetime, timezone
from pathlib import Path

# Matches: [Source: filename | Page N | screenshot_path]
CITATION_RE = re.compile(
    r'\[Source:\s*([^|]+?)\s*\|\s*Page\s*(\d+)\s*\|\s*([^\]]+?)\]'
)


def _extract_citations(text: str) -> tuple[str, list[dict]]:
    """
    Replace [Source: …] tags with [N] footnote markers.
    Returns (cleaned_text, citations) where citations is an ordered list of
    {"index": N, "file": str, "page": str, "path": str}.
    Duplicate citation strings get the same index.
    """
    citation_map: dict[str, int] = {}  # raw citation string → index
    citations: list[dict] = []

    def replace_match(m: re.Match) -> str:
        file_name = m.group(1).strip()
        page = m.group(2).strip()
        path = m.group(3).strip()
        key = m.group(0)  # whole match as dedup key
        if key not in citation_map:
            idx = len(citations) + 1
            citation_map[key] = idx
            citations.append({
                "index": idx,
                "file": file_name,
                "page": page,
                "path": path,
            })
        return f"[{citation_map[key]}]"

    cleaned = CITATION_RE.sub(replace_match, text)
    return cleaned, citations


def to_markdown(text: str) -> str:
    """Convert an answer with inline citations to a Markdown document."""
    cleaned, citations = _extract_citations(text)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# ParseRAG Export",
        "",
        f"_Generated: {now}_",
        "",
        "---",
        "",
        cleaned,
    ]

    if citations:
        lines += ["", "---", "", "## Sources", ""]
        for c in citations:
            lines.append(f"[{c['index']}] **{c['file']}** — Page {c['page']}  ")
            lines.append(f"    Path: `{c['path']}`  ")
            lines.append("")

    return "\n".join(lines)


def _safe_text(s: str) -> str:
    """Encode string to latin-1, replacing unencodable characters."""
    return s.encode("latin-1", errors="replace").decode("latin-1")


def to_pdf(text: str) -> bytes:
    """
    Render an answer as a PDF using fpdf2.
    Citations are listed at the end; if the screenshot path exists on disk
    the page image is embedded below the citation heading.
    """
    from fpdf import FPDF

    cleaned, citations = _extract_citations(text)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)

    # Title
    pdf.cell(0, 10, _safe_text("ParseRAG Export"), ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, _safe_text(f"Generated: {now}"), ln=True)
    pdf.ln(4)

    # Horizontal rule approximation
    pdf.set_draw_color(180, 180, 180)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 170, pdf.get_y())
    pdf.ln(6)

    # Answer body
    pdf.set_font("Helvetica", "", 11)
    for paragraph in cleaned.split("\n"):
        if paragraph.strip():
            pdf.multi_cell(pdf.epw, 6, _safe_text(paragraph))
        else:
            pdf.ln(4)

    # Sources section
    if citations:
        pdf.ln(8)
        pdf.set_draw_color(180, 180, 180)
        pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 170, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Sources", ln=True)
        pdf.ln(2)

        for c in citations:
            pdf.set_font("Helvetica", "B", 11)
            heading = _safe_text(
                f"[{c['index']}] {c['file']} — Page {c['page']}"
            )
            pdf.cell(0, 7, heading, ln=True)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 5, _safe_text(f"Path: {c['path']}"), ln=True)
            pdf.ln(2)

            # Embed screenshot if it exists on disk
            img_path = c["path"]
            if Path(img_path).exists():
                try:
                    pdf.image(img_path, w=160)
                    pdf.ln(4)
                except Exception:
                    # Skip image on any error (unsupported format, etc.)
                    pass

    return bytes(pdf.output())
