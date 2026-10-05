"""Smoke tests for the three PDF generators.

These exercise the grid geometry, pagination, and file-output paths without any
network access: a session folder is populated with generated images and each
generator is run against it.  Output is asserted to be a valid, non-empty PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import make_cards
import make_lotto
import make_tegnprotokoll


def _make_session(tmp_path: Path, name: str, n: int = 15) -> Path:
    """Create a session folder with *n* small PNG images."""
    session = tmp_path / name
    session.mkdir()
    for i in range(n):
        Image.new("RGB", (32, 32), (i * 10 % 256, 80, 120)).save(
            session / f"bilde_{i}.png"
        )
    return session


def _assert_pdf(path: Path) -> None:
    assert path.exists(), f"{path} was not created"
    data = path.read_bytes()
    assert data.startswith(b"%PDF"), "output is not a PDF"
    assert len(data) > 500, "PDF is suspiciously small"


# ── make_cards ───────────────────────────────────────────────────────────────


def test_make_cards_produces_pdf(tmp_path):
    session = _make_session(tmp_path, "familie")
    out = make_cards.make_cards(str(session), output_dir=tmp_path / "out")
    _assert_pdf(out)
    assert out.name == "familie.pdf"


def test_make_cards_paginates(tmp_path):
    # 30 images at 3x4 = 12/page must span 3 pages without error.
    session = _make_session(tmp_path, "skole", n=30)
    out = make_cards.make_cards(str(session), output_dir=tmp_path / "out")
    _assert_pdf(out)


def test_make_cards_rejects_empty_session(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        make_cards.make_cards(str(empty), output_dir=tmp_path / "out")


def test_make_cards_rejects_missing_dir(tmp_path):
    with pytest.raises(ValueError):
        make_cards.make_cards(str(tmp_path / "nope"), output_dir=tmp_path / "out")


# ── make_lotto ───────────────────────────────────────────────────────────────


def test_make_board_and_cutout(tmp_path):
    session = _make_session(tmp_path, "dyr")
    board = make_lotto.make_board_pdf(str(session), output_dir=tmp_path / "out")
    cutout = make_lotto.make_cutout_pdf(str(session), output_dir=tmp_path / "out")
    _assert_pdf(board)
    _assert_pdf(cutout)
    assert board.name == "dyr_board.pdf"
    assert cutout.name == "dyr_cutout.pdf"


def test_make_lotto_rejects_empty_session(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        make_lotto.make_board_pdf(str(empty), output_dir=tmp_path / "out")


def test_make_board_and_cutout_single_pass(tmp_path):
    session = _make_session(tmp_path, "kombi")
    board, cutout = make_lotto.make_board_and_cutout_pdf(
        str(session), output_dir=tmp_path / "out"
    )
    _assert_pdf(board)
    _assert_pdf(cutout)
    assert board.name == "kombi_board.pdf"
    assert cutout.name == "kombi_cutout.pdf"


def test_make_lotto_survives_broken_image(tmp_path, capsys):
    session = _make_session(tmp_path, "delvis", n=2)
    (session / "ødelagt.png").write_bytes(b"not a real png")
    board = make_lotto.make_board_pdf(str(session), output_dir=tmp_path / "out")
    _assert_pdf(board)
    # A broken image warns rather than aborting the whole PDF.
    assert "ødelagt.png" in capsys.readouterr().out


# ── make_tegnprotokoll ───────────────────────────────────────────────────────


def test_make_tegnprotokoll_produces_pdf(tmp_path):
    session = _make_session(tmp_path, "tegn")
    out = make_tegnprotokoll.make_tegnprotokoll(
        str(session), output_dir=tmp_path / "out"
    )
    _assert_pdf(out)
    assert out.name == "tegn_tegnprotokoll.pdf"


def test_make_tegnprotokoll_reads_descriptions(tmp_path):
    session = _make_session(tmp_path, "tegn2", n=3)
    (session / "descriptions.json").write_text(
        '{"bilde_0": "bruker tegnet selv"}', encoding="utf-8"
    )
    out = make_tegnprotokoll.make_tegnprotokoll(
        str(session), output_dir=tmp_path / "out"
    )
    _assert_pdf(out)


def test_make_tegnprotokoll_survives_bad_descriptions(tmp_path, capsys):
    session = _make_session(tmp_path, "tegn3", n=2)
    (session / "descriptions.json").write_text("{not valid json", encoding="utf-8")
    out = make_tegnprotokoll.make_tegnprotokoll(
        str(session), output_dir=tmp_path / "out"
    )
    _assert_pdf(out)
    # A malformed sidecar must warn rather than fail silently.
    assert "descriptions.json" in capsys.readouterr().out


def test_make_tegnprotokoll_rejects_empty_session(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        make_tegnprotokoll.make_tegnprotokoll(str(empty), output_dir=tmp_path / "out")


def test_make_cards_with_arasaac_manifest(tmp_path):
    # A session mixing own photos with a recorded pictogram gets the credit path.
    from pdf_utils import record_arasaac_image

    session = _make_session(tmp_path, "blandet", n=14)
    record_arasaac_image(session / "bilde_3.png", 42)
    out = make_cards.make_cards(str(session), output_dir=tmp_path / "out")
    _assert_pdf(out)


def test_make_tegnprotokoll_in_english(tmp_path):
    import i18n

    session = _make_session(tmp_path, "tegn_en", n=3)
    try:
        i18n.set_language("en")
        out = make_tegnprotokoll.make_tegnprotokoll(str(session), output_dir=tmp_path / "out")
    finally:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)
    _assert_pdf(out)
