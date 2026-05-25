# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 02 — Calibrate the abstain threshold
#
# We pick `threshold_low / threshold_high` from data, not by guessing.
# The calibrator is `scripts/calibrate_abstain.py`, with two modes:
#
# - **offline**: stub LLM, deterministic — good for CI smoke + threshold
#   exploration without spending tokens
# - **online**: real LLM + Qdrant, used to lock down production thresholds
#
# This notebook drives the offline mode and plots the resulting
# pos_kept / neg_blocked curve so we can pick a good operating point.

# %%
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# %%
print("Running offline calibration ...")
result = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "calibrate_abstain.py"), "--mode", "offline"],
    capture_output=True, text=True, check=False,
)
print(result.stdout[-3000:])
if result.returncode != 0:
    print("STDERR:", result.stderr[-1500:])

# %% [markdown]
# ## Inspect the threshold sweep
#
# The calibrator writes a JSON report. Each row is a `(low, high)` candidate
# with its `pos_kept / neg_blocked / weak_count` numbers.

# %%
report = ROOT / "tests" / "eval" / "abstain_calibration_report.json"
if not report.exists():
    print(f"(no report at {report} yet — run the calibrator first)")
else:
    data = json.loads(report.read_text())
    print(f"# candidates: {len(data.get('candidates', []))}")
    # Top 5 by pos_kept * neg_blocked
    cands = sorted(
        data.get("candidates", []),
        key=lambda r: r.get("pos_kept", 0) * r.get("neg_blocked", 0),
        reverse=True,
    )[:5]
    for c in cands:
        print(
            f"  low={c.get('threshold_low'):.2f} high={c.get('threshold_high'):.2f}"
            f"  -> pos_kept={c.get('pos_kept'):.2f} neg_blocked={c.get('neg_blocked'):.2f}"
        )

# %% [markdown]
# ## Quick textual plot (no matplotlib dep)
#
# A bar chart of `neg_blocked` vs `threshold_low`. Useful when you want to
# see how aggressive low-threshold tuning catches out-of-domain questions.

# %%
if report.exists():
    by_low: dict[float, list[float]] = {}
    for c in data.get("candidates", []):
        by_low.setdefault(c["threshold_low"], []).append(c["neg_blocked"])
    for low in sorted(by_low):
        avg = sum(by_low[low]) / len(by_low[low])
        bar = "#" * int(avg * 40)
        print(f"  low={low:.2f}  neg_blocked={avg:.2f}  {bar}")
