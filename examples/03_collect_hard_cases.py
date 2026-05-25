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
# # 03 — Hard-case data loop (M11)
#
# When a user clicks 👎 on an answer, that event lands in `feedback.sqlite`.
# A weekly cron extracts those into a hard-case JSONL, which feeds the next
# threshold calibration round (notebook 02).
#
# This notebook simulates the loop without the cron sidecar:
#
# 1. Inject a few synthetic `thumbs_down` events.
# 2. Run `collect_hard_cases.py` to extract them into the standard JSONL.
# 3. Show what the calibrator will see on its next pass.

# %%
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# %% [markdown]
# ## 1. Inject synthetic events

# %%
from paper_rag.feedback import collector  # noqa: E402

events = [
    {"user_id": "u1", "trace_id": "trace-001", "type": "thumbs_down",
     "payload": {"question": "What is XYZ?", "answer": "(no evidence)"}},
    {"user_id": "u1", "trace_id": "trace-002", "type": "thumbs_down",
     "payload": {"question": "Compare A and B", "answer": "Mixed signals"}},
    {"user_id": "u2", "trace_id": "trace-003", "type": "thumbs_down",
     "payload": {"question": "Define foobar", "answer": "Foobar is unknown."}},
]
for e in events:
    collector.record(**e)
print(f"injected {len(events)} thumbs_down events")

# %% [markdown]
# ## 2. Extract hard cases
#
# `collect_hard_cases.py` is the same script the weekly cron runs.

# %%
out_path = ROOT / "tests" / "eval" / "hard_cases_demo.jsonl"
result = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "collect_hard_cases.py"),
     "--out", str(out_path), "--since-days", "30"],
    capture_output=True, text=True, check=False,
)
print(result.stdout[-1500:])
if result.returncode != 0:
    print("STDERR:", result.stderr[-800:])

# %% [markdown]
# ## 3. Inspect the JSONL
#
# Each line is one hard case. The calibrator will treat these as "negatives
# the system was over-confident on", and use them to tune `threshold_low`.

# %%
if out_path.exists():
    lines = out_path.read_text().splitlines()
    print(f"hard_cases.jsonl: {len(lines)} cases")
    for line in lines[:5]:
        try:
            print(json.dumps(json.loads(line), indent=2, ensure_ascii=False)[:400])
            print("---")
        except Exception:
            print(line[:200])
else:
    print(f"(no output at {out_path})")
