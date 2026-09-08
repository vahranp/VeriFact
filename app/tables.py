"""
Layout-aware text reconstruction for table-like pages.

PyMuPDF's default `get_text("text")` emits words in reading order, which
for a table collapses a two-dimensional grid into a single vertical stream:

    FY24
    FY23
    FY22
    Male
    Female
    Total
    ...
    Permanent Employees
    35.69%
    45.15%
    36.36%
    ...

Every column relationship is gone. A model reading that has to guess which
of nine percentages belongs to "Permanent Employees / FY24 / Male", and it
guesses wrong often enough to matter -- this is the direct cause of the
evidence failures measured in app/evidence.py, where a fact's quote was a
table row *label* while its value came from a cell the flattening had
separated from it.

The fix here is deliberately the smallest one that recovers the lost
structure: words carry x/y coordinates, so rows can be recovered by
clustering on the vertical midpoint and cells by splitting on horizontal
gaps. That turns the above back into:

    FY24 | FY23 | FY22
    Male | Female | Total | Male | Female | Total | Male | Female | Total
    Permanent Employees | 35.69% | 45.15% | 36.36% | 41.93% | ...

which is alignable.

Two deliberate limits:

- This runs ONLY on pages that look tabular. Reconstructing prose would
  add pipe characters to ordinary sentences for no benefit and would
  change the text that quotes are grounded against on pages that never
  had a problem.
- It does not try to identify header rows, merge spanning cells, or model
  nested tables. A previous attempt using PyMuPDF's `find_tables()` was
  tested and rejected: with the default `lines` strategy it found only
  header rows on these documents (there are no ruling lines to detect),
  and with `strategy="text"` it returned a fragmented 69x21 grid that
  split words across cells ('Bu', 'siness', 'Responsibility'). A smaller,
  more predictable transformation beats a richer one that is wrong.

Grounding stays honest because the reconstruction is derived from the
PDF's own words: the model is shown this text and quotes are verified
against this same text, so "verbatim in the source" keeps its meaning.
"""
from dataclasses import dataclass

import fitz

# Words whose vertical midpoints fall within this many points are treated
# as being on the same visual row. Roughly half a line height at typical
# report font sizes -- large enough to survive baseline jitter, small
# enough not to merge adjacent rows.
ROW_TOLERANCE_PT = 3.0

# A horizontal gap wider than this many multiples of the local median
# character width is treated as a column boundary rather than a word space.
COLUMN_GAP_RATIO = 1.8

# Minimum absolute gap (points) before a break can be a column at all --
# stops ordinary wide word spacing from shattering a sentence into cells.
MIN_COLUMN_GAP_PT = 8.0

# A page is treated as tabular when at least this share of its non-trivial
# rows split into 3+ cells. Two-cell rows are common in prose (a heading
# and a page number), so the bar is set above that.
TABULAR_ROW_SHARE = 0.22
MIN_TABULAR_ROWS = 4


@dataclass
class Row:
    y: float
    cells: list[str]

    def render(self) -> str:
        return " | ".join(self.cells)


def _median(values: list[float], default: float) -> float:
    if not values:
        return default
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _split_into_cells(words: list[tuple[float, float, str]]) -> list[str]:
    """words: (x0, x1, text) already sorted by x. Splits on horizontal gaps
    that are wide relative to *this row's own* inter-word spacing, so a
    column break is judged against the row's typography rather than an
    absolute constant that would be wrong at a different font size."""
    if not words:
        return []
    if len(words) == 1:
        return [words[0][2].strip()]

    # Normal word spacing is estimated from character width, not from the
    # row's own gaps. Using the median gap is circular on a short row: with
    # two words the only gap IS the column gap, so it can never exceed a
    # threshold derived from itself. Character width is independent of how
    # the row happens to be laid out and scales correctly with font size.
    char_widths = [
        (x1 - x0) / len(text) for x0, x1, text in words if text and x1 > x0
    ]
    median_char = _median(char_widths, 5.0)
    threshold = max(MIN_COLUMN_GAP_PT, median_char * COLUMN_GAP_RATIO)

    cells: list[str] = []
    current = [words[0][2]]
    prev_x1 = words[0][1]
    for x0, x1, text in words[1:]:
        if x0 - prev_x1 > threshold:
            cells.append(" ".join(current))
            current = [text]
        else:
            current.append(text)
        prev_x1 = x1
    cells.append(" ".join(current))
    return [c.strip() for c in cells if c.strip()]


def find_gutters(words: list, page_width: float) -> list[float]:
    """Vertical whitespace bands that run down most of the page -- the
    gutters of a multi-column layout.

    Without this, a page holding two side-by-side tables produces rows that
    splice cells from both together, which is worse than the flattened text
    it replaces: it invents adjacency that isn't there. Detected by
    scanning x in coarse bands and finding those almost no word crosses.
    """
    if not words:
        return []
    band = 6.0
    n_bands = max(1, int(page_width / band))
    occupied = [0] * (n_bands + 1)
    for x0, _y0, x1, _y1, text, *_ in words:
        if not str(text).strip():
            continue
        for b in range(int(x0 / band), min(int(x1 / band) + 1, n_bands)):
            occupied[b] += 1

    total = sum(1 for w in words if str(w[4]).strip())
    if total < 30:
        return []

    # An empty band deep inside the text area, not the page margins.
    first = next((i for i, c in enumerate(occupied) if c), 0)
    last = next((i for i in range(len(occupied) - 1, -1, -1) if occupied[i]), 0)
    if last - first < 10:
        return []

    gutters, run_start = [], None
    for i in range(first + 4, last - 3):
        if occupied[i] == 0:
            run_start = i if run_start is None else run_start
        else:
            if run_start is not None and (i - run_start) >= 3:
                gutters.append(((run_start + i) / 2) * band)
            run_start = None
    return gutters


