# AACdeck

Generates print-ready A4 PDFs of picture cards for AAC/ASK (alternativ og supplerende kommunikasjon). Place your images in a session folder, run the script, and get a PDF ready to print, laminate, and cut.

Each card shows the image with the filename as the label — `stor_bror.jpg` becomes a card labelled **stor bror**.

## GUI

Run `app.py` for a desktop interface:

```bash
python app.py
```

The GUI provides:
- **Sessions panel** — list existing sessions or create a new one
- **Images panel** — drag-and-drop images into a session, add via file dialog, or right-click to remove
- **Page preview** — live A4-proportioned preview with page navigation
- **Generate PDF** button — runs `make_cards` in a background thread and shows an **Open PDF** button when done

### Standalone executable

Build a single-file executable with PyInstaller:

```bash
pyinstaller app.spec
# output: dist/aacdeck
```

## CLI

```bash
./make_cards.sh sessions/2026-03-familie
```

The PDF is saved to `output/2026-03-familie.pdf`.

Run without arguments to see help:

```bash
./make_cards.sh
```

## Setup

```bash
# Arch Linux
sudo pacman -S python-pillow python-reportlab pyside6

# or via pip
pip install -r requirements.txt
```

## Session folders

Organize images under `sessions/`, one folder per session:

```
sessions/
├── 2026-03-familie/
│   ├── mamma.jpg
│   ├── pappa.png
│   └── stor_bror.webp
└── 2026-04-skole/
    └── ...
```

- Supported formats: `jpg`, `jpeg`, `png`, `webp`, `avif`
- Filename stem (without extension) becomes the card label
- Underscores in filenames are replaced with spaces
- Cards are sorted alphabetically within each session

## Output

- A4 page, 3 columns × 4 rows = 12 cards per page
- Cards are square with a border, label at top, image below
- PDF is saved to `output/<session-name>.pdf`

## License

MIT © 2026 Christian Kronborg Skeide — see [LICENSE](LICENSE) for details.
