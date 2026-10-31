"""Explicit, optional bridges from retained QLD signals to MS software.

These are exports of stored signals, not reconstructions of raw acquisitions.
Importing this module does not import any optional MS package.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from .domain import AcquisitionChannel, MRMTransition
from .model import Chromatogram, Dataset, Sample, Spectrum

if TYPE_CHECKING:
    from matchms import Spectrum as MatchmsSpectrum
    from pyopenms import MSExperiment


@dataclass
class OpenMSConversion:
    """An independent mutable OpenMS experiment and JSON-ready conversion report."""

    experiment: MSExperiment
    report: dict[str, Any]


@dataclass(frozen=True)
class SpectrumMetadata:
    """Externally verified spectrum semantics, never inferred from QD7 arrays.

    Isolation offsets and precursor m/z are in Th; collision energy is in eV.
    ``evidence`` identifies the external method/raw-data evidence or user assertion.
    """

    ms_level: int
    spectrum_type: Literal["centroid", "profile"]
    polarity: Literal["positive", "negative"]
    evidence: str
    precursor_mz: float | None = None
    precursor_charge: int | None = None
    collision_energy_ev: float | None = None
    isolation_lower_offset: float | None = None
    isolation_upper_offset: float | None = None

    def __post_init__(self) -> None:
        if type(self.ms_level) is not int or self.ms_level < 1:
            raise ValueError("ms_level must be a positive integer")
        if self.spectrum_type not in {"centroid", "profile"}:
            raise ValueError("spectrum_type must be centroid or profile")
        if self.polarity not in {"positive", "negative"}:
            raise ValueError("polarity must be positive or negative")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("spectrum metadata requires nonempty evidence")
        precursor_values = (
            self.precursor_mz, self.precursor_charge, self.collision_energy_ev,
            self.isolation_lower_offset, self.isolation_upper_offset,
        )
        if self.ms_level == 1 and any(value is not None for value in precursor_values):
            raise ValueError("MS1 metadata must not contain precursor information")
        if self.ms_level > 1 and self.precursor_mz is None:
            raise ValueError("MSn metadata requires precursor_mz")
        if self.precursor_mz is not None:
            _finite(self.precursor_mz, "precursor_mz", positive=True)
        if self.precursor_charge is not None:
            charge = self.precursor_charge
            if type(charge) is not int or charge == 0:
                raise ValueError("precursor_charge must be a nonzero integer")
            if (charge > 0) != (self.polarity == "positive"):
                raise ValueError("precursor_charge conflicts with polarity")
        for name in ("collision_energy_ev", "isolation_lower_offset", "isolation_upper_offset"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)


def _optional_module(name: str, extra: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise ImportError(
            f"{name} is required for this export; install pyqld[{extra}] "
            "in a Python environment supported by that package"
        ) from error


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, not text or a boolean")
    value = float(value)
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        bound = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be finite and {bound}")
    return value


def _ownership(
    dataset: Dataset, stream: str, allow_inferred: bool,
) -> str:
    if stream == "QD5":
        evidence = dataset.media_ownership("QD5")
    elif stream == "QD7":
        evidence = dataset.media_ownership("QD7")
    else:
        raise ValueError(f"unsupported signal stream: {stream}")
    if evidence == "unresolved" or (evidence == "inferred" and not allow_inferred):
        raise ValueError(f"{stream} export requires established ownership; got {evidence}")
    return evidence


def _native_id(sample: Sample, stream: str, index: int) -> str:
    return f"pyqld_sample_{sample.index}_{stream.lower()}_{index}"


def _polarity(mode: str) -> str | None:
    return {
        "ES+": "positive", "ES-": "negative",
        "APCI+": "positive", "APCI-": "negative",
    }.get(mode.strip().upper())


def _signal_arrays(signal: Chromatogram) -> tuple[np.ndarray, np.ndarray]:
    times, values = signal.to_numpy()
    if not len(times):
        raise ValueError(f"chromatogram {signal.index}: no stored points")
    if times[0] < 0 or np.any(np.diff(times) <= 0):
        raise ValueError(f"chromatogram {signal.index}: times must increase and be nonnegative")
    if signal.scans and signal.scans != len(times):
        raise ValueError(f"chromatogram {signal.index}: declared scan count differs")
    seconds = times * 60
    if not np.all(np.isfinite(seconds)):
        raise ValueError(f"chromatogram {signal.index}: RT conversion overflow")
    if np.any(np.abs(values) > np.finfo(np.float32).max):
        raise ValueError(f"chromatogram {signal.index}: intensity exceeds OpenMS float32 range")
    return seconds, values


def _traces(
    dataset: Dataset, sample: Sample, allow_inferred: bool,
) -> list[tuple[Chromatogram, MRMTransition, AcquisitionChannel | None]]:
    _ownership(dataset, "QD5", allow_inferred)
    acquisition = dataset.acquisition(sample)
    output = []
    converted: dict[tuple[int, float, float], MRMTransition] = {}
    for trace in dataset.chromatograms_for(sample):
        transition = MRMTransition.parse(trace.trace)
        if transition is None:
            raise ValueError(
                f"chromatogram {trace.index}: unsupported non-MRM trace {trace.trace!r}"
            )
        if trace.function <= 0:
            raise ValueError(f"chromatogram {trace.index}: missing/invalid function")
        # This lookup rejects duplicate traces and conflicting acquisition evidence.
        dataset.chromatogram(sample, function=trace.function, transition=transition)
        _signal_arrays(trace)
        precursor = _finite(float(transition.precursor_mz), "precursor m/z", positive=True)
        product = _finite(float(transition.product_mz), "product m/z", positive=True)
        key = (trace.function, precursor, product)
        if key in converted and converted[key] != transition:
            raise ValueError("distinct Decimal transitions collapse to the same float64 m/z")
        converted[key] = transition
        channels = (
            acquisition.channels_for(function=trace.function, transition=transition)
            if acquisition is not None else ()
        )
        output.append((trace, transition, channels[0] if channels else None))
    return output


def _base_report(dataset: Dataset, sample: Sample) -> dict[str, Any]:
    from . import __version__

    validation = dataset.validate()
    structural = {
        "compressed_stream", "source_container", "table_row_count", "ini_structure",
        "dbf_structure", "stream_type",
    }
    errors = [issue for issue in validation.errors if issue.code in structural]
    if errors:
        raise ValueError("invalid source structure: " + "; ".join(i.message for i in errors))
    digest = hashlib.sha256()
    for name, payload in sorted(dataset.qld.streams.items()):
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "little"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "little"))
        digest.update(payload)
    acquisition = dataset.acquisition(sample)
    acquisition_metadata = None
    if acquisition is not None:
        acquired_at = acquisition.acquired_at
        acquisition_metadata = {
            "instrument": acquisition.instrument,
            "sample_id": acquisition.sample_id,
            "acquired_at": acquired_at.isoformat() if acquired_at is not None else None,
            "acquired_at_timezone": "unspecified",
            "number_of_functions": acquisition.number_of_functions,
            "report_text": acquisition.report.text,
        }
    return {
        "schema": "pyqld.interop/v1",
        "pyqld_version": __version__,
        "source": dataset.qld.path,
        "decoded_streams_sha256": digest.hexdigest() if dataset.qld.streams else None,
        "sample_index": sample.index,
        "sample_id": sample.id,
        "raw_file_name": sample.file_name,
        "acquisition": acquisition_metadata,
        "scope": "retained QLD signals; not a complete raw acquisition",
        "units": {
            "source_rt": "minute", "export_rt": "second",
            "intensity": "vendor signal; not rescaled",
            "stored_area": "vendor-reported; not rescaled",
        },
        "precision": {"coordinate": "float64", "openms_intensity": "float32"},
        "dataset_validation": validation.to_dict(),
        "warnings": [],
    }


def _spectra(
    dataset: Dataset, sample: Sample, metadata: Mapping[int, SpectrumMetadata],
    allow_inferred: bool,
) -> list[tuple[Spectrum, SpectrumMetadata, float]]:
    _ownership(dataset, "QD7", allow_inferred)
    spectra = dataset.spectra_for(sample)
    if any(type(index) is not int for index in metadata):
        raise ValueError("spectrum metadata keys must be integer QD7 indexes")
    if set(metadata) != {spectrum.index for spectrum in spectra}:
        raise ValueError("spectrum metadata must cover exactly this injection's QD7 indexes")
    if not spectra:
        raise ValueError("no retained spectra for this injection")
    output = []
    for spectrum in spectra:
        description = metadata[spectrum.index]
        if not isinstance(description, SpectrumMetadata):
            raise TypeError("spectrum metadata values must be SpectrumMetadata")
        retention_time = spectrum.retention_time
        if retention_time is None:
            raise ValueError(f"spectrum {spectrum.index}: missing retention time")
        seconds = _finite(retention_time * 60, "spectrum retention time")
        masses, values = spectrum.to_numpy()
        if not len(masses) or np.any(masses <= 0) or np.any(np.diff(masses) < 0):
            raise ValueError(f"spectrum {spectrum.index}: masses must be positive and sorted")
        if spectrum.points and spectrum.points != len(masses):
            raise ValueError(f"spectrum {spectrum.index}: declared point count differs")
        if spectrum.function <= 0:
            raise ValueError(f"spectrum {spectrum.index}: missing/invalid function")
        acquisition = dataset.acquisition(sample)
        if acquisition is not None:
            functions = [f for f in acquisition.functions if f.number == spectrum.function]
            if functions:
                polarity = _polarity(functions[0].ionization_mode)
                if polarity is not None and polarity != description.polarity:
                    raise ValueError(
                        f"spectrum {spectrum.index}: conflicting acquisition polarity"
                    )
        output.append((spectrum, description, seconds))
    return output


def to_openms(
    dataset: Dataset, sample: int | str | Sample, *,
    allow_inferred_ownership: bool = False,
    spectrum_metadata: Mapping[int, SpectrumMetadata] | None = None,
) -> OpenMSConversion:
    """Convert all retained MRM traces for one injection, without changing QLD.

    No traces are silently skipped. Spectra are excluded unless metadata covers
    every retained spectrum for this injection. Inferred media ownership needs
    explicit opt-in and is recorded in the report.
    """
    resolved = dataset.sample(sample)
    report = _base_report(dataset, resolved)
    traces = _traces(dataset, resolved, allow_inferred_ownership)
    spectra = (
        _spectra(dataset, resolved, spectrum_metadata, allow_inferred_ownership)
        if spectrum_metadata is not None else []
    )
    if not traces and not spectra:
        raise ValueError(f"sample {resolved.index}: no retained signals selected")
    oms = _optional_module("pyopenms", "openms")
    experiment = oms.MSExperiment()
    experiment.setMetaValue("pyqld:source", dataset.qld.path)
    experiment.setMetaValue("pyqld:sample_index", resolved.index)
    experiment.setMetaValue("pyqld:sample_id", resolved.id)
    experiment.setMetaValue("pyqld:raw_file_name", resolved.file_name)
    experiment.setMetaValue("pyqld:scope", report["scope"])
    if report["acquisition"] is not None:
        instrument = experiment.getInstrument()
        instrument.setName(report["acquisition"]["instrument"])
        experiment.setInstrument(instrument)
    report["pyopenms_version"] = oms.__version__
    report["ownership"] = {"QD5": dataset.media_ownership("QD5")}
    report["allow_inferred_ownership"] = allow_inferred_ownership
    report["chromatograms"] = []
    report["spectra"] = []
    if dataset.spectra and spectrum_metadata is None:
        report["warnings"].append(
            "QD7 spectra excluded; spectrum semantics require explicit input"
        )
    if dataset.media_ownership("QD5") == "inferred":
        report["warnings"].append("QD5 ownership inferred from equal sample-major groups")
    for trace, transition, channel in traces:
        native_id = _native_id(resolved, "QD5", trace.index)
        chromatogram = oms.MSChromatogram()
        chromatogram.setNativeID(native_id)
        chromatogram.setName(trace.description or trace.trace)
        chromatogram.setChromatogramType(
            oms.ChromatogramSettings.ChromatogramType.SELECTED_REACTION_MONITORING_CHROMATOGRAM
        )
        chromatogram.set_peaks(_signal_arrays(trace))
        precursor, product = oms.Precursor(), oms.Product()
        precursor.setMZ(float(transition.precursor_mz))
        product.setMZ(float(transition.product_mz))
        chromatogram.setPrecursor(precursor)
        chromatogram.setProduct(product)
        metadata: dict[str, Any] = {
            "sample_index": resolved.index, "source_index": trace.index,
            "function": trace.function, "trace": trace.trace,
            "precursor_mz_decimal": str(transition.precursor_mz),
            "product_mz_decimal": str(transition.product_mz),
            "processing": trace.processing,
        }
        if channel is not None:
            metadata.update({
                "channel": channel.number, "ionization_mode": channel.ionization_mode,
                "collision_energy_reported": str(channel.collision_energy),
                "collision_energy_unit": "unverified",
                "delay_reported": channel.delay,
            })
            if channel.dwell_time is not None:
                metadata["dwell_time_seconds"] = _finite(
                    float(channel.dwell_time), "dwell time",
                )
            if channel.cone_voltage is not None:
                voltage = float(channel.cone_voltage)
                if not math.isfinite(voltage):
                    raise ValueError("cone voltage must be finite")
                metadata["cone_voltage_volts"] = voltage
            polarity = _polarity(channel.ionization_mode)
            if polarity is not None:
                settings = chromatogram.getInstrumentSettings()
                settings.setPolarity(
                    oms.IonSource.Polarity.POSITIVE if polarity == "positive"
                    else oms.IonSource.Polarity.NEGATIVE
                )
                chromatogram.setInstrumentSettings(settings)
            else:
                report["warnings"].append(f"{native_id}: unrecognized polarity")
        else:
            report["warnings"].append(f"{native_id}: no matching acquisition channel")
        for key, value in metadata.items():
            chromatogram.setMetaValue(f"pyqld:{key}", value)
        experiment.addChromatogram(chromatogram)
        report["chromatograms"].append({
            "native_id": native_id, "points": len(trace), **metadata,
        })
    if spectra:
        report["ownership"]["QD7"] = dataset.media_ownership("QD7")
        if dataset.media_ownership("QD7") == "inferred":
            report["warnings"].append("QD7 ownership inferred from equal sample-major groups")
    for source, description, seconds in spectra:
        masses, values = source.to_numpy()
        if np.any(np.abs(values) > np.finfo(np.float32).max):
            raise ValueError(f"spectrum {source.index}: intensity exceeds OpenMS float32 range")
        spectrum = oms.MSSpectrum()
        native_id = _native_id(resolved, "QD7", source.index)
        spectrum.setNativeID(native_id)
        # Some OpenMS bindings require writable buffers even though set_peaks copies.
        spectrum.set_peaks((masses.copy(), values.copy()))
        spectrum.setRT(seconds)
        spectrum.setMSLevel(description.ms_level)
        spectrum.setType(
            oms.SpectrumSettings.SpectrumType.CENTROID
            if description.spectrum_type == "centroid"
            else oms.SpectrumSettings.SpectrumType.PROFILE
        )
        settings = spectrum.getInstrumentSettings()
        settings.setPolarity(
            oms.IonSource.Polarity.POSITIVE if description.polarity == "positive"
            else oms.IonSource.Polarity.NEGATIVE
        )
        spectrum.setInstrumentSettings(settings)
        if description.precursor_mz is not None:
            precursor = oms.Precursor()
            precursor.setMZ(description.precursor_mz)
            if description.precursor_charge is not None:
                precursor.setCharge(description.precursor_charge)
            if description.collision_energy_ev is not None:
                precursor.setActivationEnergy(description.collision_energy_ev)
            if description.isolation_lower_offset is not None:
                precursor.setIsolationWindowLowerOffset(description.isolation_lower_offset)
            if description.isolation_upper_offset is not None:
                precursor.setIsolationWindowUpperOffset(description.isolation_upper_offset)
            spectrum.setPrecursors([precursor])
        spectrum.setMetaValue("pyqld:source_index", source.index)
        spectrum.setMetaValue("pyqld:function", source.function)
        spectrum.setMetaValue("pyqld:metadata_evidence", description.evidence)
        experiment.addSpectrum(spectrum)
        report["spectra"].append({
            "native_id": native_id, "source_index": source.index,
            "function": source.function, "points": len(source),
            "metadata_source": "caller supplied", **asdict(description),
        })
    return OpenMSConversion(experiment, report)


def _write_outputs(outputs: Mapping[Path, Callable[[Path], None]]) -> None:
    """Stage outputs before publishing; never overwrite, roll back our links on failure."""
    destinations = list(outputs)
    if not destinations:
        raise ValueError("no outputs requested")
    parent = destinations[0].parent
    if any(path.parent != parent for path in destinations):
        raise ValueError("linked exports must share a destination directory")
    for path in destinations:
        if os.path.lexists(path):
            raise FileExistsError(path)
    published: list[Path] = []
    committed = False
    with tempfile.TemporaryDirectory(prefix=".pyqld-export-", dir=parent) as temporary:
        try:
            for path, writer in outputs.items():
                staged = Path(temporary) / path.name
                writer(staged)
                with staged.open("rb") as handle:
                    os.fsync(handle.fileno())
            for path in destinations:
                os.link(Path(temporary) / path.name, path)
                published.append(path)
            committed = True
        finally:
            if not committed:
                for path in published:
                    try:
                        published_stat = path.lstat()
                    except FileNotFoundError:
                        continue
                    staged_stat = (Path(temporary) / path.name).stat()
                    if os.path.samestat(published_stat, staged_stat):
                        path.unlink()


def export_mzml(
    dataset: Dataset, sample: int | str | Sample, path: str | Path, *,
    allow_inferred_ownership: bool = False,
    spectrum_metadata: Mapping[int, SpectrumMetadata] | None = None,
) -> Path:
    """Write one injection's chromatogram mzML and a mandatory ``.mzML.json`` report.

    Both destinations must be absent. Stored areas/concentrations are not
    converted into intensities. The report records omissions and export units.
    """
    conversion = to_openms(
        dataset, sample, allow_inferred_ownership=allow_inferred_ownership,
        spectrum_metadata=spectrum_metadata,
    )
    destination = Path(path)
    sidecar = destination.with_name(destination.name + ".json")
    oms = _optional_module("pyopenms", "openms")

    def write_mzml(staged: Path) -> None:
        oms.MzMLFile().store(str(staged), conversion.experiment)
        conversion.report["mzml"] = {
            "file": destination.name,
            "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
        }

    def write_report(staged: Path) -> None:
        staged.write_text(
            json.dumps(conversion.report, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )

    _write_outputs({destination: write_mzml, sidecar: write_report})
    return destination


def to_matchms(
    dataset: Dataset, sample: int | str | Sample,
    metadata: Mapping[int, SpectrumMetadata], *, allow_inferred_ownership: bool = False,
) -> list[MatchmsSpectrum]:
    """Convert explicitly verified centroid MS2 spectra; no peak processing is applied."""
    resolved = dataset.sample(sample)
    _base_report(dataset, resolved)
    spectra = _spectra(dataset, resolved, metadata, allow_inferred_ownership)
    for source, description, _ in spectra:
        if description.ms_level != 2 or description.spectrum_type != "centroid":
            raise ValueError("matchms export requires verified centroid MS2 spectra")
        if np.any(source.to_numpy()[1] < 0):
            raise ValueError(
                f"spectrum {source.index}: matchms requires nonnegative intensities"
            )
    matchms = _optional_module("matchms", "matchms")
    output = []
    for source, description, seconds in spectra:
        masses, values = source.to_numpy()
        attributes = {
            "spectrum_id": _native_id(resolved, "QD7", source.index),
            "precursor_mz": description.precursor_mz,
            "ms_level": description.ms_level, "ionmode": description.polarity,
            "retention_time": seconds,
            "retention_time_unit": "second", "pyqld_source": dataset.qld.path,
            "pyqld_sample_index": resolved.index,
            "pyqld_metadata_evidence": description.evidence,
            "pyqld_ownership": dataset.media_ownership("QD7"),
            "pyqld_spectrum_metadata": asdict(description),
        }
        if description.precursor_charge is not None:
            attributes["charge"] = description.precursor_charge
        output.append(matchms.Spectrum(
            mz=masses.copy(), intensities=values.copy(), metadata=attributes,
            metadata_harmonization=False,
        ))
    return output


def quantitation_report(
    dataset: Dataset, sample: int | str | Sample, *, allow_inferred_ownership: bool = False,
) -> dict[str, Any]:
    """Preserve all injection result rows, QC, method records and batch audit data.

    No primary-row selection or numeric rescaling occurs. Signal references
    use the same IDs as mzML; missing and multiple links remain explicit.
    """
    resolved = dataset.sample(sample)
    report = _base_report(dataset, resolved)
    _ownership(dataset, "QD5", allow_inferred_ownership)
    traces = dataset.chromatograms_for(resolved)
    report["ownership"] = {"QD5": dataset.media_ownership("QD5")}
    report["allow_inferred_ownership"] = allow_inferred_ownership
    if dataset.media_ownership("QD5") == "inferred":
        report["warnings"].append("QD5 ownership inferred from equal sample-major groups")
    report["units"]["result_rt"] = "minute"
    report["units"]["concentration"] = "per result's vendor units; not rescaled"
    report["sample"] = resolved.to_dict()
    report["results"] = []
    for result in dataset.results_for(resolved):
        transition = MRMTransition.parse(result.transition)
        matches = [
            trace for trace in traces if trace.function == result.function
            and (
                MRMTransition.parse(trace.trace) == transition if transition is not None
                else trace.trace == result.transition
            )
        ]
        report["results"].append({
            "result_index": result.index, "fields": result.to_dict(),
            "primary": result.is_primary, "deleted": result.is_deleted,
            "integration": asdict(result.integration), "quality": asdict(result.quality),
            "chromatogram_ids": [_native_id(resolved, "QD5", t.index) for t in matches],
            "signal_link": "unique" if len(matches) == 1 else "missing" if not matches
            else "ambiguous",
        })
    report["compounds"] = [
        {"compound_index": compound.index, "fields": compound.to_dict()}
        for compound in dataset.compounds
    ]
    report["method"] = [
        {
            "method_index": compound.index, "fields": compound.to_dict(),
            "calibration": asdict(compound.calibration),
        }
        for compound in dataset.method.compounds
    ]
    report["audit_scope"] = "whole batch; not filtered to this injection"
    report["audit"] = [section.to_dict() for section in dataset.method.audit]
    return report


def export_quantitation(
    dataset: Dataset, sample: int | str | Sample, path: str | Path, *,
    allow_inferred_ownership: bool = False,
) -> Path:
    """Write linked quantitation/provenance JSON, refusing existing destinations."""
    report = quantitation_report(
        dataset, sample, allow_inferred_ownership=allow_inferred_ownership,
    )
    text = json.dumps(report, indent=2, allow_nan=False) + "\n"
    destination = Path(path)

    def write_report(staged: Path) -> None:
        staged.write_text(text, encoding="utf-8")

    _write_outputs({destination: write_report})
    return destination
