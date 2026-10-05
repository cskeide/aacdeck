"""Tests for the i18n layer, including a scan that every t("…") call is translated."""

from __future__ import annotations

import ast
import string
from pathlib import Path

import pytest

import i18n
from i18n import language_from_locale, parse_cli_lang, set_language, t

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_language():
    before = i18n.get_language()
    yield
    set_language(before)


def _translated_literals() -> dict[str, str]:
    """Every string literal passed as the first argument to t(), → 'file:line'."""
    found: dict[str, str] = {}
    for path in sorted(ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "t"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.setdefault(node.args[0].value, f"{path.name}:{node.lineno}")
    return found


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_t_call_has_a_norwegian_translation():
    missing = {k: v for k, v in _translated_literals().items() if k not in i18n.NB}
    assert not missing, f"add these to i18n.NB: {missing}"


def test_no_stale_translations():
    used = _translated_literals()
    stale = sorted(k for k in i18n.NB if k not in used)
    assert not stale, f"i18n.NB entries no t() call uses: {stale}"


def test_placeholders_match():
    bad = {k: v for k, v in i18n.NB.items() if _fields(k) != _fields(v)}
    assert not bad


def test_t_translates_and_formats():
    set_language("nb")
    assert t("Search") == "Søk"
    assert t("Page {page} / {total}", page=2, total=5) == "Side 2 / 5"
    assert t("Search", lang="en") == "Search"
    set_language("en")
    assert t("Page {page} / {total}", page=2, total=5) == "Page 2 / 5"
    assert t("Untranslated text") == "Untranslated text"


def test_set_language_rejects_unknown():
    with pytest.raises(ValueError):
        set_language("sv")


@pytest.mark.parametrize(
    "locale,expected",
    [("nb_NO", "nb"), ("nn_NO", "nb"), ("no", "nb"), ("en_US", "en"), ("sv_SE", "en"), ("C", "en")],
)
def test_language_from_locale(locale, expected):
    assert language_from_locale(locale) == expected


def test_parse_cli_lang():
    assert parse_cli_lang(["--lang", "en", "sessions/x"]) == ["sessions/x"]
    assert i18n.get_language() == "en"
    assert parse_cli_lang(["sessions/x", "--lang=nb"]) == ["sessions/x"]
    assert i18n.get_language() == "nb"
    with pytest.raises(ValueError):
        parse_cli_lang(["--lang", "xx", "sessions/x"])
