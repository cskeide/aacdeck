---
name: run-app
description: Start the AACdeck GUI, or run one of the three generator CLIs, with venv bootstrap and Qt/display checks. Use when asked to run, start, open, launch, or smoke-test the app.
---

Launch this project. The GUI is a PySide6 desktop app — it paints into a native window on the user's display, it does not serve HTTP, and it cannot be shown in the Browser pane or any preview panel. Don't add a `launch.json` entry for it or try to `preview_start` it.

## 1. Preflight: the venv

Everything runs out of `.venv/`. The runtime deps are **not** installed system-wide, so a bare `python app.py` fails with `ModuleNotFoundError: No module named 'PySide6'`.

```bash
ls .venv/bin/python
```

If it's missing, bootstrap before doing anything else — the repo's own PostToolUse hooks shell out to `.venv/bin/ruff`, so a missing venv also breaks every `.py` edit:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## 2. Launch the GUI

Run it **in the background** — it blocks until the user closes the window, so a foreground call would hang the turn:

```bash
.venv/bin/python app.py
```

Use `run_in_background: true` on the Bash call. Then confirm it actually came up rather than dying on import, by checking the background output for a traceback. Silence is success: the app prints nothing on a clean start.

Tell the user the window is open on their display. You cannot see it — for anything visual, ask them what they see, or use the offscreen capture below.

## 3. Headless smoke test

When the point is "does it still construct", not "show me the window", skip the display entirely:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import app; from PySide6.QtWidgets import QApplication; a=QApplication([]); app.MainWindow(); print('ok')"
```

This catches import errors, signal/slot typos, and constructor-time crashes in about a second. It does **not** exercise the `QThread` workers — those only run on a real user action.

To get an actual image of the UI without a display, grab the widget offscreen:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
import app
from PySide6.QtWidgets import QApplication
a = QApplication([]); w = app.MainWindow(); w.resize(1100, 800); w.show()
a.processEvents()
w.grab().save('/tmp/ask-gui.png')
print('/tmp/ask-gui.png')"
```

Then `Read` the PNG. Layout is approximate under the offscreen platform — treat it as a sanity check, not a pixel reference. The offscreen plugin prints `This plugin does not support propagateSizeHints()` to stderr; that is expected noise, not a failure.

`grab()` captures the visible tab only. The `QTabWidget` is a local in `MainWindow.__init__`, not stored on `self`, so reach it with `findChild` — then switch, pump events again, and grab:

```python
from PySide6.QtWidgets import QTabWidget
tabs = w.findChild(QTabWidget)
tabs.setCurrentIndex(1)   # 0 Cards, 1 Lotto, 2 Sign Protocol
a.processEvents()
w.grab().save('/tmp/ask-gui-lotto.png')
```

## 4. Running a generator instead

If the user wants a PDF rather than the GUI, go straight to the CLI — it's faster and the output is inspectable:

```bash
.venv/bin/python make_cards.py sessions/<name>
```

Same shape for `make_lotto.py lotto-sessions/<name>` and `make_tegnprotokoll.py tegnprotokoll-sessions/<name>`. PDFs land in `output/<session>.pdf`.

To actually check the layout, rasterize and look at it:

```bash
pdftoppm -png -r 80 output/<session>.pdf /tmp/page && ls /tmp/page*.png
```

`Read` the resulting PNGs. This is the only way to verify rendering changes — the test suite asserts that PDFs are produced, not that they look right.

## Failure modes

- **`ModuleNotFoundError: PySide6`** — venv missing or not used. See step 1. Note `make_cards.sh` calls bare `python3`, deliberately bypassing the venv; prefer `.venv/bin/python make_cards.py` over the wrapper.
- **`qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`** — missing system Qt libs. On Arch: `sudo pacman -S xcb-util-cursor libxkbcommon-x11`. Other distros: see the apt-get list in `.github/workflows/build.yml`. Report the command, let the user run the install.
- **No `DISPLAY` or `WAYLAND_DISPLAY`** — you're in a headless context. Don't try to launch the GUI; use the offscreen smoke test.
- **`ValueError: '…' is not a directory`** — a CLI was pointed at a session folder that doesn't exist. Only `sessions/` survives a fresh clone; `lotto-sessions/` and `tegnprotokoll-sessions/` must be created by hand (`mkdir -p lotto-sessions/<name>`) or by opening the matching GUI tab.
- **Empty session** — the generators refuse a folder with no supported images (`.jpg`, `.jpeg`, `.png`, `.webp`, `.avif`).
