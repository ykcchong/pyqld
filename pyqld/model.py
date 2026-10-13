"""High-level object model for navigating a ``.qld`` file.

The :class:`Dataset` wraps a :class:`~pyqld.qld.QLDFile` and exposes
samples, compounds, peaks, chromatograms, spectra and the analytical
*method* via Python objects with attribute access.

Linkage rules (reverse-engineered from Waters TargetLynx output)
----------------------------------------------------------------

* Samples (``QD1``) and compounds (``QD3``) are ordered tables.
* Peaks (``QD2``) carry ``SAMPLE_ID`` (matching :attr:`Sample.id`) and
  ``NAME`` (matching :attr:`Compound.name`).
* Chromatograms (``QD5``) are emitted in sample-major order, keyed by
  ``(Function, Trace)``.
* Spectra (``QD7``) are matched by ``Function`` and optionally by
  retention-time window.
* The analytical method lives in ``QD4`` and the ``qld`` audit stream.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from .dbf import DBFTable
from .domain import (
    AcquisitionInfo,
    AuditEvent,
    CalibrationCurve,
    Integration,
    Quality,
)
from .ini import IniBlob, IniSection
from .qld import QLDFile, open_qld
from .quantitation import LinearCalibration

# ---------------------------------------------------------------------------
# Record wrappers (dBase rows)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4096)
def _snake_case(name: str) -> str:
    # Table rows reuse column names; bound retention across unrelated files.
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@lru_cache(maxsize=256)
def _record_keys(names: tuple[str, ...]) -> Mapping[str, str]:
    """Share immutable aliases for identical ordered row schemas."""
    return MappingProxyType({_snake_case(key): key for key in names})


class _Record(Mapping[str, Any]):
    """Read-only, snake-case mapping over one vendor table row."""

    __slots__ = ("_data", "_keys", "index")

    def __init__(self, data: dict[str, Any], index: int) -> None:
        self._data = data
        self._keys = _record_keys(tuple(data))
        self.index = index

    def __getitem__(self, key: str) -> Any:
        raw_key = key if key in self._data else self._keys.get(key)
        if raw_key is None:
            raise KeyError(key)
        return self._data[raw_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary with Python-style field names."""
        return {key: self._data[raw_key] for key, raw_key in self._keys.items()}

    @property
    def raw_fields(self) -> Mapping[str, Any]:
        """Original vendor fields for diagnostics and format research."""
        return MappingProxyType(self._data)

    def __getattr__(self, key: str) -> Any:
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e

    def __repr__(self) -> str:
        cls = type(self).__name__
        name = (
            self._data.get("NAME")
            or self._data.get("ID")
            or self._data.get("FILE_NAME")
            or ""
        )
        return f"<{cls} index={self.index} {name!r}>"


class Sample(_Record):
    """One row of the samples table (``QD1``)."""

    @property
    def id(self) -> str:
        return str(self._data.get("ID", ""))

    @property
    def file_name(self) -> str:
        return str(self._data.get("FILE_NAME", ""))

    @property
    def description(self) -> str:
        return str(self._data.get("FILE_TEXT", ""))

    @property
    def sample_type(self) -> str:
        return str(self._data.get("TYPE", ""))

    @property
    def vial(self) -> str:
        return str(self._data.get("BOTTLE", ""))

    @property
    def injection_volume(self) -> float | None:
        value = self._data.get("INJ_VOL")
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None


class Compound(_Record):
    """One row of the compounds table (``QD3``)."""

    @property
    def name(self) -> str:
        return str(self._data.get("NAME", ""))

    @property
    def cas_number(self) -> str:
        return str(self._data.get("CAS", ""))

    @property
    def units(self) -> str:
        return str(self._data.get("REP_UNITS", ""))

    @property
    def function(self) -> int:
        v = cast(str | int, self._data.get("RAW_FUNC"))
        try:
            return int(v) if v not in (None, "") else 0
        except (TypeError, ValueError):
            return 0

    @property
    def quan_trace(self) -> str:
        return str(self._data.get("QUAN_TRACE", ""))

    @property
    def quantifier_transition(self) -> str:
        return self.quan_trace

    @property
    def qual_trace(self) -> str:
        """Qualifier (secondary) trace if the method declares one."""
        return str(self._data.get("SEC_TRACE", "") or "")

    @property
    def qualifier_transition(self) -> str:
        return self.qual_trace

    @property
    def has_qualifier(self) -> bool:
        """True when the method emits a qualifier chromatogram."""
        return bool(self._data.get("SEC_EXIST", False)) and bool(self.qual_trace)

    @property
    def traces(self) -> list[str]:
        """Ordered list of MRM transitions (quan first, then qual)."""
        out = [self.quan_trace] if self.quan_trace else []
        if self.has_qualifier:
            out.append(self.qual_trace)
        return out

    @property
    def expected_retention_time(self) -> float | None:
        value = self._data.get("PRED_RT")
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @property
    def retention_window(self) -> float | None:
        value = self._data.get("RT_WIN")
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None


