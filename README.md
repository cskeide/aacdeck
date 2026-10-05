# AACdeck

Print-ready A4 PDFs for ASK/AAC (alternativ og supplerende kommunikasjon) — in Norwegian or English, ready to print, laminate, and cut:

- **Cards** — picture cards from your own photos and/or ARASAAC pictograms. `stor_bror.jpg` becomes a card labelled **stor bror**.
- **Lotto** — search ARASAAC pictograms and get a lotto board plus a matching cut-out sheet.
- **Sign Protocol** (tegnprotokoll) — search Tegnbanken signs and get a table of sign, illustration, and how the child uses it.

## Download

Ready-to-run apps for Windows, macOS, and Linux are on the [Releases page](https://github.com/cskeide/aacdeck/releases/latest) — no Python needed. Download the file for your system and open it.

The app starts in Norwegian on a Norwegian system and in English otherwise; switch with the language menu in the top-right corner (takes effect after a restart). PDF headings and credit lines follow the same language.

Your sessions and PDFs are kept in **Documents/AACdeck**. The **Open data folder** button in the top-right corner opens it. (Earlier versions kept them next to the app; they are copied over automatically the first time you start a new version.)

## Using the app

Each tab works the same way:

1. **+ New session** — one session per set of cards, e.g. `2026-04-skole`.
2. Add images — drag and drop or **Add images…** (Cards), or search and **Add selected to session** (pictograms and signs). Right-click an image to rename, duplicate, or remove it; the label follows the filename.
3. Check the **Page preview**, then **Generate PDF**.

Cards are sorted alphabetically. Supported formats: `jpg`, `jpeg`, `png`, `webp`, `avif`.

## Credits and licences

- Pictograms: Sergio Palao / [ARASAAC](https://arasaac.org), owned by the Government of Aragón — CC BY-NC-SA 4.0. PDFs that contain pictograms print this credit automatically.
- Sign illustrations: Statped / [tegnbanken.no](https://tegnbanken.no) — CC BY-NC-ND 4.0, credited on every sign-protocol page.

Both licences are non-commercial.

## Running from source

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

When run from source, sessions live in the project folder (`sessions/`, `lotto-sessions/`, `tegnprotokoll-sessions/`) and PDFs go to `output/`.

Each tool also has a command-line version (add `--lang en` for English PDF text):

```bash
.venv/bin/python make_cards.py sessions/2026-03-familie            # → output/2026-03-familie.pdf
.venv/bin/python make_lotto.py lotto-sessions/2026-04-dyr          # → output/2026-04-dyr_board.pdf + _cutout.pdf
.venv/bin/python make_tegnprotokoll.py tegnprotokoll-sessions/bade  # → output/bade_tegnprotokoll.pdf
```

Build the standalone app with `pip install pyinstaller && pyinstaller app.spec` (output: `dist/aacdeck`). See [CLAUDE.md](CLAUDE.md) for development details.

## License

MIT © 2026 Christian Kronborg Skeide — see [LICENSE](LICENSE) for details. The pictograms and sign illustrations the app downloads are under their own licences (above).
