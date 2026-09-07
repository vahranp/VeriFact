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


def chunk_page(page_number: int, text: str) -> list[Chunk]:
    """Splits an over-long page into overlapping windows. Most pages of the
    starter PDFs fit in one chunk; this only kicks in for very dense pages
    or, more importantly, for large PDFs we haven't seen yet."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [Chunk(page_number, text)]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHUNK_CHARS, len(text))
        chunks.append(Chunk(page_number, text[start:end]))
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