class MethodCompound(_Record):
    """One row of the method-compounds table (``QD4``)."""

    @property
    def name(self) -> str:
        return str(self._data.get("NAME", ""))

    @cached_property
    def calibration(self) -> CalibrationCurve:
        return CalibrationCurve.from_record(self)


class QuantitationResult(_Record):
    """One quantitative result row from QD2."""

    @property
    def sample_id(self) -> str:
        return str(self._data.get("SAMPLE_ID", ""))

    @property
    def compound_name(self) -> str:
        return str(self._data.get("NAME", ""))

    @property
    def analyte_name(self) -> str:
        return self.compound_name

    @property
    def function(self) -> int:
        value = self._data.get("RAW_FUNC")
        try:
            return int(value) if value not in (None, "") else 0
        except (TypeError, ValueError):
            return 0

    @property
    def transition(self) -> str:
        return str(self._data.get("QUAN_TRACE", ""))

    @property
    def units(self) -> str:
        return str(self._data.get("REP_UNITS", ""))

    @property
    def area(self) -> float | None:
        v = cast(str | int | float, self._data.get("AREA"))
        return float(v) if v not in (None, "") else None

    @property
    def height(self) -> float | None:
        v = cast(str | int | float, self._data.get("HEIGHT"))
        return float(v) if v not in (None, "") else None

    @property
    def retention_time(self) -> float | None:
        v = cast(str | int | float, self._data.get("FOUND_RT"))
        return float(v) if v not in (None, "") else None

    @property
    def is_primary(self) -> bool:
        return int(self._data.get("METH_PEAK", 0) or 0) == 0

    @property
    def response(self) -> float | None:
        value = self._data.get("RESPONSE")
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @property
    def concentration(self) -> float | None:
        value = self._data.get("ANAL_CONC")
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @cached_property
    def integration(self) -> Integration:
        return Integration.from_result(self)

    @cached_property
    def quality(self) -> Quality:
        return Quality.from_result(self)


Peak = QuantitationResult


# ---------------------------------------------------------------------------
# Helpers for INI-section data extraction
# ---------------------------------------------------------------------------


def _floats_from_section(section: IniSection, child_name: str) -> list[float]:
    """Extract a packed-float blob from a sub-section named ``child_name``."""
    for key, value in section.items:
        if key != child_name or not isinstance(value, IniSection):
            continue
        for k2, v2 in value.items:
            if (
                k2 == "__blob__"
                and isinstance(v2, IniBlob)
                and v2.payload_kind == "float32"
            ):
                return list(v2.payload)
    return []


def _scalar(section: IniSection, key: str) -> str | None:
    for k, v in section.items:
        if k == key and isinstance(v, str):
            return v
    return None


def _int(section: IniSection, key: str, default: int = 0) -> int:
    v = _scalar(section, key)
    try:
        return int(v) if v else default
    except (TypeError, ValueError):
        return default


def _float(section: IniSection, key: str, default: float = 0.0) -> float:
    v = _scalar(section, key)
    try:
        return float(v) if v else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Chromatogram / Spectrum wrappers
# ---------------------------------------------------------------------------


