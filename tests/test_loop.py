"""Closed-loop checks: the aim must not ring when its own moves lag.

Runs the simulation in loop_sim.py (textured world, weaving target, input
lag) and asserts on the outcome. See that file for the model.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loop_sim as L

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def go(**kw):
    return L.run(seconds=10, **kw)


# Aggressive smoothing + 4 frames of input lag: the regime that rubber-bands.
plain = go(prediction=False, compensate=False, smooth=2.0, latency=4)
naive = go(prediction=True, compensate=False, smooth=2.0, latency=4)
fixed = go(prediction=True, compensate=True, smooth=2.0, latency=4)
check("uncompensated prediction rings (the bug)", naive[2] > 20, f"jump {naive[2]:.1f}px/frame")
check("compensated prediction is stable", fixed[2] < 8, f"jump {fixed[2]:.1f}px/frame")
check("compensated prediction tracks tightly", fixed[0] < 30, f"error {fixed[0]:.1f}px (plain {plain[0]:.0f})")

# A well-behaved loop must not get worse for the compensation.
calm_plain = go(prediction=False, compensate=False, smooth=3.0, latency=2)
calm = go(prediction=True, compensate=True, smooth=3.0, latency=2)
check("prediction clearly beats plain aim", calm[0] < 0.5 * calm_plain[0], f"{calm[0]:.1f} vs {calm_plain[0]:.1f}px")

# Screen grabs include our overlays; masking them must restore ego-motion.
unmasked = go(prediction=True, compensate=True, smooth=3.0, latency=2, overlays="unmasked")
masked = go(prediction=True, compensate=True, smooth=3.0, latency=2, overlays="masked")
check("masking overlays restores ego-motion on screen grabs", masked[0] < 0.5 * unmasked[0],
      f"masked {masked[0]:.1f}px vs unmasked {unmasked[0]:.1f}px")
check("screen mode matches window mode", masked[0] < 1.5 * calm[0] + 3, f"{masked[0]:.1f} vs {calm[0]:.1f}px")

print("\nFAILURES:", failures or "none")
sys.exit(1 if failures else 0)
