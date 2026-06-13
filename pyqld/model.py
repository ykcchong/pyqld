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

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .dbf import DBFTable
from .ini import IniBlob, IniSection
from .qld import QLDFile, open_qld

# ---------------------------------------------------------------------------
# Record wrappers (dBase rows)
# ---------------------------------------------------------------------------


class _Record:
    """Thin wrapper around one dBase record (dict)."""

    __slots__ = ("_data", "index")

    def __init__(self, data: dict[str, Any], index: int) -> None:
        self._data = data
        self.index = index

    # dict-like access
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # attribute access
    def __getattr__(self, key: str) -> Any:
        try:
            return self._data[key]
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


class Compound(_Record):
    """One row of the compounds table (``QD3``)."""

    @property
    def name(self) -> str:
        return str(self._data.get("NAME", ""))

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
    def qual_trace(self) -> str:
        """Qualifier (secondary) trace if the method declares one."""
        return str(self._data.get("SEC_TRACE", "") or "")

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


class MethodCompound(_Record):
    """One row of the method-compounds table (``QD4``)."""

    @property
    def name(self) -> str:
        return str(self._data.get("NAME", ""))


class Peak(_Record):
    """One row of the peaks table (``QD2``)."""

    @property
    def sample_id(self) -> str:
        return str(self._data.get("SAMPLE_ID", ""))

    @property
    def compound_name(self) -> str:
        return str(self._data.get("NAME", ""))

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
    def trace(self) -> str:
        return _scalar(self._section, "Trace") or ""

    @property
    def description(self) -> str:
        return _scalar(self._section, "Desc") or ""

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

    def to_dict(self) -> dict[str, Any]:
        return self._section.to_dict()

    def __repr__(self) -> str:
        return (
            f"<Spectrum index={self.index} fn={self.function} "
            f"t={self.time:.3f} pts={self.points}>"
        )


# ---------------------------------------------------------------------------
# SampleCompound join view
# ---------------------------------------------------------------------------


@dataclass
class SampleCompound:
    """A ``(sample, compound)`` join: peaks, chromatograms, spectra."""

    dataset: Dataset
    sample: Sample
    compound: Compound

    # ---- peaks ---------------------------------------------------------

    @property
    def peaks(self) -> list[Peak]:
        sid = self.sample.id
        cname = self.compound.name
        return [
            p
            for p in self.dataset.peaks
            if p.sample_id == sid and p.compound_name == cname
        ]

    @property
    def primary_peak(self) -> Peak | None:
        peaks = self.peaks
        for p in peaks:
            if p.is_primary:
                return p
        return peaks[0] if peaks else None

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
    def qual_chromatogram(self) -> Chromatogram | None:
        """The qualifier chromatogram, or ``None`` if absent."""
        chrs = self.chromatograms
        if len(chrs) < 2 or not self.compound.has_qualifier:
            return None
        return chrs[1]

    def chromatogram(self, trace: str) -> Chromatogram | None:
        """Lookup a chromatogram by its ``Trace`` string."""
        target = trace.strip()
        for ch in self.chromatograms:
            if ch.trace.strip() == target:
                return ch
        return None

    def spectra(self, *, rt_window: float | None = None) -> list[Spectrum]:
        """Spectra whose ``Function`` matches the compound.

        With ``rt_window`` (minutes), only spectra within
        ``primary_peak.retention_time ± rt_window`` are returned.
        """
        fn = self.compound.function
        out = [s for s in self.dataset.spectra if s.function == fn]
        if rt_window is None:
            return out
        rt = self.retention_time
        if rt is None:
            return []
        lo, hi = rt - rt_window, rt + rt_window
        return [s for s in out if lo <= s.time <= hi]

    def __repr__(self) -> str:
        return (
            f"<SampleCompound sample={self.sample.id!r} "
            f"compound={self.compound.name!r} area={self.area} "
            f"rt={self.retention_time}>"
        )


# ---------------------------------------------------------------------------
# Method view
# ---------------------------------------------------------------------------