class Chromatogram:
    """One ``[Chromatogram]`` section from ``QD5``."""

    __slots__ = ("_section", "index")

    def __init__(self, section: IniSection, index: int) -> None:
        self._section = section
        self.index = index

    @property
    def section(self) -> IniSection:
        return self._section

    @property
    def function(self) -> int:
        return _int(self._section, "Function")

    @property
    def version(self) -> int:
        return _int(self._section, "Version")

    @property
    def trace(self) -> str:
        return _scalar(self._section, "Trace") or ""

    @property
    def description(self) -> str:
        return _scalar(self._section, "Desc") or ""

    @property
    def is_absolute(self) -> bool:
        return bool(_int(self._section, "Absolute"))

    @property
    def is_mass_spectrum(self) -> bool:
        return bool(_int(self._section, "IsMS"))

    @property
    def accurate_mass(self) -> float | None:
        value = _scalar(self._section, "AccMass")
        try:
            return float(value) if value else None
        except ValueError:
            return None

    @property
    def processing(self) -> str:
        return _scalar(self._section, "Process") or ""

    @property
    def trace_number(self) -> int:
        return _int(self._section, "TraceNumber")

    @property
    def scans(self) -> int:
        return _int(self._section, "Scans")

    @property
    def window(self) -> float:
        return _float(self._section, "Window")

    @property
    def times(self) -> list[float]:
        return _floats_from_section(self._section, "Times")

    @property
    def intensities(self) -> list[float]:
        return _floats_from_section(self._section, "Intensities")

    def __iter__(self) -> Iterator[tuple[float, float]]:
        return iter(zip(self.times, self.intensities, strict=False))

    def __len__(self) -> int:
        return min(len(self.times), len(self.intensities))

    def to_dict(self) -> dict[str, Any]:
        return self._section.to_dict()

    def __repr__(self) -> str:
        return (
            f"<Chromatogram index={self.index} fn={self.function} "
            f"trace={self.trace!r} scans={self.scans}>"
        )


class Spectrum:
    """One ``[Spectrum]`` section from ``QD7``."""

    __slots__ = ("_section", "index")

    def __init__(self, section: IniSection, index: int) -> None:
        self._section = section
        self.index = index

    @property
    def section(self) -> IniSection:
        return self._section

    @property
    def function(self) -> int:
        return _int(self._section, "Function")

    @property
    def version(self) -> int:
        return _int(self._section, "Version")

    @property
    def time(self) -> float:
        return _float(self._section, "Time")

    @property
    def points(self) -> int:
        return _int(self._section, "Points")

    @property
    def masses(self) -> list[float]:
        return _floats_from_section(self._section, "Masses")

    @property
    def intensities(self) -> list[float]:
        return _floats_from_section(self._section, "Intensities")

    def __iter__(self) -> Iterator[tuple[float, float]]:
        return iter(zip(self.masses, self.intensities, strict=False))

    def __len__(self) -> int:
        return min(len(self.masses), len(self.intensities))

    def to_dict(self) -> dict[str, Any]:
        return self._section.to_dict()

    def __repr__(self) -> str:
        return (
            f"<Spectrum index={self.index} fn={self.function} "
            f"t={self.time:.3f} pts={self.points}>"
        )


# ---------------------------------------------------------------------------
# CompoundResult join view
# ---------------------------------------------------------------------------


@dataclass
class CompoundResult:
    """A ``(sample, compound)`` join: results, chromatograms, and spectra."""

    dataset: Dataset
    sample: Sample
    compound: Compound

    # ---- peaks ---------------------------------------------------------

    @property
    def peaks(self) -> list[Peak]:
        return list(
            self.dataset._peaks_by_pair.get(
                (self.sample.id, self.compound.name), []
            )
        )

    @property
    def results(self) -> list[QuantitationResult]:
        """Quantitation results for this sample and compound."""
        return self.peaks

    @property
    def primary_peak(self) -> Peak | None:
        peaks = self.peaks
        for p in peaks:
            if p.is_primary:
                return p
        return peaks[0] if peaks else None

    @property
    def primary_result(self) -> QuantitationResult | None:
        return self.primary_peak

    @property
    def result(self) -> QuantitationResult | None:
        return self.primary_result

    @property
    def area(self) -> float | None:
        p = self.primary_peak
        return p.area if p else None

    @property
    def height(self) -> float | None:
        p = self.primary_peak
        return p.height if p else None

    @property
    def retention_time(self) -> float | None:
        p = self.primary_peak
        return p.retention_time if p else None

    # ---- chromatograms / spectra --------------------------------------

    @property
    def chromatograms(self) -> list[Chromatogram]:
        """All chromatograms for this pair, in method order."""
        return self.dataset._chromatograms_for(self.sample, self.compound)

    @property
    def quan_chromatogram(self) -> Chromatogram | None:
        """The quantifier chromatogram, or ``None`` if missing."""
        chrs = self.chromatograms
        return chrs[0] if chrs else None

    @property
    def quantifier_chromatogram(self) -> Chromatogram | None:
        return self.quan_chromatogram

    @property
    def qual_chromatogram(self) -> Chromatogram | None:
        """The qualifier chromatogram, or ``None`` if absent."""
        chrs = self.chromatograms
        if len(chrs) < 2 or not self.compound.has_qualifier:
            return None
        return chrs[1]

    @property
    def qualifier_chromatogram(self) -> Chromatogram | None:
        return self.qual_chromatogram

    def chromatogram(self, trace: str) -> Chromatogram | None:
        """Lookup a chromatogram by its ``Trace`` string."""
        target = trace.strip()
        for ch in self.chromatograms:
            if ch.trace.strip() == target:
                return ch
        return None

    def spectra(self, *, rt_window: float | None = None) -> list[Spectrum]:
        """Spectra for this sample whose function matches the compound.

        With ``rt_window`` (minutes), only spectra within
        ``primary_peak.retention_time ± rt_window`` are returned.
        """
        out = self.dataset.spectra_for(
            self.sample, function=self.compound.function
        )
        if rt_window is None:
            return out
        rt = self.retention_time
        if rt is None:
            return []
        lo, hi = rt - rt_window, rt + rt_window
        return [s for s in out if lo <= s.time <= hi]

    def __repr__(self) -> str:
        return (
            f"<CompoundResult sample={self.sample.id!r} "
            f"compound={self.compound.name!r} area={self.area} "
            f"rt={self.retention_time}>"
        )


