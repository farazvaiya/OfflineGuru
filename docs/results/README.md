# Measurement artifacts

Checked-in evidence for the numbers in `docs/EVALUATION.md`. The runtime
directory `data/` is gitignored, so these snapshots are kept here deliberately:
claims should be auditable, not just asserted.

All produced on: i5-7300U (2C/4T), 7.1 GB RAM, no GPU, Ubuntu 26.04,
Python 3.14.7, int8 models, 2026-10-20.

| File | Produced by | Contents |
|---|---|---|
| `asr.json` | `scripts/eval_asr.py --sizes tiny,base,small` | WER, p50/p95 latency, RTF, RSS per model |
| `translation_compare.json` | `scripts/compare_translators.py` | 12 sentences, Marian vs NLLB outputs and per-sentence latency |
| `loadtest_1.json` … `loadtest_30.json` | `scripts/loadtest.py --clients N` | join/WS latency, fan-out spread, bus drops, e2e percentiles |
| `run_final_160s.json` | `scripts/pipeline_cli.py data/demo/lecture.wav --speed 1` | 37 segments with per-segment timings, summary and quiz |

Reproduce (Internet needed only if `models/` is empty):

```bash
make models && make demo
.venv/bin/python scripts/eval_asr.py --sizes tiny,base,small
.venv/bin/python scripts/compare_translators.py
./scripts/serve.sh &
.venv/bin/python scripts/loadtest.py --clients 30
.venv/bin/python scripts/pipeline_cli.py data/demo/lecture.wav --speed 1
```

Numbers will differ on other hardware. The test material is synthetic clean
speech; it is a reproducible benchmark, not a classroom study.