class Method:
    """Analytical method view: method-compounds (``QD4``) + audit (``qld``)."""

    def __init__(self, dataset: Dataset) -> None:
        self._ds = dataset
        tbl = dataset.qld.tables.get("QD4")
        if isinstance(tbl, DBFTable):
            self._compounds = [
                MethodCompound(r, i) for i, r in enumerate(tbl.records)
            ]
            self._table: DBFTable | None = tbl
        else:
            self._compounds = []
            self._table = None

    @property
    def compounds(self) -> list[MethodCompound]:
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
    def audit(self) -> list[IniSection]:
        """Top-level sections of the ``qld`` audit stream."""
        v = self._ds.qld.tables.get("qld")
        return v if isinstance(v, list) else []

    def compound(self, key: int | str) -> MethodCompound:
        if isinstance(key, int):
            return self._compounds[key]
        for c in self._compounds:
            if c.name == key:
                return c
        raise KeyError(key)

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
        self.samples: list[Sample] = (
            [Sample(r, i) for i, r in enumerate(s.records)]
            if isinstance(s, DBFTable)
            else []
        )

        # compounds
        c = qld.tables.get("QD3")
        self.compounds: list[Compound] = (
            [Compound(r, i) for i, r in enumerate(c.records)]
            if isinstance(c, DBFTable)
            else []
        )

        # peaks
        p = qld.tables.get("QD2")
        self.peaks: list[Peak] = (
            [Peak(r, i) for i, r in enumerate(p.records)]
            if isinstance(p, DBFTable)
            else []
        )

        # chromatograms / spectra
        cs = qld.tables.get("QD5")
        self.chromatograms: list[Chromatogram] = (
            [Chromatogram(sec, i) for i, sec in enumerate(cs)]
            if isinstance(cs, list)
            else []
        )
        sp = qld.tables.get("QD7")
        self.spectra: list[Spectrum] = (
            [Spectrum(sec, i) for i, sec in enumerate(sp)]
            if isinstance(sp, list)
            else []
        )

        # sample_info
        si = qld.tables.get("QD6")
        self.sample_info: list[IniSection] = si if isinstance(si, list) else []

        self.method = Method(self)

        # Lookup indices
        self._sample_by_id: dict[str, Sample] = {s.id: s for s in self.samples}
        self._sample_by_file: dict[str, Sample] = {
            s.file_name: s for s in self.samples
        }
        self._compound_by_name: dict[str, Compound] = {
            c.name: c for c in self.compounds
        }

        # Chromatogram layout
        self._per_sample_stride: int = 0
        self._chrom_index: list[dict[tuple[int, str], Chromatogram]] = []
        self._build_chrom_layout()

    # ---------------------------------------------------- internal layout

    def _build_chrom_layout(self) -> None:
        """Build a ``(function, trace) -> Chromatogram`` map per sample."""
        n_samples = len(self.samples) or 1
        n_chrs = len(self.chromatograms)
        stride = n_chrs // n_samples if n_samples else n_chrs
        self._per_sample_stride = stride
        self._chrom_index = [{} for _ in range(n_samples)]
        if not stride:
            return
        for si in range(n_samples):
            base = si * stride
            for k in range(stride):
                j = base + k
                if j >= n_chrs:
                    break
                ch = self.chromatograms[j]
                key = (ch.function, ch.trace.strip())
                self._chrom_index[si].setdefault(key, ch)

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

    def sample(self, key: int | str | Sample) -> Sample:
        """Lookup a sample by index, ID, file name, or existing :class:`Sample`."""
        if isinstance(key, Sample):
            return key
        if isinstance(key, int):
            return self.samples[key]
        if key in self._sample_by_id:
            return self._sample_by_id[key]
        if key in self._sample_by_file:
            return self._sample_by_file[key]
        raise KeyError(f"no sample with id/file_name {key!r}")

    def compound(self, key: int | str | Compound) -> Compound:
        """Lookup a compound by index, name, or existing :class:`Compound`."""
        if isinstance(key, Compound):
            return key
        if isinstance(key, int):
            return self.compounds[key]
        if key in self._compound_by_name:
            return self._compound_by_name[key]
        raise KeyError(f"no compound named {key!r}")

    def pair(
        self,
        sample: int | str | Sample,
        compound: int | str | Compound,
    ) -> SampleCompound:
        """Return a :class:`SampleCompound` view for the given pair."""
        return SampleCompound(
            self, self.sample(sample), self.compound(compound)
        )

    # --------------------------------------------------------- iteration

    def pairs(self) -> Iterator[SampleCompound]:
        """Yield every ``(sample, compound)`` view in the dataset."""
        for s in self.samples:
            for c in self.compounds:
                yield SampleCompound(self, s, c)

    def __repr__(self) -> str:
        return (
            f"<Dataset samples={len(self.samples)} "
            f"compounds={len(self.compounds)} peaks={len(self.peaks)} "
            f"chrs={len(self.chromatograms)} spectra={len(self.spectra)}>"
        )
