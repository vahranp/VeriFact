"""
Turns a PDF into (page_number, text, char_windows) tuples.
No document-specific logic here at all -- just generic page extraction
and chunking so long pages don't blow the LLM context window.
"""
from dataclasses import dataclass

import fitz  # PyMuPDF

from app.config import MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS, MIN_PAGE_CHARS


@dataclass
class Chunk:
    page_number: int  # 1-indexed, matches what a human would call "page N"
    text: str
    # The opening lines of the page this chunk came from, carried along for
    # chunks 2..N. Found by direct diagnosis, not speculation: a balance
    # sheet page states its denomination once at the top ("All amounts in
    # Indian Rupees in million, unless otherwise stated"), and chunking
    # split that header away from the data rows -- so the model reading a
    # later chunk had no way to know the figures were in millions and
    # recorded the unit as bare "INR". That single missing word silently
    # broke the downstream numeric comparison by a factor of 1e6.
    #
    # Kept OUT of `text` on purpose: `text` is what quotes are grounded
    # against, and what the model is told to extract from, so injecting
    # header text into it would both invite duplicate facts on every chunk
    # and let a quote "ground" against context the chunk doesn't really
    # contain.
    page_context: str = ""


# How much of a page's opening text to carry into its later chunks. Small
# on purpose -- enough for a denomination/period/title line, not enough to
# meaningfully eat into the chunk budget.
PAGE_CONTEXT_CHARS = 260


def extract_pages(pdf_path: str) -> list[tuple[int, str]]:
    """Returns [(page_number, raw_text), ...] for every non-trivial page."""
    doc = fitz.open(pdf_path)
    pages = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        text = page.get_text("text").strip()
        if len(text) >= MIN_PAGE_CHARS:
            pages.append((i + 1, text))
    doc.close()
    return pages


def _page_context(text: str) -> str:
    """The page's opening lines, trimmed at a line boundary so a
    denomination/title line isn't cut mid-word."""
    head = text[:PAGE_CONTEXT_CHARS]
    if len(text) > PAGE_CONTEXT_CHARS:
        cut = head.rfind("\n")
        if cut > 40:  # keep something meaningful rather than an empty stub
            head = head[:cut]
    return head.strip()


def chunk_page(page_number: int, text: str) -> list[Chunk]:
    """Splits an over-long page into overlapping windows. Most pages of the
    starter PDFs fit in one chunk; this only kicks in for very dense pages
    or, more importantly, for large PDFs we haven't seen yet.

    Chunks after the first carry the page's opening lines as separate
    `page_context` (see Chunk) so table denominations and headings stated
    once at the top of a page are still available when interpreting rows
    further down it."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [Chunk(page_number, text)]

    context = _page_context(text)
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHUNK_CHARS, len(text))
        # The first chunk already contains the header inline; later ones don't.
        chunks.append(Chunk(page_number, text[start:end], "" if start == 0 else context))
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP_CHARS
    return chunks


def extract_chunks(pdf_path: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_number, text in extract_pages(pdf_path):
        chunks.extend(chunk_page(page_number, text))
    return chunks


def parse_page_spec(spec: str) -> set[int]:
    """Parses a page selector like "1,3,7-10" into {1,3,7,8,9,10}. Used to
    let a caller target specific pages of a large PDF instead of paying to
    process every page -- a generically useful capability for big
    documents, not just for bounding demo cost."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.update(range(int(lo), int(hi) + 1))
        else:
            pages.add(int(part))
    return pages


def page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    n = len(doc)
    doc.close()
    return n