SampleCompound = CompoundResult


# ---------------------------------------------------------------------------
# Method view
# ---------------------------------------------------------------------------


class Method:
    """Analytical method view: method-compounds (``QD4``) + audit (``qld``)."""

    def __init__(self, dataset: Dataset) -> None:
        self._ds = dataset
        tbl = dataset.qld.tables.get("QD4")
        if isinstance(tbl, DBFTable):
            self._compounds = tuple(
                MethodCompound(r, i) for i, r in enumerate(tbl.records)
            )
            self._table: DBFTable | None = tbl
        else:
            self._compounds = ()
            self._table = None

    @property
    def compounds(self) -> tuple[MethodCompound, ...]:
        return self._compounds

    @property
    def table(self) -> DBFTable | None:
        """The raw :class:`DBFTable` for ``method_compounds``."""
        return self._table

    @property
    def fields(self) -> list[dict[str, Any]]:
        if self._table is None:
            return []
        return [
            {
                "name": f.name,
                "type": f.type,
                "width": f.width,
                "decimals": f.decimals,
            }
            for f in self._table.fields
        ]

    @property
    def audit(self) -> tuple[IniSection, ...]:
        """Top-level sections of the ``qld`` audit stream."""
        v = self._ds.qld.tables.get("qld")
        return tuple(v) if isinstance(v, list) else ()

    @cached_property
    def calibration_curves(self) -> tuple[CalibrationCurve, ...]:
        return tuple(compound.calibration for compound in self._compounds)

    @cached_property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        events: list[AuditEvent] = []
        for top_level in self.audit:
            if top_level.name != "Audit":
                continue
            for _, value in top_level.items:
                if isinstance(value, IniSection) and value.name == "Audit":
                    events.append(AuditEvent.from_section(value))
        return tuple(events)

    def compound(self, key: int | str) -> MethodCompound:
        if isinstance(key, int):
            return self._compounds[key]
        for c in self._compounds:
            if c.name == key:
                return c
        raise KeyError(key)

    def get_compound(self, key: int | str) -> MethodCompound | None:
        try:
            return self.compound(key)
        except (IndexError, KeyError):
            return None

    def __getitem__(self, key: int | str) -> MethodCompound:
        return self.compound(key)

    def __iter__(self) -> Iterator[MethodCompound]:
        return iter(self._compounds)

    def __len__(self) -> int:
        return len(self._compounds)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class Dataset:
    """High-level wrapper around a parsed :class:`QLDFile`.

    Build via :meth:`from_path` or by passing an already-loaded :class:`QLDFile`.

    Examples
    --------
    >>> ds = Dataset.from_path("UMP 20260508 PS.qld")
    >>> ds.sample("UMP B")
    >>> ds.compound("5-HIAA")
    >>> pair = ds.pair("UMP B", "5-HIAA")
    >>> pair.area, pair.retention_time
    """

    def __init__(self, qld: QLDFile) -> None:
        self.qld = qld

        # samples
        s = qld.tables.get("QD1")
        self.samples: tuple[Sample, ...] = (
            tuple(Sample(r, i) for i, r in enumerate(s.records))
            if isinstance(s, DBFTable)
            else ()
        )

        # compounds
        c = qld.tables.get("QD3")
        self.compounds: tuple[Compound, ...] = (
            tuple(Compound(r, i) for i, r in enumerate(c.records))
            if isinstance(c, DBFTable)
            else ()
        )

        # quantitation results
        p = qld.tables.get("QD2")
        self.results: tuple[QuantitationResult, ...] = (
            tuple(QuantitationResult(r, i) for i, r in enumerate(p.records))
            if isinstance(p, DBFTable)
            else ()
        )
        self.peaks = self.results

        # chromatograms / spectra
        cs = qld.tables.get("QD5")
        self.chromatograms: tuple[Chromatogram, ...] = (
            tuple(Chromatogram(sec, i) for i, sec in enumerate(cs))
            if isinstance(cs, list)
            else ()
        )
        sp = qld.tables.get("QD7")
        self.spectra: tuple[Spectrum, ...] = (
            tuple(Spectrum(sec, i) for i, sec in enumerate(sp))
            if isinstance(sp, list)
            else ()
        )

        # sample_info
        si = qld.tables.get("QD6")
        self.sample_info: tuple[IniSection, ...] = (
            tuple(si) if isinstance(si, list) else ()
        )
        self.acquisitions: tuple[AcquisitionInfo, ...] = tuple(
            AcquisitionInfo.from_section(section, index)
            for index, section in enumerate(self.sample_info)
        )

        self.method = Method(self)

        # Lookup indices
        self._sample_by_id: dict[str, Sample] = {s.id: s for s in self.samples}
        self._sample_by_file: dict[str, Sample] = {
            s.file_name: s for s in self.samples
        }
        self._compound_by_name: dict[str, Compound] = {
            c.name: c for c in self.compounds
        }
        self._peaks_by_pair: dict[tuple[str, str], list[Peak]] = {}
        for peak in self.results:
            key = (peak.sample_id, peak.compound_name)
            self._peaks_by_pair.setdefault(key, []).append(peak)

        # Sample-owned media layouts
        self._chrom_index: list[dict[tuple[int, str], Chromatogram]] = []
        self._spectra_by_sample: list[list[Spectrum]] = []
        self._spectra_by_sample_function: list[dict[int, list[Spectrum]]] = []
        self._build_media_layouts()

    # ---------------------------------------------------- internal layout

    def _sample_table_sizes(
        self,
        top_level_name: str,
        child_name: str,
        total_items: int,
    ) -> list[int] | None:
        """Return validated per-sample table sizes from the audit stream."""
        n_samples = len(self.samples)
        if n_samples == 0:
            return [] if total_items == 0 else None
        if total_items == 0:
            return [0] * n_samples

        sizes: list[int | None] = [None] * n_samples
        for top_level in self.method.audit:
            if top_level.name != top_level_name:
                continue
            for key, value in top_level.items:
                if key != child_name or not isinstance(value, IniSection):
                    continue
                table_index = _int(value, "TableID") - 1
                table_size = _int(value, "TableSize", -1)
                if 0 <= table_index < n_samples and table_size >= 0:
                    sizes[table_index] = table_size

        if all(size is not None for size in sizes):
            resolved = [int(size) for size in sizes if size is not None]
            if sum(resolved) == total_items:
                return resolved

        if total_items % n_samples == 0:
            return [total_items // n_samples] * n_samples
        return None

    def _build_media_layouts(self) -> None:
        """Index chromatograms and spectra by their owning sample."""
        n_samples = len(self.samples)
        self._chrom_index = [{} for _ in range(n_samples)]
        self._spectra_by_sample = [[] for _ in range(n_samples)]
        self._spectra_by_sample_function = [{} for _ in range(n_samples)]

        chrom_sizes = self._sample_table_sizes(
            "Chromatograms", "SampleChromatograms", len(self.chromatograms)
        )
        if chrom_sizes is not None:
            offset = 0
            for sample_index, size in enumerate(chrom_sizes):
                for ch in self.chromatograms[offset : offset + size]:
                    key = (ch.function, ch.trace.strip())
                    self._chrom_index[sample_index].setdefault(key, ch)
                offset += size

        spectrum_sizes = self._sample_table_sizes(
            "Spectra", "SampleSpectra", len(self.spectra)
        )
        if spectrum_sizes is not None:
            offset = 0
            for sample_index, size in enumerate(spectrum_sizes):
                sample_spectra = list(
                    self.spectra[offset : offset + size]
                )
                self._spectra_by_sample[sample_index] = sample_spectra
                for spectrum in sample_spectra:
                    self._spectra_by_sample_function[sample_index].setdefault(
                        spectrum.function, []
                    ).append(spectrum)
                offset += size

    def _chromatograms_for(
        self, sample: Sample, compound: Compound
    ) -> list[Chromatogram]:
        if not self._chrom_index or not (
            0 <= sample.index < len(self._chrom_index)
        ):
            return []
        idx = self._chrom_index[sample.index]
        fn = compound.function
        out: list[Chromatogram] = []
        for trace in compound.traces:
            ch = idx.get((fn, trace.strip()))
            if ch is not None:
                out.append(ch)
        return out

    # --------------------------------------------------------- factories

    @classmethod
    def from_path(cls, path: str | Path) -> Dataset:
        """Open a ``.qld`` file and return a :class:`Dataset`.

        Parameters
        ----------
        path : str | Path
            Path to the ``.qld`` file.

        Returns
        -------
        Dataset
        """
        return cls(open_qld(path))

    # --------------------------------------------------------- lookups

    def with_changes(self, record: _Record, **values: Any) -> Dataset:
        """Return a rebuilt dataset with edited fields on a record owned by it."""
        collections = (
            ("QD1", self.samples), ("QD2", self.results),
            ("QD3", self.compounds), ("QD4", self.method.compounds),
        )
        for table, records in collections:
            if 0 <= record.index < len(records) and records[record.index] is record:
                return Dataset(self.qld.with_changes(table, record.index, **values))
        raise ValueError("record belongs to a different dataset")

    def save(self, path: str | Path, *, overwrite: bool = False) -> Path:
        """Save the current dataset as a QLD file without overwriting by default."""
        return self.qld.save(path, overwrite=overwrite)

    def edit_peak(
        self, result: QuantitationResult, start: float, end: float,
        calibration: LinearCalibration, *, baseline: str = "linear", area_scale: float = 1.0,
    ) -> Dataset:
        """Integrate new boundaries and quantify an existing, owned result row."""
        from .quantitation import edit_peak

        return edit_peak(self, result, start, end, calibration,
                         baseline=baseline, area_scale=area_scale)

    def requantitate(
        self, calibration: LinearCalibration, *, sample: int | str | Sample | None = None,
        compound: int | str | Compound | None = None, result_indexes: list[int] | None = None,
    ) -> Dataset:
        """Atomically recalculate selected results using an explicit calibration."""
        from .quantitation import requantitate

        return requantitate(self, calibration, sample=sample, compound=compound,
                            result_indexes=result_indexes)

    def compound_results(
        self, *, sample: int | str | Sample | None = None,
        compound: int | str | Compound | None = None,
    ) -> list[dict[str, Any]]:
        """Export all named result rows, retaining identifiers and peak multiplicity."""
        return [{
            "result_index": result.index, "sample_id": result.sample_id,
            "compound": result.compound_name, "function": result.function,
            "transition": result.transition, "primary": result.is_primary,
            "deleted": bool(result.get("deleted_pk") or result.get("__deleted__")),
            "start_rt": result.get("start_rt"), "retention_time": result.retention_time,
            "end_rt": result.get("end_rt"), "area": result.area, "height": result.height,
            "response": result.response, "concentration": result.concentration,
            "valid_concentration": result.get("valid_conc"), "units": result.units,
            "processing_note": result.get("mod_text", ""),
        } for result in self.results_for(sample, compound=compound, named=True)]

    def export_results(
        self, path: str | Path, *, format: str = "csv",
        sample: int | str | Sample | None = None, compound: int | str | Compound | None = None,
    ) -> Path:
        """Write current named compound results as CSV or JSON, refusing overwrite."""
        import csv
        import json

        if format not in ("csv", "json"):
            raise ValueError("result export format must be csv or json")
        rows = self.compound_results(sample=sample, compound=compound)
        destination = Path(path)
        with destination.open("x", encoding="utf-8", newline="") as output:
            if format == "json":
                json.dump(rows, output, ensure_ascii=False, allow_nan=False, indent=2)
            elif rows:
                writer = csv.DictWriter(output, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        return destination

    def sample(self, key: int | str | Sample) -> Sample:
        """Lookup a sample by index, ID, file name, or existing :class:`Sample`."""
        if isinstance(key, Sample):
            if (
                0 <= key.index < len(self.samples)
                and self.samples[key.index] is key
            ):
                return key
            raise ValueError("sample belongs to a different dataset")
        if isinstance(key, int):
            return self.samples[key]
        if key in self._sample_by_id:
            return self._sample_by_id[key]
        if key in self._sample_by_file:
            return self._sample_by_file[key]
        raise KeyError(f"no sample with id/file_name {key!r}")

    def get_sample(self, key: int | str | Sample) -> Sample | None:
        try:
            return self.sample(key)
        except (IndexError, KeyError, ValueError):
            return None

    def compound(self, key: int | str | Compound) -> Compound:
        """Lookup a compound by index, name, or existing :class:`Compound`."""
        if isinstance(key, Compound):
            if (
                0 <= key.index < len(self.compounds)
                and self.compounds[key.index] is key
            ):
                return key
            raise ValueError("compound belongs to a different dataset")
        if isinstance(key, int):
            return self.compounds[key]
        if key in self._compound_by_name:
            return self._compound_by_name[key]
        raise KeyError(f"no compound named {key!r}")

    def get_compound(
        self, key: int | str | Compound
    ) -> Compound | None:
        try:
            return self.compound(key)
        except (IndexError, KeyError, ValueError):
            return None

    def acquisition(self, sample: int | str | Sample) -> AcquisitionInfo | None:
        """Return acquisition metadata for a sample, when QD6 is present."""
        resolved = self.sample(sample)
        if 0 <= resolved.index < len(self.acquisitions):
            return self.acquisitions[resolved.index]
        return None

    def spectra_for(
        self,
        sample: int | str | Sample,
        *,
        function: int | None = None,
    ) -> list[Spectrum]:
        """Return spectra owned by a sample, optionally for one function."""
        resolved = self.sample(sample)
        if not 0 <= resolved.index < len(self._spectra_by_sample):
            return []
        if function is not None:
            return list(
                self._spectra_by_sample_function[resolved.index].get(
                    function, []
                )
            )
        return list(self._spectra_by_sample[resolved.index])

    def results_for(
        self,
        sample: int | str | Sample | None = None,
        *,
        compound: int | str | Compound | None = None,
        function: int | None = None,
        transition: str | None = None,
        named: bool | None = None,
    ) -> list[QuantitationResult]:
        """Filter quantitation results using domain values."""
        sample_id = self.sample(sample).id if sample is not None else None
        compound_name = (
            self.compound(compound).name if compound is not None else None
        )
        normalized_transition = transition.strip() if transition is not None else None

        return [
            result
            for result in self.results
            if (sample_id is None or result.sample_id == sample_id)
            and (compound_name is None or result.compound_name == compound_name)
            and (function is None or result.function == function)
            and (
                normalized_transition is None
                or result.transition.strip() == normalized_transition
            )
            and (named is None or bool(result.compound_name) is named)
        ]

    @property
    def unassigned_results(self) -> list[QuantitationResult]:
        """Results that carry no compound name in QD2."""
        return self.results_for(named=False)

    def pair(
        self,
        sample: int | str | Sample,
        compound: int | str | Compound,
    ) -> CompoundResult:
        """Return a :class:`CompoundResult` view for the given pair."""
        return CompoundResult(
            self, self.sample(sample), self.compound(compound)
        )

    def __getitem__(
        self, key: tuple[int | str | Sample, int | str | Compound]
    ) -> CompoundResult:
        sample, compound = key
        return self.pair(sample, compound)

    # --------------------------------------------------------- iteration

    def pairs(self) -> Iterator[CompoundResult]:
        """Yield every ``(sample, compound)`` view in the dataset."""
        for s in self.samples:
            for c in self.compounds:
                yield CompoundResult(self, s, c)

    def __iter__(self) -> Iterator[Sample]:
        return iter(self.samples)

    def __len__(self) -> int:
        return len(self.samples)

    def __repr__(self) -> str:
        return (
            f"<Dataset samples={len(self.samples)} "
            f"compounds={len(self.compounds)} peaks={len(self.peaks)} "
            f"chrs={len(self.chromatograms)} spectra={len(self.spectra)}>"
        )