def reconstruct_rows(page) -> list[Row]:
    """Recovers visual rows and cells from a page's word coordinates.

    A multi-column page is split at its gutters and each column is
    reconstructed independently, then emitted in reading order -- left
    column fully, then right -- so rows never splice across the gutter.
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return []

    gutters = find_gutters(words, page.rect.width)
    if gutters:
        bounds = [0.0, *gutters, float(page.rect.width) + 1]
        out: list[Row] = []
        for lo, hi in zip(bounds, bounds[1:]):
            zone = [w for w in words if lo <= (w[0] + w[2]) / 2 < hi]
            if zone:
                out.extend(_rows_from_words(zone))
        return out

    return _rows_from_words(words)


def _rows_from_words(words: list) -> list[Row]:
    buckets: dict[int, list[tuple[float, float, str]]] = {}
    positions: dict[int, list[float]] = {}
    for x0, y0, x1, y1, text, *_ in words:
        if not str(text).strip():
            continue
        key = int(round(((y0 + y1) / 2) / ROW_TOLERANCE_PT))
        buckets.setdefault(key, []).append((x0, x1, str(text)))
        positions.setdefault(key, []).append((y0 + y1) / 2)

    rows: list[Row] = []
    for key in sorted(buckets):
        ordered = sorted(buckets[key], key=lambda w: w[0])
        cells = _split_into_cells(ordered)
        if cells:
            rows.append(Row(y=sum(positions[key]) / len(positions[key]), cells=cells))
    return rows


def looks_tabular(rows: list[Row]) -> bool:
    """Is this page enough of a table to be worth reconstructing?

    Judged on how many rows split into three or more cells. Prose rows
    split into one cell (or two, for a heading with a trailing page
    number), so a page of sentences scores near zero and is left alone.
    """
    substantive = [r for r in rows if any(len(c) > 1 for c in r.cells)]
    if len(substantive) < MIN_TABULAR_ROWS:
        return False
    multi = sum(1 for r in substantive if len(r.cells) >= 3)
    return (multi / len(substantive)) >= TABULAR_ROW_SHARE


def render_rows(rows: list[Row]) -> str:
    return "\n".join(r.render() for r in rows)


# table_context values -- see layout_aware_text. Exported so callers that
# need to reason about extraction risk (app/pdf_extract.py, app/
# fact_extraction.py) can compare against them rather than magic strings.
RECONSTRUCTED = "reconstructed"
PLAIN_NOT_TABULAR = "plain_not_tabular"
PLAIN_RECONSTRUCTION_REJECTED = "plain_reconstruction_rejected"


def layout_aware_text(page) -> tuple[str, str]:
    """Returns (text, table_context).

    table_context is one of:
      RECONSTRUCTED                 -- the page looked tabular and row/cell
                                        reconstruction succeeded; `text` is
                                        the reconstructed, pipe-delimited
                                        form.
      PLAIN_NOT_TABULAR             -- the page didn't look tabular at all;
                                        `text` is plain reading-order text,
                                        and there was no flattening risk to
                                        begin with.
      PLAIN_RECONSTRUCTION_REJECTED -- the page DID look tabular, but
                                        reconstruction was rejected (an
                                        exception, or it lost too much
                                        content) and fell back to plain
                                        text -- the SAME flattened-grid text
                                        that originally caused row-label
                                        extraction errors (see app/
                                        evidence.py), now with no structural
                                        help. This is the case worth a
                                        caller's attention: nothing about
                                        the fallback text itself signals the
                                        elevated risk unless table_context
                                        is checked.

    Falls back to the plain reading-order text whenever the page doesn't
    look tabular or reconstruction produced nothing usable, so a page that
    was working before is not disturbed.
    """
    plain = page.get_text("text").strip()
    try:
        rows = reconstruct_rows(page)
    except Exception:  # noqa: BLE001 - never let layout analysis break ingestion
        return plain, PLAIN_RECONSTRUCTION_REJECTED

    if not rows or not looks_tabular(rows):
        return plain, PLAIN_NOT_TABULAR

    rendered = render_rows(rows).strip()
    # A reconstruction that lost a meaningful amount of text is a bug in
    # the clustering, not an improvement -- prefer the known-good text.
    if len(rendered) < len(plain) * 0.6:
        return plain, PLAIN_RECONSTRUCTION_REJECTED
    return rendered, RECONSTRUCTED


def page_is_tabular(pdf_path: str, page_number: int) -> bool:
    """Convenience for scripts and tests. page_number is 1-indexed."""
    doc = fitz.open(pdf_path)
    try:
        return looks_tabular(reconstruct_rows(doc.load_page(page_number - 1)))
    finally:
        doc.close()
