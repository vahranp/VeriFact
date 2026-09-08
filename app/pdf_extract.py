"""
Turns a PDF into (page_number, text, char_windows) tuples.
No document-specific logic here at all -- just generic page extraction
and chunking so long pages don't blow the LLM context window.
"""
from dataclasses import dataclass

import fitz  # PyMuPDF

from app.config import MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS, MIN_PAGE_CHARS
from app.tables import layout_aware_text


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
    """Returns [(page_number, text), ...] for every non-trivial page.

    Table-like pages are reconstructed from word coordinates rather than
    taken in reading order (see app/tables.py): flattening a grid into a
    vertical token stream destroys the link between a row label and its
    values, which was the direct cause of facts whose quote was a row
    label while the value came from a cell elsewhere. Pages that don't
    look tabular are returned unchanged.
    """
    doc = fitz.open(pdf_path)
    try:
        pages = []
        for i in range(len(doc)):
            page = doc.load_page(i)
            text, _used_layout = layout_aware_text(page)
            text = text.strip()
            if len(text) >= MIN_PAGE_CHARS:
                pages.append((i + 1, text))
        return pages
    finally:
        doc.close()


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


# A selector can't ask for more pages than any real document has. Bounds
# the set this builds so a typo like "1-100000" can't allocate its way
# through memory before anything validates it.
MAX_SELECTABLE_PAGE = 10_000


class PageSpecError(ValueError):
    """A page selector that can't be honoured. Carries a message meant to
    be shown to whoever typed the selector."""


def parse_page_spec(spec: str) -> set[int]:
    """Parses a page selector like "1,3,7-10" into {1,3,7,8,9,10}. Used to
    let a caller target specific pages of a large PDF instead of paying to
    process every page.

    Raises PageSpecError with an explanatory message on anything malformed.
    This used to raise a bare ValueError from int() deep inside the parse,
    which is how a real upload of "24-24-24" got past the API, created a
    document row, queued a background job, and then died with
    "invalid literal for int() with base 10: '24-24'" -- a crashed job and
    a 500, instead of a rejected input.
    """
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2 or not all(b.strip() for b in bounds):
                raise PageSpecError(
                    f"'{part}' is not a valid page range -- write it as start-end, e.g. 7-10."
                )
            lo, hi = (_page_int(b, part) for b in bounds)
            if lo > hi:
                raise PageSpecError(
                    f"page range '{part}' runs backwards -- write it as {hi}-{lo}."
                )
            if hi > MAX_SELECTABLE_PAGE:
                raise PageSpecError(
                    f"page range '{part}' exceeds the maximum selectable page "
                    f"({MAX_SELECTABLE_PAGE})."
                )
            pages.update(range(lo, hi + 1))
        else:
            pages.add(_page_int(part, part))

    if not pages:
        raise PageSpecError(
            f"page selector '{spec}' does not select any pages -- "
            f"use something like 1,3,7-10."
        )
    return pages


def _page_int(token: str, context: str) -> int:
    token = token.strip()
    try:
        value = int(token)
    except ValueError:
        raise PageSpecError(
            f"'{context}' is not a valid page selector -- '{token}' is not a page number."
        ) from None
    if value < 1:
        raise PageSpecError(f"page numbers start at 1, but '{context}' asks for {value}.")
    if value > MAX_SELECTABLE_PAGE:
        raise PageSpecError(
            f"'{context}' exceeds the maximum selectable page ({MAX_SELECTABLE_PAGE})."
        )
    return value


def page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()
