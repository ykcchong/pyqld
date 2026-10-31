"""Explicit small-molecule assay exports; no assay metadata is inferred from QLD."""

from __future__ import annotations

import csv
import hashlib
import json
import warnings
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .domain import MRMTransition
from .interop import (
    _base_report,
    _finite,
    _native_id,
    _optional_module,
    _polarity,
    _traces,
    _write_outputs,
)
from .model import Dataset, Sample

if TYPE_CHECKING:
    from pyopenms import TargetedExperiment


@dataclass(frozen=True)
class AssayTransition:
    """User-supplied metadata for one compound's declared transition role.

    Charges are signed, nonzero integers. Retention time is an explicit local
    time in seconds, not iRT. Evidence describes the external source of these
    values; stored peak areas and predicted QLD times are never substituted.
    """

    compound_index: int
    role: Literal["quantifier", "qualifier"]
    precursor_charge: int
    product_charge: int
    library_intensity: float
    retention_time_seconds: float
    evidence: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.compound_index, bool)
            or not isinstance(self.compound_index, int)
            or self.compound_index < 0
        ):
            raise ValueError("compound_index must be a nonnegative integer")
        if self.role not in ("quantifier", "qualifier"):
            raise ValueError("role must be quantifier or qualifier")
        for name in ("precursor_charge", "product_charge"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value == 0:
                raise ValueError(f"{name} must be a nonzero integer")
        for name in ("library_intensity", "retention_time_seconds"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("evidence must be a nonempty string")


def _prepare(
    dataset: Dataset, sample: int | str | Sample, assays: Iterable[AssayTransition],
    allow_inferred: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = dataset.sample(sample)
    report = _base_report(dataset, resolved)
    traces = _traces(dataset, resolved, allow_inferred)
    by_index = {trace.index: (transition, channel) for trace, transition, channel in traces}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    groups: dict[int, tuple[int, float]] = {}
    for assay in assays:
        if not isinstance(assay, AssayTransition):
            raise TypeError("assays must contain AssayTransition instances")
        if assay.compound_index >= len(dataset.compounds):
            raise ValueError(f"invalid compound_index: {assay.compound_index}")
        compound = dataset.compound(assay.compound_index)
        if assay.role == "qualifier" and not compound.has_qualifier:
            raise ValueError(f"compound {compound.index}: no declared qualifier")
        label = compound.quan_trace if assay.role == "quantifier" else compound.qual_trace
        declared = MRMTransition.parse(label)
        if declared is None or compound.function <= 0:
            raise ValueError(f"compound {compound.index}: invalid declared {assay.role}")
        trace = dataset.chromatogram(
            resolved, function=compound.function, transition=declared,
        )
        if trace is None or trace.index not in by_index:
            raise ValueError(f"compound {compound.index}: no stored {assay.role} trace")
        transition, channel = by_index[trace.index]
        native_id = _native_id(resolved, "QD5", trace.index)
        if native_id in seen:
            raise ValueError(f"duplicate native transition assignment: {native_id}")
        seen.add(native_id)
        group = (assay.precursor_charge, assay.retention_time_seconds)
        if compound.index in groups and groups[compound.index] != group:
            raise ValueError(
                f"compound {compound.index}: inconsistent precursor charge or retention time"
            )
        groups[compound.index] = group
        polarity = _polarity(channel.ionization_mode) if channel is not None else None
        for charge in (assay.precursor_charge, assay.product_charge):
            if (
                (polarity == "positive" and charge < 0)
                or (polarity == "negative" and charge > 0)
            ):
                raise ValueError(f"{native_id}: charge sign conflicts with {polarity} polarity")
        selected.append({
            **asdict(assay),
            "compound_id": f"pyqld_compound_{compound.index}",
            "compound_name": compound.name,
            "native_id": native_id,
            "trace_index": trace.index,
            "function": trace.function,
            "source_transition": trace.trace,
            "precursor_mz": float(transition.precursor_mz),
            "product_mz": float(transition.product_mz),
            "polarity": polarity,
        })
    if not selected:
        raise ValueError("at least one explicit assay transition is required")
    report.update({
        "schema": "pyqld.assays/v1",
        "scope": "explicit small-molecule assays linked to retained QLD traces; not raw data",
        "ownership": {"QD5": dataset.media_ownership("QD5")},
        "allow_inferred_ownership": allow_inferred,
        "assays": selected,
        "omitted_chromatograms": [
            {
                "native_id": _native_id(resolved, "QD5", trace.index),
                "trace_index": trace.index,
                "reason": "not explicitly selected by assay metadata",
            }
            for trace, _, _ in traces
            if _native_id(resolved, "QD5", trace.index) not in seen
        ],
        "omissions": [
            "Stored chromatogram points and spectra are not assay library data.",
            "No iRT, charge, library intensity, or retention time inferred from QLD.",
        ],
        "units": {
            "retention_time_seconds": "second",
            "library_intensity": "user-supplied; not rescaled",
        },
    })
    if dataset.media_ownership("QD5") == "inferred":
        report["warnings"].append("QD5 ownership inferred from equal sample-major groups")
    return selected, report


def _targeted_experiment(rows: list[dict[str, Any]], oms: Any) -> TargetedExperiment:
    experiment: TargetedExperiment = oms.TargetedExperiment()
    compounds: set[str] = set()
    for row in rows:
        if row["compound_id"] not in compounds:
            compound = oms.Compound()
            compound.id = row["compound_id"]
            compound.setChargeState(row["precursor_charge"])
            compound.setMetaValue("pyqld:compound_index", row["compound_index"])
            compound.setMetaValue("pyqld:compound_name", row["compound_name"])
            rt = oms.RetentionTime()
            rt.retention_time_type = oms.RetentionTime.LOCAL
            rt.retention_time_unit = oms.RetentionTime.SECOND
            rt.setRT(row["retention_time_seconds"])
            compound.rts = [rt]
            experiment.addCompound(compound)
            compounds.add(row["compound_id"])
        transition = oms.ReactionMonitoringTransition()
        transition.setCompoundRef(row["compound_id"])
        transition.setNativeID(row["native_id"])
        transition.setPrecursorMZ(row["precursor_mz"])
        product = oms.TraMLProduct()
        product.setMZ(row["product_mz"])
        product.setChargeState(row["product_charge"])
        transition.setProduct(product)
        transition.setLibraryIntensity(row["library_intensity"])
        transition.setQuantifyingTransition(row["role"] == "quantifier")
        transition.setIdentifyingTransition(row["role"] == "qualifier")
        transition.setMetaValue("pyqld:role", row["role"])
        transition.setMetaValue("pyqld:evidence", row["evidence"])
        transition.setMetaValue("pyqld:trace_index", row["trace_index"])
        experiment.addTransition(transition)
    return experiment


def to_targeted_experiment(
    dataset: Dataset, sample: int | str | Sample, assays: Iterable[AssayTransition], *,
    allow_inferred_ownership: bool = False,
) -> TargetedExperiment:
    """Build an OpenMS small-molecule library using only explicit assay metadata.

    Each assignment must resolve uniquely to a stored trace through its compound's
    function and declared role. Unknown polarity is left unknown, not guessed.
    """
    rows, _ = _prepare(dataset, sample, assays, allow_inferred_ownership)
    return _targeted_experiment(rows, _optional_module("pyopenms", "openms"))


def _publish(
    path: str | Path, report: dict[str, Any], writer: Callable[[Path], None], kind: str,
) -> Path:
    destination = Path(path)
    sidecar = destination.with_name(destination.name + ".json")

    def write_data(staged: Path) -> None:
        writer(staged)
        report[kind] = {
            "file": destination.name,
            "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
        }

    def write_report(staged: Path) -> None:
        staged.write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )

    _write_outputs({destination: write_data, sidecar: write_report})
    return destination


def export_traml(
    dataset: Dataset, sample: int | str | Sample, assays: Iterable[AssayTransition],
    path: str | Path, *, allow_inferred_ownership: bool = False,
    allow_nonconformant: bool = False,
) -> Path:
    """Write semantically validated TraML and mandatory JSON; never overwrite.

    Semantic failures refuse publication unless ``allow_nonconformant=True``.
    That explicit override emits a UserWarning and records all diagnostics while
    preserving the native OpenMS output, including supplied charges/intensities.
    """
    rows, report = _prepare(dataset, sample, assays, allow_inferred_ownership)
    oms = _optional_module("pyopenms", "openms")
    experiment = _targeted_experiment(rows, oms)
    report["pyopenms_version"] = oms.__version__
    report["units"]["export_rt"] = "second"
    report["allow_nonconformant"] = allow_nonconformant

    def write_traml(staged: Path) -> None:
        writer = oms.TraMLFile()
        writer.store(str(staged), experiment)
        valid, errors, validation_warnings = writer.isSemanticallyValid(str(staged))
        report["semantic_validation"] = {
            "is_valid": valid, "errors": errors, "warnings": validation_warnings,
        }
        if not valid:
            message = "TraML semantic validation failed: " + "; ".join(errors)
            if not allow_nonconformant:
                raise ValueError(
                    message + "; pass allow_nonconformant=True to explicitly permit this output"
                )
            warnings.warn(message, UserWarning, stacklevel=2)

    return _publish(path, report, write_traml, "traml")


def export_skyline(
    dataset: Dataset, sample: int | str | Sample, assays: Iterable[AssayTransition],
    path: str | Path, *, allow_inferred_ownership: bool = False,
) -> Path:
    """Write a Skyline small-molecule transition CSV and mandatory JSON sidecar.

    Explicit Retention Time is in minutes. Roles, intensities and evidence remain
    in the sidecar, not invented CSV columns. No pyopenms installation is needed.
    """
    rows, report = _prepare(dataset, sample, assays, allow_inferred_ownership)
    report["units"]["export_rt"] = "minute"
    report["omissions"].append(
        "Roles, library intensities, evidence and native IDs are in the sidecar, "
        "not Skyline CSV."
    )

    def write_csv(staged: Path) -> None:
        with staged.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Molecule List Name", "Molecule Name", "Precursor m/z", "Product m/z",
                "Precursor Charge", "Product Charge", "Explicit Retention Time",
            ])
            for row in rows:
                writer.writerow([
                    row["compound_id"], row["compound_name"] or row["compound_id"],
                    row["precursor_mz"], row["product_mz"], row["precursor_charge"],
                    row["product_charge"], row["retention_time_seconds"] / 60,
                ])

    return _publish(path, report, write_csv, "skyline")
