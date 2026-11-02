"""Practice-session statistics: acquisition, lock time, time on target."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prosuite.core.session import LOCK_RADIUS_PX, REACQUIRE_GAP_S, SessionStats
from prosuite.ui.session_panel import fmt_seconds, format_summary

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def feed(stats, t0, seconds, scanning=True, dist=None, fps=60, shots=0):
    t = t0
    for _ in range(int(seconds * fps)):
        t += 1 / fps
        stats.update(t, scanning, dist, shots)
    return t


s = SessionStats()
t = feed(s, 0.0, 1.0, dist=None)
sm = s.summary()
check("scan time counts while nothing is visible", abs(sm.scan_seconds - 1.0) < 0.05 and sm.targets == 0, f"{sm.scan_seconds:.2f}s")
check("no data reads as None", sm.on_target is None and sm.mean_lock_s is None)

# A target appears far away, and takes 0.5 s to reach the crosshair.
t = feed(s, t, 0.5, dist=200)
t = feed(s, t, 1.0, dist=5)
sm = s.summary()
check("one target counted", sm.targets == 1)
check("lock time measured from first sight", sm.locks == 1 and 0.45 < sm.mean_lock_s < 0.6, f"{sm.mean_lock_s:.3f}s")
check("on-target share", 0.6 < sm.on_target < 0.72, f"{sm.on_target:.2f}")

# A flicker shorter than the gap is the same target.
t = feed(s, t, REACQUIRE_GAP_S / 3, dist=None)
t = feed(s, t, 0.3, dist=5)
check("short flicker is not a new target", s.summary().targets == 1)

# A long absence then a return is a new one, locked quicker.
t = feed(s, t, REACQUIRE_GAP_S * 2, dist=None)
t = feed(s, t, 0.1, dist=LOCK_RADIUS_PX + 40)
t = feed(s, t, 0.2, dist=LOCK_RADIUS_PX - 1)
sm = s.summary()
check("return after a gap is a second target", sm.targets == 2 and sm.locks == 2)
check("best lock time is the faster one", 0.05 < sm.best_lock_s < sm.mean_lock_s, f"best {sm.best_lock_s:.2f} mean {sm.mean_lock_s:.2f}")

# A target that appears already under the crosshair is a lock with no time.
t = feed(s, t, REACQUIRE_GAP_S * 2, dist=None)
t = feed(s, t, 0.2, dist=3)
sm = s.summary()
check("instant lock counted but not timed", sm.targets == 3 and sm.locks == 3 and sm.best_lock_s > 0.05, f"best {sm.best_lock_s:.2f}")

# Idle time is not scanning time; stalls aren't credited in full.
before = s.summary().scan_seconds
t = feed(s, t, 2.0, scanning=False)
check("idle time is not counted", abs(s.summary().scan_seconds - before) < 0.05)
s.update(t + 30.0, True, None, 0)
check("a long stall is capped", s.summary().scan_seconds - before < 0.2)

# Shots are relative to when the session began.
s2 = SessionStats()
s2.update(0.0, True, None, shots_total=40)
s2.update(0.1, True, None, shots_total=47)
check("shot count is relative to session start", s2.summary().shots == 7)
s2.reset()
s2.update(0.2, True, None, shots_total=60)
check("reset starts the shot count again", s2.summary().shots == 0)

# Formatting.
check("seconds format", fmt_seconds(42) == "42s" and fmt_seconds(125) == "2m 05s" and fmt_seconds(3725) == "1h 2m")
rows = format_summary(SessionStats().summary())
check("empty summary shows dashes", rows["on_target"] == "-" and rows["lock_time"] == "-")
rows = format_summary(s.summary())
check("populated summary formats", rows["lock_time"].endswith("ms") and rows["on_target"].endswith("%"), f"{rows['lock_time']} {rows['on_target']}")

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
