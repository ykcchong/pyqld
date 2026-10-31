"""Read-only diagnostics for decoded datasets and their retained source streams."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal

from .codec import _compression_error
from .dbf import DBFTable, parse_dbf
from .ini import parse_ini

if TYPE_CHECKING:
    from .model import Dataset


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Literal["error", "warning"]
    message: str
    stream: str | None = None
    index: int | None = None


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ValueError(
                "dataset validation failed: "
                + "; ".join(f"{issue.code}: {issue.message}" for issue in self.errors)
            )

    def to_dict(self) -> dict[str, Any]:
        return {"is_valid": self.is_valid, "issues": [asdict(issue) for issue in self.issues]}


def validate_dataset(dataset: Dataset) -> ValidationReport:
    from .model import Chromatogram
    from .qld import _iter_ole_streams

    issues: list[ValidationIssue] = []

    def add(code, message, stream=None, index=None, severity="error"):
        issues.append(ValidationIssue(code, severity, message, stream, index))

    # The original compressed bytes remain available even though ordinary reads
    # use tolerant decoding. Modified streams are checked in their current form.
    if dataset.qld._source is not None:
        try:
            for name, raw in _iter_ole_streams(dataset.qld._source):
                if name not in dataset.qld._modified:
                    error = _compression_error(raw)
                    if error:
                        add("compressed_stream", error, name)
        except (OSError, ValueError) as error:
            add("source_container", str(error))

    for name, table in dataset.qld.tables.items():
        if name in {"QD1", "QD2", "QD3", "QD4"} and not isinstance(table, DBFTable):
            add("stream_type", "expected a dBase table", name)
        elif name in {"QD5", "QD6", "QD7", "QD8", "qld"} and not isinstance(table, list):
            add("stream_type", "expected INI sections", name)
        elif table is None:
            add(
                "unknown_stream", "uninterpreted stream retained as raw bytes", name,
                severity="warning",
            )
        if isinstance(table, DBFTable):
            if name in dataset.qld.streams:
                try:
                    parse_dbf(dataset.qld.streams[name], strict=True)
                except ValueError as error:
                    add("dbf_structure", str(error), name)
            elif table.issues:
                for issue in table.issues:
                    add("dbf_structure", issue, name)
            if len(table.records) != table.nrecs:
                add(
                    "table_row_count",
                    f"declared {table.nrecs}, parsed {len(table.records)}",
                    name,
                )
        elif isinstance(table, list) and name in dataset.qld.streams:
            try:
                parse_ini(dataset.qld.streams[name], strict=True)
            except ValueError as error:
                add("ini_structure", str(error), name)

    for key, samples in dataset._samples_by_key.items():
        if len(samples) > 1:
            add(
                "ambiguous_sample_key",
                f"{key!r}: indexes {[s.index for s in samples]}",
                "QD1",
                severity="warning",
            )
    for result in dataset.unresolved_results:
        add(
            "result_ownership",
            f"cannot resolve injection for result {result.index}",
            "QD2",
            result.index,
        )
    peak_tables_present = any(
        key == "PeakList" for top in dataset.method.audit if top.name == "Peaks"
        for key, _ in top.items
    )
    if peak_tables_present and dataset._sample_table_sizes(
        "Peaks", "PeakList", len(dataset.results), allow_even_split=False
    ) is None:
        add("result_layout", "invalid or incomplete audited per-sample row counts", "QD2")
    for result in dataset.results:
        for key in ("area", "height", "found_rt", "response", "anal_conc", "meth_peak"):
            value = result.get(key)
            if value in (None, ""):
                continue
            try:
                if not math.isfinite(float(value)):
                    raise ValueError("nonfinite")
                if key == "meth_peak" and not float(value).is_integer():
                    raise ValueError("noninteger marker")
            except (TypeError, ValueError, OverflowError):
                add("invalid_result_value", f"invalid {key}: {value!r}", "QD2", result.index)
    for (sample_index, name), rows in dataset._peaks_by_pair.items():
        if not name:
            continue
        active = [row for row in rows if not row.is_deleted]
        count = sum(row.is_primary for row in active)
        if count > 1:
            add(
                "ambiguous_primary",
                f"sample {sample_index}, {name!r}: {count} primary rows",
                "QD2",
                active[0].index,
            )
        elif active and count == 0:
            add(
                "no_primary",
                f"sample {sample_index}, {name!r}: no active primary row",
                "QD2",
                active[0].index,
                "warning",
            )

    for top, child, items, stream in (
        ("Chromatograms", "SampleChromatograms", dataset.chromatograms, "QD5"),
        ("Spectra", "SampleSpectra", dataset.spectra, "QD7"),
    ):
        sizes = dataset._sample_table_sizes(top, child, len(items), allow_even_split=False)
        inferred = dataset._sample_table_sizes(top, child, len(items))
        if inferred is None:
            add("media_ownership", "cannot establish per-sample media boundaries", stream)
        elif sizes is None and len(dataset.samples) > 1:
            add(
                "inferred_media_ownership",
                "ownership uses equal sample-major groups",
                stream,
                severity="warning",
            )
        for item in items:
            if not isinstance(item, Chromatogram):
                try:
                    if item.retention_time is None:
                        add(
                            "spectrum_time", "missing spectrum retention time",
                            stream, item.index, "warning",
                        )
                except ValueError as error:
                    add("spectrum_time", str(error), stream, item.index)
            x = item.times if isinstance(item, Chromatogram) else item.masses
            y = item.intensities
            if not x and not y:
                add("missing_signal", "no decoded signal arrays", stream, item.index, "warning")
                continue
            if len(x) != len(y):
                add(
                    "signal_length",
                    f"coordinate/intensity lengths {len(x)} and {len(y)}",
                    stream,
                    item.index,
                )
            if not all(math.isfinite(value) for value in (*x, *y)):
                add(
                    "nonfinite_signal",
                    "signal arrays contain nonfinite values",
                    stream,
                    item.index,
                )
            if stream == "QD5" and any(b <= a for a, b in zip(x, x[1:], strict=False)):
                add(
                    "signal_time_order", "times are not strictly increasing", stream, item.index
                )
            declared = item.scans if isinstance(item, Chromatogram) else item.points
            if declared and declared != len(x):
                add(
                    "signal_count", f"declared {declared}, decoded {len(x)}", stream, item.index
                )

    for acquisition in dataset.acquisitions:
        try:
            functions = acquisition.functions
            if len(functions) != acquisition.number_of_functions:
                add(
                    "function_count",
                    f"declared {acquisition.number_of_functions}, recognized {len(functions)}",
                    "QD6",
                    acquisition.index,
                    "warning",
                )
            channels = acquisition.mrm_channels
            identities = Counter((c.function, c.transition) for c in channels)
            if any(count > 1 for count in identities.values()):
                add(
                    "ambiguous_channel",
                    "repeated function/transition pairs",
                    "QD6",
                    acquisition.index,
                )
            if acquisition.header and acquisition.acquired_at is None:
                add(
                    "acquisition_time",
                    "missing or unrecognized acquisition date/time",
                    "QD6",
                    acquisition.index,
                    "warning",
                )
        except ValueError as error:
            add("acquisition_report", str(error), "QD6", acquisition.index)
    for sample, index in zip(dataset.samples, dataset._chrom_index, strict=True):
        for function, transition in index:
            try:
                dataset._chromatogram_for(
                    sample,
                    function,
                    str(transition)
                    if isinstance(transition, str)
                    else f"{transition.precursor_mz}>{transition.product_mz}",
                )
            except ValueError as error:
                add(
                    "chromatogram_identity",
                    str(error),
                    "QD5",
                    index[(function, transition)][0].index,
                )
    return ValidationReport(tuple(issues))
