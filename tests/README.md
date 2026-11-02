# Tests

Seven headless suites, ~100 checks. No test framework and no real mouse input:
each script prints `PASS`/`FAIL`/`SKIP` per check and exits non-zero if
anything failed, so they work from any shell or a CI step.

```
python tests/smoke.py           # config schema, hotkeys, themes, kalman, ESP
python tests/test_arraylist.py  # ordering, animation, move mode
python tests/test_trigger.py    # triggerbot delay rate limiting
python tests/test_radar.py      # bearing, distance, filtering
python tests/test_prediction.py # kalman, lead, frame-rate independence
python tests/test_egomotion.py  # world-frame tracking while the aim pins the target
python tests/test_loop.py       # closed loop: no ringing when moves lag
python tests/test_detection.py  # colour floors, frame stats, mask preview
python tests/test_widgets.py    # status strip, preview, eyedropper, accents
python tests/test_autosave.py   # atomic writes, restore, damaged files
python tests/test_session.py    # session stats: targets, lock time, on-target share
```

Run them all:

```
for f in tests/*.py; do python "$f" || echo "FAILED: $f"; done
```

They default to `QT_QPA_PLATFORM=offscreen`, so no windows appear.
`test_trigger.py` substitutes a fake aimer - it never sends real clicks.

## Gotchas

**Fonts.** The offscreen platform has no font directory and falls back to a
monospace face where every glyph has the same advance. `test_arraylist.py`
detects this and prints SKIP for the checks that compare rendered text widths.
To run those for real:

```
QT_QPA_PLATFORM=windows python tests/test_arraylist.py
```

**`cv2.contourArea` measures the polygon between contour points**, so an 8x8
filled square reports 49, not 64. Size fixtures near a threshold need care.

**Blob centroids land on half-pixels** for even-width fixtures, and half a
pixel is a larger *fraction* of a small FOV. Compare bearings with a tolerance,
never with equality.
