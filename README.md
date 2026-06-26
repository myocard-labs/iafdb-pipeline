# myocard-iafdb-pipeline

> PhysioNet IAFDB producer for the myocard-labs intracardiac-EGM stack. Downloads the dataset, calibrates and band-passes the signal, slices bipolar segments, and writes HDF5 banks plus JSON provenance sidecars.

Part of the [myocard-labs](https://github.com/myocard-labs) cardiac signal-processing toolkit.

---

## Why

[PhysioNet IAFDB](https://physionet.org/content/iafdb/1.0.0/) — the Intracardiac Atrial Fibrillation Database — is one of the few openly available intracardiac bipolar EGM datasets. It carries 32 records from 8 patients across 4 catheter placements (SVC, IVC, TVA, AFW), sampled at 1 kHz, with mixed surface-ECG complements per record. It's the in-vivo anchor the myocard-labs project uses to sanity-check the fibrosis classifier against real recordings — a label-free diagnostic, since IAFDB carries no fibrosis labels to score against.

`myocard-iafdb-pipeline` is the IAFDB-specific producer: it knows about PhysioNet's URL layout, the IAFDB channel conventions, and the dataset's quirks (mixed surface leads, no session timestamps, uncalibrated amplitudes). It hands the resulting calibrated segments to [`myocard-egm-data`](https://github.com/myocard-labs/egm-data) for HDF5 writing, against schemas owned by [`myocard-egm-contracts`](https://github.com/myocard-labs/egm-contracts), using DSP primitives from [`myocard-egm-signal`](https://github.com/myocard-labs/egm-signal).

What this repo does NOT do: HDF5 I/O (egm-data owns it), DSP primitives like filtering / calibration / segment extraction (egm-signal owns them), schema definitions (egm-contracts owns them), or classification (egm-classifier owns it). The split lets the producer stay focused on one job — converting IAFDB into bank-format artifacts that satisfy the project-wide contracts.

Downstream, the resulting banks are consumed by `myocard-egm-classifier` (training + the label-free IAFDB diagnostic) and by `synthetic-egm-pipeline`'s mixer (noise bank → additive noise on clean simulated traces).

---

## Install

From source during pre-1.0 iteration:

```bash
pip install "myocard-iafdb-pipeline @ git+https://github.com/myocard-labs/iafdb-pipeline.git"
```

Editable install for development:

```bash
git clone https://github.com/myocard-labs/iafdb-pipeline.git
cd iafdb-pipeline
pip install -e ".[dev]"
pre-commit install
```

The runtime deps (`myocard-egm-contracts`, `myocard-egm-data`, `myocard-egm-signal`, `numpy`, `wfdb`, `tqdm`, `pyyaml`) come in transitively. The three myocard siblings are pinned to git tags during pre-1.0; drop the direct references once they publish to PyPI.

---

## Quick start

End-to-end: download the dataset, then produce a healthy bank and a noise bank using the shipped example configs.

```bash
iafdb-download                                                     # mirror IAFDB to ./data/iafdb/
iafdb-export-bank examples/iafdb_healthy_default.yaml              # ./banks/iafdb_healthy_v1.h5
iafdb-export-noise-bank examples/iafdb_noise_percentile.yaml       # ./banks/iafdb_noise_v1.h5 + .json
```

Four console scripts are installed:

| Command | Purpose |
|---|---|
| `iafdb-download` | Fetch the IAFDB dataset from PhysioNet (32 records, ~150 MB). |
| `iafdb-inspect` | Print per-record metadata and per-channel stats. |
| `iafdb-export-bank` | Build an `iafdb_bank.h5` (optionally also a paired ClassifierBank). |
| `iafdb-export-noise-bank` | Build a `noise_bank.h5` + `noise_bank_run_record.json`. |

The two `export` commands are config-driven; the YAML schema and a per-command walkthrough live in [`docs/usage.md`](docs/usage.md). Pre-written configs covering the Sánchez sinus-rhythm 0.5 mV threshold, the Kosiuk AF-adjusted 0.2 mV threshold, an unfiltered pretraining mode, a paired-ClassifierBank mode, and two noise-side strategies (percentile, absolute) live under [`examples/`](examples/).

---

## Programmatic usage

The orchestrators are importable when you'd rather drive the producer from Python:

```python
from pathlib import Path
from myocard_egm_signal import AbsoluteThreshold
from myocard_iafdb_pipeline.export import export_bank
from myocard_iafdb_pipeline.records import iter_records

records = iter_records(Path("data/iafdb"), skip_missing=True)
result = export_bank(
    output_path=Path("out/iafdb_healthy_v1.h5"),
    records=records,
    threshold=AbsoluteThreshold(0.2),
    window_ms=512.0,
    hop_ms=256.0,
    target_qrs_pp_mv=1.0,
    overwrite=True,
)
print(result.n_segments, result.source_records)
```

See [`docs/usage.md`](docs/usage.md) for the noise-side orchestrator, the full kwarg reference, and the consumer-side bank-reading recipe.

---

## Tests

```bash
pytest                  # full suite
pytest --cov            # with coverage
ruff check .            # lint
ruff format --check .   # format check
mypy                    # type check
```

CI runs the same checks on Python 3.10, 3.11, and 3.12 — see `.github/workflows/ci.yml`.

---

## Project status

This package is part of the in-progress [myocard-labs](https://github.com/myocard-labs) refactor. Pre-1.0 — expect breaking changes across minor versions until the schemas stabilize. The current release is `v0.2.0` and pins `egm-contracts v0.2.0`, `egm-data v0.2.0`, and `egm-signal v0.1.0`. See [`project/roadmap.md`](project/roadmap.md) for what's planned and [`project/architecture.md`](project/architecture.md) for the design rationale.

---

## Citation

If you use this software in academic work, please cite both the IAFDB dataset and this producer:

```bibtex
@misc{iafdb_v1,
  author       = {{PhysioNet}},
  title        = {Intracardiac Atrial Fibrillation Database (IAFDB) v1.0.0},
  year         = {2008},
  doi          = {10.13026/C23S33},
  url          = {https://physionet.org/content/iafdb/1.0.0/},
}

@software{klein_myocard_iafdb_pipeline_2026,
  author  = {Klein, Daniel},
  title   = {myocard-iafdb-pipeline: PhysioNet IAFDB producer for the myocard-labs intracardiac-EGM stack},
  year    = {2026},
  url     = {https://github.com/myocard-labs/iafdb-pipeline},
}
```

PhysioNet is the canonical citation for the dataset; the producer is a separate piece of software with its own citation.

---

## License

MIT — see [LICENSE](LICENSE). Attribution requirements for the IAFDB dataset are listed in [NOTICE](NOTICE).
