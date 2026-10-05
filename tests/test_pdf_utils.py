"""Unit tests for the pure helpers in pdf_utils."""

from __future__ import annotations

import io
import json

from PIL import Image
from reportlab.pdfgen import canvas

import pytest

import pdf_utils

from pdf_utils import (
    IMAGE_EXTS,
    compute_grid,
    fit_label,
    fit_text,
    safe_stem,
    stem_to_label,
    to_rgb,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm


# ── safe_stem ────────────────────────────────────────────────────────────────


def test_safe_stem_spaces_to_underscores():
    assert safe_stem("stor bror") == "stor_bror"


def test_safe_stem_strips_windows_illegal_chars():
    assert safe_stem('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_safe_stem_collapses_and_trims_underscores():
    assert safe_stem("  a   b  ") == "a_b"


def test_safe_stem_empty_falls_back_to_image():
    assert safe_stem("") == "image"
    assert safe_stem("///") == "image"


def test_safe_stem_strips_control_chars():
    assert safe_stem("a\x00b\x1fc") == "a_b_c"


# ── stem_to_label ────────────────────────────────────────────────────────────


def test_stem_to_label_underscores_to_spaces():
    assert stem_to_label("stor_bror") == "stor bror"


def test_stem_to_label_strips_duplicate_counter():
    # GUI appends "__N" for duplicate drops; it must not leak into the label.
    assert stem_to_label("eple__2") == "eple"
    assert stem_to_label("stor_bror__10") == "stor bror"


def test_stem_to_label_single_underscore_not_a_counter():
    # A single underscore before digits is a normal word separator.
    assert stem_to_label("bilde_2") == "bilde 2"


# ── fit_text ─────────────────────────────────────────────────────────────────


def _canvas() -> canvas.Canvas:
    return canvas.Canvas(io.BytesIO())


def test_fit_text_returns_start_when_text_fits():
    c = _canvas()
    # Plenty of width → no shrinking needed.
    assert fit_text(c, "Helvetica-Bold", "hi", 10_000, 12) == 12


def test_fit_text_shrinks_for_long_text():
    c = _canvas()
    size = fit_text(c, "Helvetica-Bold", "a very long label indeed", 40, 12)
    assert size < 12


def test_fit_text_floors_at_4pt():
    c = _canvas()
    # Impossibly narrow width → hits the lower bound rather than going to zero.
    size = fit_text(c, "Helvetica-Bold", "wide word", 1, 12)
    assert size == 4


def test_fit_text_result_actually_fits_when_possible():
    c = _canvas()
    text, width = "label", 60
    size = fit_text(c, "Helvetica-Bold", text, width, 12)
    if size > 4:  # only guaranteed to fit when not floored
        assert c.stringWidth(text, "Helvetica-Bold", size) <= width


# ── fit_label ────────────────────────────────────────────────────────────────


def test_fit_label_returns_text_unchanged_when_it_fits():
    c = _canvas()
    text, size = fit_label(c, "Helvetica-Bold", "hi", 10_000, 12)
    assert text == "hi"
    assert size == 12


def test_fit_label_truncates_with_ellipsis_when_impossible():
    c = _canvas()
    # Too narrow even at the 4 pt floor → must truncate rather than overflow.
    text, size = fit_label(c, "Helvetica-Bold", "kommunikasjon", 8, 12)
    assert text.endswith("…")
    assert text != "kommunikasjon"
    # The truncated string actually fits the available width.
    assert c.stringWidth(text, "Helvetica-Bold", size) <= 8


def test_fit_label_never_returns_empty():
    c = _canvas()
    text, _ = fit_label(c, "Helvetica-Bold", "word", 0.1, 12)
    assert text  # at minimum a lone ellipsis


# ── compute_grid ─────────────────────────────────────────────────────────────


def test_compute_grid_matches_ask_card_layout():
    # Mirrors make_cards constants: 3 cols, 10 mm margin, 5 mm gap.
    page_w, page_h = A4
    label_area_h = 12 + 2 * (2.5 * mm)
    grid = compute_grid(page_w, page_h, 3, 10 * mm, 5 * mm, label_area_h)
    assert grid.card_size > 0
    assert grid.image_area_h > 0
    assert grid.cards_per_page == grid.rows_per_page * 3
    assert grid.cards_per_page >= 12  # 3 x 4 fits on A4


def test_compute_grid_rejects_oversized_margin():
    page_w, page_h = A4
    with pytest.raises(ValueError):
        compute_grid(page_w, page_h, 3, page_w, 5 * mm, 30)


def test_compute_grid_rejects_label_taller_than_card():
    page_w, page_h = A4
    # A label area larger than the card leaves negative image height.
    with pytest.raises(ValueError):
        compute_grid(page_w, page_h, 3, 10 * mm, 5 * mm, 10_000)


# ── to_rgb ───────────────────────────────────────────────────────────────────


def test_to_rgb_from_rgb_is_rgb():
    img = Image.new("RGB", (4, 4), (10, 20, 30))
    assert to_rgb(img).mode == "RGB"


def test_to_rgb_flattens_rgba_onto_white():
    # Fully transparent pixel should become white after flattening.
    img = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    out = to_rgb(img)
    assert out.mode == "RGB"
    assert out.getpixel((0, 0)) == (255, 255, 255)


def test_to_rgb_palette_with_transparency():
    img = Image.new("P", (2, 2))
    img.info["transparency"] = 0
    assert to_rgb(img).mode == "RGB"


# ── IMAGE_EXTS ───────────────────────────────────────────────────────────────


def test_image_exts_lowercase_with_dot():
    assert ".jpg" in IMAGE_EXTS
    assert ".avif" in IMAGE_EXTS
    # Stored normalised (lowercase, dotted); callers lower-case suffixes.
    assert all(e.startswith(".") and e == e.lower() for e in IMAGE_EXTS)


# ── ARASAAC attribution ──────────────────────────────────────────────────────


def test_uses_arasaac_false_without_manifest(tmp_path):
    img = tmp_path / "hund.png"
    img.write_bytes(b"")
    assert pdf_utils.uses_arasaac([img]) is False


def test_record_then_uses_arasaac(tmp_path):
    own = tmp_path / "mamma.jpg"
    picto = tmp_path / "hund.png"
    pdf_utils.record_arasaac_image(picto, 2517)
    pdf_utils.record_arasaac_image(tmp_path / "katt.png", 7114)
    assert pdf_utils.uses_arasaac([own, picto]) is True
    assert pdf_utils.uses_arasaac([own]) is False
    data = json.loads((tmp_path / pdf_utils.ARASAAC_MANIFEST).read_text())
    assert data == {"hund.png": 2517, "katt.png": 7114}


def test_record_arasaac_recovers_from_corrupt_manifest(tmp_path):
    (tmp_path / pdf_utils.ARASAAC_MANIFEST).write_text("not json")
    picto = tmp_path / "hund.png"
    pdf_utils.record_arasaac_image(picto, 1)
    assert pdf_utils.uses_arasaac([picto]) is True


def test_uses_arasaac_empty_list():
    assert pdf_utils.uses_arasaac([]) is False
