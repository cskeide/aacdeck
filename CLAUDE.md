# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Python desktop tool that generates print-ready A4 PDFs for AAC/ASK (alternativ og supplerende kommunikasjon) materials in Norwegian: picture cards, lotto boards, and sign-language protocols. Single flat package — no monorepo.

## Commands

Dev work happens in a project venv at `.venv/` (gitignored). **`ruff`, `pytest`, and the runtime deps are not installed system-wide and are not on `PATH`** — always invoke them via `.venv/bin/…` or activate first with `source .venv/bin/activate`. A bare `ruff check .` fails with command-not-found.

```bash
python -m venv .venv                                        # one-time; ensurepip bootstraps pip
.venv/bin/pip install -r requirements.txt                   # PySide6, Pillow, reportlab (capped <next-major)
.venv/bin/pip install -r requirements-dev.txt               # pytest + ruff (dev only)
.venv/bin/pip install pyinstaller                           # only for builds; not in either requirements file

.venv/bin/python app.py                                     # PySide6 GUI (all three tools)
.venv/bin/python make_cards.py sessions/<name>              # ASK cards CLI (./make_cards.sh wraps this)
.venv/bin/python make_lotto.py lotto-sessions/<name>        # Lotto CLI
.venv/bin/python make_tegnprotokoll.py tegnprotokoll-sessions/<name>  # Sign protocol CLI
.venv/bin/python arasaac.py <query>                         # ad-hoc API client test
.venv/bin/python tegnbanken.py <query>                      # ad-hoc API client test

.venv/bin/python -m pytest                                  # full suite (tests/, ~1s, no network needed)
.venv/bin/python -m pytest tests/test_pdf_utils.py::test_safe_stem_empty_falls_back_to_image  # single test
.venv/bin/ruff check .                                      # lint (config in ruff.toml)

.venv/bin/pyinstaller app.spec                              # build dist/aacdeck (onefile, GUI)
```

