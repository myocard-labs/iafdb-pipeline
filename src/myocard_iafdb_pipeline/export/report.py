"""Per-record audit report for a bank-export run (B11b).

An optional JSON sidecar written beside an ``iafdb_bank.h5`` when
``--report`` is passed, recording *how each record was treated* rather than
what came out of it. The bank's own columns already say what each trace is;
what they cannot say is which surface lead calibrated a record, how large
its QRS reference actually was, or how many candidate windows were thrown
away and why.

Two audiences, both of which need it after the fact:

- **The methods section.** "How much real data survived, and on what
  basis" is a question a paper has to answer, and reconstructing it means
  re-running the whole export against the same settings.
- **Re-calibration.** If a record's header turns out to be wrong, the
  report says which lead was used and what it measured, so the effect of
  the fix can be reasoned about without re-deriving it.

**Deliberately unvalidated JSON.** The other sidecar in this repo,
``noise_bank_run_record``, has an egm-contracts schema; this one does not,
and that asymmetry is intentional for Phase 1.5 (CL-026): the methods paper
may reshape these fields, and freezing a contract around a shape nobody has
consumed yet buys nothing. ``report_version`` is here so the eventual
schema has something to migrate from. Until then the shape is documented in
``docs/usage.md`` and readers should treat unknown keys as ignorable.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from myocard_iafdb_pipeline.export.activation_extract import ChannelTally

REPORT_VERSION = "1"
"""Version of *this* file's shape — not an egm-contracts schema version.

Bumped when the layout changes incompatibly, so a consumer written against
an earlier run can tell. See the module docstring for why there is no
formal schema yet.
"""


@dataclass(frozen=True)
class RecordDiagnostics:
    """What one record contributed, and how it was conditioned.

    Collected during the export because none of it is recoverable from the
    bank afterwards: the calibration metadata is consumed to scale the
    signal and then discarded, and a record that contributed *nothing*
    leaves no trace in the bank at all — which is exactly the record an
    audit most wants to know about.
    """

    record_name: str
    patient_id: str
    placement: str
    # Calibration provenance, straight from egm-signal's Calibration.
    # All null under `method: none` — see `_record_diagnostics` for why the
    # scalar is not reported as 1.0.
    calibration_scalar: float | None
    calibration_lead: str | None
    measured_qrs_pp_mv: float | None
    n_beats: int | None
    # The record's surface-lead complement. IAFDB records carry exactly
    # three of four possible leads, and which three decides the priority
    # walk's outcome — so it explains the lead choice above.
    surface_leads: tuple[str, ...]
    segments_kept: int
    # Activation mode only; empty for the sliding path, which has no
    # per-window drop reasons to report.
    channel_tallies: tuple[ChannelTally, ...] = field(default_factory=tuple)


def _tally_dict(tally: ChannelTally) -> dict[str, Any]:
    return {
        "channel": tally.channel,
        "activations": tally.activations,
        "kept": tally.kept,
        "dropped_boundary": tally.dropped_boundary,
        "dropped_multi_activation": tally.dropped_multi_activation,
        "kept_multi_activation": tally.kept_multi_activation,
        "degenerate": tally.degenerate,
    }


def build_report(
    *,
    bank_path: Path,
    bank_id: str,
    source: str,
    bank_written: bool,
    windowing_mode: str,
    run_settings: dict[str, Any],
    records: list[RecordDiagnostics],
) -> dict[str, Any]:
    """Assemble the report document.

    ``bank_path`` is recorded as a bare filename, not a full path: the
    report sits beside its bank, and an absolute path would be wrong the
    moment either file moved. This mirrors the direction of the pointer on
    the bank side, which the schema defines as relative to the bank's own
    directory.

    ``bank_written`` is false when the run produced no usable segments and
    the bank was suppressed. The report is still written in that case — it
    is the only record of why — so this flag is what stops ``bank_file``
    from being read as a promise that the file is there.
    """
    per_record: list[dict[str, Any]] = []
    for rec in records:
        entry: dict[str, Any] = {
            "record_name": rec.record_name,
            "patient_id": rec.patient_id,
            "placement": rec.placement,
            "calibration": {
                "scalar": rec.calibration_scalar,
                "lead": rec.calibration_lead,
                "measured_qrs_pp_mv": rec.measured_qrs_pp_mv,
                "n_beats": rec.n_beats,
            },
            "surface_leads": list(rec.surface_leads),
            "segments_kept": rec.segments_kept,
        }
        if rec.channel_tallies:
            entry["channels"] = [_tally_dict(t) for t in rec.channel_tallies]
        per_record.append(entry)

    totals: dict[str, Any] = {
        "records_processed": len(records),
        "records_contributing": sum(1 for r in records if r.segments_kept > 0),
        "segments_kept": sum(r.segments_kept for r in records),
    }
    all_tallies = [t for r in records for t in r.channel_tallies]
    if all_tallies:
        totals["activations"] = sum(t.activations for t in all_tallies)
        totals["dropped_boundary"] = sum(t.dropped_boundary for t in all_tallies)
        totals["dropped_multi_activation"] = sum(t.dropped_multi_activation for t in all_tallies)
        totals["kept_multi_activation"] = sum(t.kept_multi_activation for t in all_tallies)
        totals["degenerate_channels"] = sum(1 for t in all_tallies if t.degenerate)

    return {
        "report_version": REPORT_VERSION,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "bank_id": bank_id,
        "bank_file": bank_path.name,
        "bank_written": bank_written,
        "source": source,
        "windowing_mode": windowing_mode,
        "run": run_settings,
        "totals": totals,
        "records": per_record,
    }


def write_report(path: Path, report: dict[str, Any], *, overwrite: bool = False) -> Path:
    """Write the report as indented JSON.

    Indented rather than compact because the file is read by people —
    an auditor scanning per-record calibration, or a reviewer checking a
    methods claim — and it is small enough that the size cost is irrelevant.
    """
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass overwrite=True to replace.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def relative_to_bank(report_path: Path, bank_path: Path) -> str:
    """The value for the bank's ``run_record_path`` attr.

    The schema defines it as relative to the bank's own directory, so the
    pair survives being moved or copied together — which is what phase
    storage does. Falls back to the bare filename when the two are not
    under a common root, since an absolute path would be worse than a
    slightly wrong relative one: it would point at *this* machine.
    """
    try:
        return str(report_path.resolve().relative_to(bank_path.resolve().parent))
    except ValueError:
        return report_path.name


__all__ = [
    "REPORT_VERSION",
    "RecordDiagnostics",
    "build_report",
    "relative_to_bank",
    "write_report",
]
