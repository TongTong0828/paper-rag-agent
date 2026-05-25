# examples/

Runnable walk-throughs for the three workflows users ask about most often.

These are plain `.py` scripts (jupytext-flavoured `# %%` cells) — they
double as scripts you can run with `python examples/<name>.py` AND open
in Jupyter / VS Code as notebooks.

| Script | What it shows |
|---|---|
| [`01_qa_basic.py`](01_qa_basic.py) | Ingest → ask → cite. Covers `paper_qa`, validates citations, prints abstain trace. |
| [`02_calibrate_abstain.py`](02_calibrate_abstain.py) | Run `calibrate_abstain.py` on a tiny eval set; visualise the no_evidence/weak/confident split. |
| [`03_collect_hard_cases.py`](03_collect_hard_cases.py) | Replay `thumbs_down` events into the hard-case set; show the JSONL it produces. |

## Quickstart

```bash
pip install -e ".[full,dev]"
make qdrant-up
make init-store

python examples/01_qa_basic.py
python examples/02_calibrate_abstain.py
python examples/03_collect_hard_cases.py
```

If you'd prefer Jupyter:

```bash
pip install jupytext
jupytext --to notebook examples/01_qa_basic.py
jupyter lab examples/01_qa_basic.ipynb
```
