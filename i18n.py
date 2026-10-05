"""Tiny translation layer: English source text → Norwegian (bokmål).

English text is the lookup key, so code stays readable and a missing
translation falls back to English. No Qt dependency: the PDF generators and
CLIs use this too. The language is set once at startup (GUI: from QSettings or
the system locale; CLI: ``--lang``) and only read afterwards, so worker threads
can call :func:`t` safely.

``tests/test_i18n.py`` fails if any ``t("…")`` call in the code has no
Norwegian entry here, or if an entry's ``{placeholders}`` don't match.
"""

from __future__ import annotations

LANGUAGES: dict[str, str] = {"nb": "Norsk", "en": "English"}
DEFAULT_LANGUAGE = "nb"

_current = DEFAULT_LANGUAGE


def set_language(code: str) -> None:
    global _current
    if code not in LANGUAGES:
        raise ValueError(f"Unsupported language {code!r}; choose from {', '.join(LANGUAGES)}")
    _current = code


def get_language() -> str:
    return _current


def language_from_locale(name: str) -> str:
    """Map a locale name such as ``nb_NO`` or ``en_US`` to a supported language."""
    return "nb" if name.split("_")[0].split("-")[0].lower() in ("nb", "nn", "no") else "en"


def t(text: str, /, lang: str | None = None, **fields: object) -> str:
    """Translate *text* into *lang* (default: the current language).

    ``{name}`` placeholders are filled from *fields*.
    """
    lang = lang or _current
    out = text if lang == "en" else NB.get(text, text)
    return out.format(**fields) if fields else out


def parse_cli_lang(args: list[str]) -> list[str]:
    """Strip ``--lang CODE`` / ``--lang=CODE`` from CLI *args*, apply it, return the rest."""
    rest: list[str] = []
    it = iter(args)
    for arg in it:
        if arg == "--lang":
            set_language(next(it, ""))
        elif arg.startswith("--lang="):
            set_language(arg.split("=", 1)[1])
        else:
            rest.append(arg)
    return rest


NB: dict[str, str] = {
    # ── Main window ──────────────────────────────────────────────────────────
    "Open data folder": "Åpne datamappe",
    "Cards": "Kort",
    "Lotto": "Lotto",
    "Sign Protocol": "Tegnprotokoll",
    "Restart needed": "Omstart trengs",
    "Restart AACdeck to switch language.": "Start AACdeck på nytt for å bytte språk.",
    # ── Shared across tabs ───────────────────────────────────────────────────
    "Sessions": "Samlinger",
    "+ New session": "+ Ny samling",
    "Select a session": "Velg en samling",
    "Page preview": "Forhåndsvisning",
    "No session selected": "Ingen samling valgt",
    "← Prev": "← Forrige",
    "Next →": "Neste →",
    "Page {page} / {total}": "Side {page} / {total}",
    "Rendering…": "Lager forhåndsvisning…",
    "Open PDF": "Åpne PDF",
    "Generate PDF": "Lag PDF",
    "Generating PDF…": "Lager PDF…",
    "Generation failed": "Kunne ikke lage PDF",
    "Saved: {path}": "Lagret: {path}",
    "Error: {msg}": "Feil: {msg}",
    "Already exists": "Finnes allerede",
    "Session '{name}' already exists.": "Samlingen «{name}» finnes allerede.",
    "No session": "Ingen samling",
    "Please select or create a session first.": "Velg eller lag en samling først.",
    "Rename…": "Gi nytt navn…",
    "Remove from session": "Fjern fra samlingen",
    "Name taken": "Navnet er i bruk",
    "'{name}' already exists.": "«{name}» finnes allerede.",
    "Delete '{name}' from this session?": "Slette «{name}» fra samlingen?",
    "Rename card": "Gi kortet nytt navn",
    "Card label:": "Tekst på kortet:",
    "Search": "Søk",
    "Searching…": "Søker…",
    "No results.": "Ingen treff.",
    "{n} result(s)": "{n} treff",
    "Add selected to session": "Legg valgte til i samlingen",
    "Add to session": "Legg til i samlingen",
    "Download error: {msg}": "Nedlastingsfeil: {msg}",
    # ── Cards tab ────────────────────────────────────────────────────────────
    "Add images…": "Legg til bilder…",
    "Add images": "Legg til bilder",
    "Images (*.jpg *.jpeg *.png *.webp *.avif)": "Bilder (*.jpg *.jpeg *.png *.webp *.avif)",
    "New session": "Ny samling",
    "Session name (e.g. 2026-04-skole):": "Navn på samlingen (f.eks. 2026-04-skole):",
    "Duplicate": "Dupliser",
    "Remove image": "Fjern bilde",
    "No cards in session": "Ingen kort i samlingen",
    # ── Pictogram search (Cards + Lotto) ─────────────────────────────────────
    "Search Pictograms": "Søk etter piktogrammer",
    "Search in English or Norwegian…": "Søk på norsk eller engelsk…",
    # ── Lotto tab ────────────────────────────────────────────────────────────
    "Generate PDFs": "Lag PDF-er",
    "Generating PDFs…": "Lager PDF-er…",
    "Open board PDF": "Åpne brett-PDF",
    "Open cut-out PDF": "Åpne utklipps-PDF",
    "New lotto session": "Ny lottosamling",
    "Session name (e.g. 2026-04-lotto-animals):": "Navn på samlingen (f.eks. 2026-04-lotto-dyr):",
    "Saved: {board} and {cutout}": "Lagret: {board} og {cutout}",
    "Remove card": "Fjern kort",
    # ── Sign Protocol tab ────────────────────────────────────────────────────
    "Search Signs": "Søk etter tegn",
    "Refresh database": "Oppdater tegnbasen",
    "Clear cache and reload sign data": "Tøm hurtigbufferen og hent tegndata på nytt",
    "Search for signs…": "Søk etter tegn…",
    "Generate Sign Protocol PDF": "Lag tegnprotokoll-PDF",
    "New sign protocol session": "Ny tegnprotokoll-samling",
    "Session name (e.g. 2026-04-signs-home):": "Navn på samlingen (f.eks. 2026-04-tegn-hjemme):",
    "Set description…": "Skriv beskrivelse…",
    "Set description": "Beskrivelse",
    "Describe how the child uses the sign:": "Beskriv hvordan barnet bruker tegnet:",
    "Cache cleared — next search will fetch fresh data.": "Hurtigbufferen er tømt – neste søk henter ferske data.",
    "Rename sign": "Gi tegnet nytt navn",
    "Sign label:": "Navn på tegnet:",
    "Remove sign": "Fjern tegn",
    "No signs in session": "Ingen tegn i samlingen",
    "Warning: could not save descriptions — {error}": "Advarsel: kunne ikke lagre beskrivelsene – {error}",
    # ── PDF text (generators + previews) ─────────────────────────────────────
    "Sign protocol — {name}": "Tegnprotokoll — {name}",
    "Sign": "Tegn",
    "Image": "Bilde",
    "How the child uses the sign": "Hvordan barnet bruker tegnet",
    "Illustrations: Statped / tegnbanken.no — CC BY-NC-ND 4.0": (
        "Illustrasjoner: Statped / tegnbanken.no — CC BY-NC-ND 4.0"
    ),
    "Pictograms: Sergio Palao / ARASAAC (arasaac.org), Government of Aragón — CC BY-NC-SA 4.0": (
        "Piktogrammer: Sergio Palao / ARASAAC (arasaac.org), Aragón-regjeringen — CC BY-NC-SA 4.0"
    ),
}
