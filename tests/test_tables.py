"""Tests for layout-aware table reconstruction.

The risk with this feature is not that it fails to help -- it is that it
fires on prose and mangles pages that were working. So these tests pin the
guard conditions as hard as the reconstruction itself.

Built on synthetic PDFs generated in-memory with PyMuPDF, so they assert
behaviour on known geometry rather than on whatever the starter documents
happen to contain.
"""
import fitz

from app.tables import (
    PLAIN_NOT_TABULAR, PLAIN_RECONSTRUCTION_REJECTED, RECONSTRUCTED,
    Row, _split_into_cells, find_gutters, layout_aware_text, looks_tabular,
    reconstruct_rows,
)


def _pdf(draw):
    """Builds a one-page PDF in memory and returns its page."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    draw(page)
    # Round-trip through bytes so we read it the way a real upload would.
    reopened = fitz.open("pdf", doc.tobytes())
    doc.close()
    return reopened, reopened.load_page(0)


def _table_page():
    def draw(page):
        page.insert_text((72, 100), "Year", fontsize=10)
        page.insert_text((220, 100), "Male", fontsize=10)
        page.insert_text((330, 100), "Female", fontsize=10)
        page.insert_text((440, 100), "Total", fontsize=10)
        for i, (year, m, f, t) in enumerate([
            ("FY2023", "500", "300", "800"),
            ("FY2024", "700", "400", "1100"),
            ("FY2025", "650", "450", "1100"),
        ]):
            y = 130 + i * 24
            page.insert_text((72, y), year, fontsize=10)
            page.insert_text((220, y), m, fontsize=10)
            page.insert_text((330, y), f, fontsize=10)
            page.insert_text((440, y), t, fontsize=10)
    return _pdf(draw)


def _prose_page():
    def draw(page):
        text = ("The company reported steady growth across all divisions during the "
                "period under review. Management attributes this to disciplined cost "
                "control and continued investment in network capacity. The board has "
                "recommended no change to the dividend policy at this time.")
        page.insert_textbox(fitz.Rect(72, 100, 520, 300), text, fontsize=11)
    return _pdf(draw)


class TestRowReconstruction:
    def test_a_row_label_stays_with_its_values(self):
        """The whole point: flattening puts the label and its numbers on
        separate lines, so the model has to guess which value is whose."""
        doc, page = _table_page()
        try:
            rows = reconstruct_rows(page)
            rendered = [r.render() for r in rows]
            fy2024 = next(r for r in rendered if "FY2024" in r)
            assert "700" in fy2024 and "400" in fy2024 and "1100" in fy2024
        finally:
            doc.close()

    def test_columns_are_separated_into_cells(self):
        doc, page = _table_page()
        try:
            rows = reconstruct_rows(page)
            fy2023 = next(r for r in rows if "FY2023" in r.render())
            assert fy2023.cells == ["FY2023", "500", "300", "800"]
        finally:
            doc.close()

    def test_header_row_is_preserved_in_order(self):
        doc, page = _table_page()
        try:
            header = next(r for r in reconstruct_rows(page) if "Male" in r.render())
            assert header.cells == ["Year", "Male", "Female", "Total"]
        finally:
            doc.close()

    def test_rows_come_back_in_visual_order(self):
        doc, page = _table_page()
        try:
            rendered = [r.render() for r in reconstruct_rows(page)]
            years = [r for r in rendered if r.startswith("FY")]
            assert years[0].startswith("FY2023") and years[-1].startswith("FY2025")
        finally:
            doc.close()


class TestProseIsLeftAlone:
    """A page of sentences must not be rewritten with pipe characters."""

    def test_prose_is_not_detected_as_tabular(self):
        doc, page = _prose_page()
        try:
            assert looks_tabular(reconstruct_rows(page)) is False
        finally:
            doc.close()

    def test_prose_text_is_returned_unchanged(self):
        doc, page = _prose_page()
        try:
            text, table_context = layout_aware_text(page)
            assert table_context == PLAIN_NOT_TABULAR
            assert text == page.get_text("text").strip()
            assert "|" not in text
        finally:
            doc.close()

    def test_a_table_page_does_use_layout(self):
        doc, page = _table_page()
        try:
            _text, table_context = layout_aware_text(page)
            assert table_context == RECONSTRUCTED
        finally:
            doc.close()

    def test_an_empty_page_is_safe(self):
        doc = fitz.open()
        doc.new_page()
        page = doc.load_page(0)
        try:
            assert reconstruct_rows(page) == []
            assert layout_aware_text(page) == ("", PLAIN_NOT_TABULAR)
        finally:
            doc.close()


class TestMultiColumnPages:
    """Two side-by-side tables must not splice cells across the gutter --
    that invents adjacency the page never had, which is worse than the
    flattened text it replaces."""

    def test_a_gutter_is_detected(self):
        def draw(page):
            for i in range(6):
                y = 100 + i * 22
                page.insert_text((60, y), f"Left{i} 1 2 3", fontsize=10)
                page.insert_text((380, y), f"Right{i} 4 5 6", fontsize=10)
        doc, page = _pdf(draw)
        try:
            assert find_gutters(page.get_text("words"), page.rect.width)
        finally:
            doc.close()

    def test_rows_do_not_splice_across_the_gutter(self):
        def draw(page):
            for i in range(6):
                y = 100 + i * 22
                page.insert_text((60, y), f"LeftRow{i} 1 2 3", fontsize=10)
                page.insert_text((380, y), f"RightRow{i} 4 5 6", fontsize=10)
        doc, page = _pdf(draw)
        try:
            for row in reconstruct_rows(page):
                rendered = row.render()
                assert not ("LeftRow" in rendered and "RightRow" in rendered), rendered
        finally:
            doc.close()

    def test_a_single_column_page_has_no_gutter(self):
        doc, page = _prose_page()
        try:
            assert find_gutters(page.get_text("words"), page.rect.width) == []
        finally:
            doc.close()


class TestCellSplitting:
    def test_a_single_word_is_one_cell(self):
        assert _split_into_cells([(10.0, 40.0, "Revenue")]) == ["Revenue"]

    def test_no_words_yields_no_cells(self):
        assert _split_into_cells([]) == []

    def test_normal_word_spacing_does_not_split(self):
        words = [(10.0, 40.0, "Total"), (43.0, 70.0, "revenue")]
        assert _split_into_cells(words) == ["Total revenue"]

    def test_a_wide_gap_splits(self):
        words = [(10.0, 40.0, "Revenue"), (200.0, 230.0, "500")]
        assert _split_into_cells(words) == ["Revenue", "500"]


class TestContentPreservation:
    def test_reconstruction_does_not_drop_content(self):
        """A reconstruction that loses text is a clustering bug, not an
        improvement -- layout_aware_text falls back rather than ship it."""
        doc, page = _table_page()
        try:
            text, _ = layout_aware_text(page)
            for token in ("FY2023", "FY2024", "FY2025", "500", "1100", "Female"):
                assert token in text
        finally:
            doc.close()


class TestTableContextFlagsTheRiskyFallback:
    """table_context (see app/db.py's facts.table_context, app/pdf_extract.py,
    app/fact_extraction.py) exists specifically to distinguish "this page
    was never tabular" from "this page WAS tabular but reconstruction was
    rejected" -- the second is the risky one: the model sees the exact
    flattened-grid text that originally caused row-label extraction
    errors, with no signal that anything is different about this page."""

    def test_a_content_losing_reconstruction_is_flagged_rejected_not_silently_plain(self, monkeypatch):
        import app.tables as tables_module
        doc, page = _table_page()
        try:
            monkeypatch.setattr(tables_module, "render_rows", lambda rows: "x")
            text, table_context = layout_aware_text(page)
            assert table_context == PLAIN_RECONSTRUCTION_REJECTED
            assert text == page.get_text("text").strip()
        finally:
            doc.close()

    def test_an_exception_during_reconstruction_is_flagged_rejected(self, monkeypatch):
        """The exception handler can't know whether the page was tabular
        (the exception happens before looks_tabular runs) -- flagged as
        the risky case rather than assumed safe, the more conservative
        of the two."""
        import app.tables as tables_module
        doc, page = _table_page()
        try:
            def boom(_page):
                raise RuntimeError("simulated layout failure")
            monkeypatch.setattr(tables_module, "reconstruct_rows", boom)
            text, table_context = layout_aware_text(page)
            assert table_context == PLAIN_RECONSTRUCTION_REJECTED
            assert text == page.get_text("text").strip()
        finally:
            doc.close()

    def test_row_render_joins_cells_readably(self):
        assert Row(y=1.0, cells=["A", "1", "2"]).render() == "A | 1 | 2"
