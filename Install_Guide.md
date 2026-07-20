# ProSuite — Setup Guide

## Requirements

- Windows 10 or 11 (the mouse output uses Win32 `SendInput`, so it is Windows-only)
- Python 3.9 or newer
- pip

Check your Python with:

```
python --version
```

## Install

From the project folder:

```
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

That pulls in PySide6, OpenCV, NumPy, mss and pynput.

### Using a virtual environment (recommended)

Keeps these packages out of your system Python, which matters because
`opencv-python` and `numpy` are major-version sensitive and can clash with
other projects:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe ProSuiteCheat.py
```

## Run

```
python ProSuiteCheat.py
```

### Command-line options

| Flag | Effect |
|---|---|
| `--no-hotkeys` | Start without installing global keyboard/mouse hooks. Everything works except keybinds. |
| `--debug` | Verbose logging. |

`--no-hotkeys` is worth knowing about. Hotkeys are captured with `pynput`,
which installs system-wide low-level input hooks (`WH_KEYBOARD_LL` /
`WH_MOUSE_LL`). Those share the input path with any virtual HID drivers you
have installed (vJoy, ViGEmBus, controller remappers). An out-of-date or buggy
one of those can crash — a 2019-era vJoy driver caused a `0x3B` bugcheck on
this machine. If you hit instability, start with `--no-hotkeys` to rule the
hooks out, then update or disable the offending driver.

Logs are written to `prosuite.log` next to the script.

## Project layout

```
ProSuiteCheat.py        Launcher
prosuite/
  constants.py          Palette, themes, stylesheet builders
  paths.py              Asset / config / log paths
  logging_setup.py
  settings.py           Config save + load
  core/
    aimer.py            SendInput mouse output
    kalman.py           Target motion filter
    detection.py        Capture, colour keying, worker thread
    input_listener.py   Global hotkeys
  ui/
    widgets.py          Toggles, sliders, control factories
    sections.py         Collapsible sections, menu pages
    overlays.py         FOV, ESP, prediction, radar
    arraylist.py        Active-feature HUD
    nav.py              Sidebar
    main_window.py      Main window
```

Screen capture and detection run on a dedicated worker thread and communicate
with the UI over Qt signals, so a slow frame never blocks the interface.

## Building the executable

```
python -m pip install pyinstaller
pyinstaller ProSuiteCheat.spec
```

Output lands in `dist/ProSuiteCheat.exe`. The spec sets `pathex=['.']` so the
`prosuite` package is picked up, and bundles `assets/`.

## Troubleshooting

**`Python was not found`** — Python is not on PATH. Either reinstall with
"Add Python to PATH" ticked, or use the launcher: `py ProSuiteCheat.py`.

**`ModuleNotFoundError: No module named 'PySide6'`** — dependencies are not
installed, or you are running a different Python than the one you installed
into. Check with `python -c "import sys; print(sys.executable)"`.

**`Unable to import Shiboken` / `source code string cannot contain null bytes`**
— a corrupted PySide6 install, usually from an interrupted download or an
unclean shutdown. Repair with:

```
python -m pip install --force-reinstall --no-cache-dir PySide6 shiboken6
```

**Icons missing / grey squares** — `assets/` is not next to the script, or the
files are unreadable. The app substitutes placeholder pixmaps and logs a
warning rather than failing.

**Nothing is detected** — check the Detection Color matches your target and
raise Color Tolerance. Detection also requires saturation and value above 100,
so very dark or washed-out colours will not register.