Headless GUI smoke test (constructs the main window without a display):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import app; from PySide6.QtWidgets import QApplication; a=QApplication([]); app.MainWindow(); print('ok')"
```

Tests live in `tests/` (pytest, configured in `pytest.ini`); they cover the pure helpers in `pdf_utils`, run each generator against a temp session of generated images, and exercise the API clients with monkeypatched network calls — no real network or system fonts required. `tests/test_app_paths.py` imports the GUI and uses `pytest.importorskip`, so it silently skips where PySide6's Qt libs can't load — including the CI test job, which installs no system Qt libs. Ruff is configured **lint-only** (`ruff.toml`, rules E/F/W; import-sorting and E402 deliberately disabled — there is no formatter and the import layout is intentional). No type checker is configured. Don't add placeholder commands or assume other tooling exists.

On Linux, PySide6 needs system Qt/XCB libs (see `.github/workflows/build.yml` for the apt-get list; on Arch these are `xcb-util-cursor` and `libxkbcommon-x11`). CI builds onefile executables for Linux/Windows/macOS on push to `main` and tags (`v*`) create a GitHub release.

**CI pins Python 3.12; the local venv may be newer** (3.14 on the maintainer's machine, using PySide6's `cp310-abi3` wheel). A local pass is strong evidence but not proof — version-sensitive changes should be confirmed against CI.

There is **no version string in the source** — no `__version__`, nothing in `app.spec` or `README.md`. The git tag *is* the version, and pushing a `v*` tag is the only thing that publishes a release.

## Architecture

- **GUI (`app.py` + `gui_*.py`)** — `app.py` only assembles `MainWindow` (tabs + "Open data folder" button), the icon, and `main()`. Each tab is its own module with its `QThread` workers at the top: `gui_cards.py` (`CardsTab`), `gui_lotto.py` (`LottoTab`), `gui_tegnprotokoll.py` (`TegnprotokollTab`). `gui_pictograms.py` holds `PictogramSearchPanel` + the ARASAAC workers, shared by the Cards and Lotto tabs. `gui_common.py` owns `BASE_DIR`/session/output paths, the legacy-data migration and `pillow_to_qimage()`. `gui_previews.py` renders the Pillow previews with its own pixel constants, but **column counts and column fractions are imported from the generator modules** (`make_cards.COLS`, `make_lotto.LOTTO_COLS`, `make_tegnprotokoll.COL_FRACS`) so the preview can't drift from the real PDF layout. The generators and API clients are imported as libraries.
- **`make_cards.py` / `make_lotto.py` / `make_tegnprotokoll.py`** — dual-mode modules: importable library *and* standalone CLI. Each defines a small set of layout constants at the top of the file — edit those to change spacing without touching drawing logic. `make_lotto` exposes `make_board_pdf`, `make_cutout_pdf`, and `make_board_and_cutout_pdf` (the last decodes each image once and renders both PDFs — used by the CLI and GUI).
- **`pdf_utils.py`** — shared helpers used by all three generators and the GUI: `IMAGE_EXTS` (frozenset), `to_rgb()`, `register_nordic_bold_font()`, `register_nordic_regular_font()`, `fit_text()` (returns a font size), `fit_label()` (returns `(display_text, size)`, ellipsizing labels that can't fit even at the minimum size), `compute_grid()` (shared square-card page geometry → `Grid` namedtuple, raises `ValueError` on impossible layouts), `safe_stem()` (Windows-safe filename sanitiser), `stem_to_label()` (stem → label; strips the GUI's `__N` duplicate counter), `open_file()` (cross-platform PDF/folder opener), and the ARASAAC attribution helpers (`ARASAAC_CREDIT`, `draw_credit()`, `record_arasaac_image()`, `carry_arasaac_record()`, `uses_arasaac()`).
- **`arasaac.py` / `tegnbanken.py`** — external API clients, each with a built-in CLI mode for testing. `search()` returns `[]` when the server is reachable but finds nothing, and raises `SearchError` on a genuine network/server failure (tegnbanken still falls back to a stale cache first). The GUI search workers surface `SearchError` as an error message rather than a misleading "No results".

## Conventions

- Sessions live in `sessions/`, `lotto-sessions/`, `tegnprotokoll-sessions/` (one folder per session), conventionally named `YYYY-MM-description` (e.g. `2026-03-familie`). `output/` is auto-created; PDFs land there named after the session.
- **Only `sessions/` survives a fresh clone** (via `.gitkeep`) — `.gitignore` excludes `*-sessions/*/`, so the lotto and tegnprotokoll collection dirs simply don't exist until something makes them. The GUI creates whichever it needs on tab init and `_refresh_sessions()` returns early when one is absent, so this is invisible in normal use. The CLIs only `mkdir` `output/`, so a CLI-first workflow means creating the session folder yourself (`mkdir -p lotto-sessions/2026-04-test`) — otherwise `make_cards.py`/`make_lotto.py` raise `ValueError: '…' is not a directory`. `make_tegnprotokoll.py` has no such guard and fails later on the empty-session check instead.
- Supported images: `.jpg`, `.jpeg`, `.png`, `.webp`, `.avif`. Filename stem → card label via `stem_to_label()` (underscores → spaces, `__N` duplicate counter stripped) — used uniformly by all three generators and the GUI previews/lists, so labels match everywhere. All images are decoded by Pillow and converted to RGB PNG before reaching ReportLab.
- Tegnbanken data is cached at `~/.cache/aacdeck/tegnbanken/data.xml` (7-day TTL), not in the project dir.
- Optional `descriptions.json` sidecar in a tegnprotokoll session maps `{"stem": "description text"}`.
- **ARASAAC attribution is a licence requirement (CC BY-NC-SA).** Every ARASAAC download is recorded in the session's `.arasaac.json` sidecar (`pdf_utils.record_arasaac_image`). Lotto PDFs always print `pdf_utils.ARASAAC_CREDIT` in the bottom margin; card PDFs print it only when `uses_arasaac()` finds a recorded image. Any new path that saves ARASAAC images must record them.

## Gotchas

- **ReportLab uses a bottom-left origin.** Labels are visually at the top of a card but have the *highest* `y` values in drawing code.
- **Font fallback breaks Norwegian characters.** Priority is Liberation Sans Bold → Arial Bold → DejaVu Sans Bold → Helvetica-Bold; the built-in Helvetica-Bold fallback does **not** render æ/ø/å.
- **Error-handling differs by module when run as CLI:** `make_cards.py` uses `sys.exit()`; `make_lotto.py`/`make_tegnprotokoll.py` raise `ValueError`. The GUI workers account for this when calling them as libraries.
- **PyInstaller onefile** extracts to `sys._MEIPASS`, so the GUI workers always pass `OUTPUT_DIR` to the generators explicitly (their own default resolves inside the temp dir).
- **`BASE_DIR` differs by mode.** Defined in `gui_common.py`. From source it is the project folder; in the packaged app it is `~/Documents/AACdeck` (via `QStandardPaths`). On first launch, `migrate_legacy_data()` *copies* (never moves) session/output folders from beside the executable — where releases before 2026-10 kept them — unless the new folder already exists.

## Repo automations (`.claude/`)

- **Hooks** (`.claude/settings.json`) — after every `Edit`/`Write` to a `.py` file, `ruff check <file>` and the full pytest suite run automatically and report failures back. No need to run them manually after an edit; do run them after a batch of non-`.py` changes or before tagging.
- **`pdf-layout-reviewer`** agent — review rendering/layout changes (origin direction, preview drift, constants, fonts).
- **`qt-threading-reviewer`** agent — review `QThread` workers and anything touching Qt objects off the main thread.
- **`layout-constants`** skill — Claude-only (`user-invocable: false`); auto-loads the constant tables and rendering gotchas when drawing code comes up.
- **`run-app`** skill — launches the GUI (venv bootstrap, background launch, Qt/display failure modes), the offscreen smoke test and screenshot capture, and the CLI + `pdftoppm` route for inspecting generated PDFs.
- **`release`** skill — user-only (`disable-model-invocation: true`); tags and publishes. Never invoke it on your own inference.

## Reference docs

This file is the single source of truth. `AGENTS.md` and `.github/copilot-instructions.md` are deliberately thin pointers back to it — keep them that way rather than re-documenting anything there.

For rendering work, the **`layout-constants` skill** holds the layout-constant tables for all three generators, the `make_cards.py` internal call flow, and the geometry rules.
