"""Pythonic domain values built from decoded TargetLynx records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from .ini import IniBlob, IniSection


@lru_cache(maxsize=4096)
def _snake_case(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in {"1", "T", "TRUE", "Y", "YES"}
    return bool(value)


@dataclass(frozen=True)
class Integration:
    """Integrated peak boundaries and measurements."""

    start_time: float | None
    apex_time: float | None
    end_time: float | None
    area: float | None
    height: float | None
    start_height: float | None
    end_height: float | None
    start_scan: int | None
    apex_scan: int | None
    end_scan: int | None

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> Integration:
        return cls(
            start_time=_as_float(result.get("start_rt")),
            apex_time=_as_float(result.get("found_rt")),
            end_time=_as_float(result.get("end_rt")),
            area=_as_float(result.get("area")),
            height=_as_float(result.get("height")),
            start_height=_as_float(result.get("st_height")),
            end_height=_as_float(result.get("en_height")),
            start_scan=_as_int(result.get("st_scan")),
            apex_scan=_as_int(result.get("found_scan")),
            end_scan=_as_int(result.get("en_scan")),
        )


@dataclass(frozen=True)
class Quality:
    """Peak quality, qualifier, and detection-limit measurements."""

    flags: int
    peak_quality: float | None
    peak_quality_text: str
    signal_to_noise: float | None
    chromatogram_noise: float | None
    ion_ratio: float | None
    expected_ion_ratio: float | None
    ion_ratio_window: float | None
    below_detection_limit: bool
    below_quantitation_limit: bool
    below_minimum_area: bool
    below_minimum_height: bool
    above_linear_limit: bool
    ion_ratio_out_of_range: bool
    response_out_of_range: bool

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> Quality:
        return cls(
            flags=_as_int(result.get("pk_flags")) or 0,
            peak_quality=_as_float(result.get("pk_quality")),
            peak_quality_text=str(result.get("pk_qualstr") or ""),
            signal_to_noise=_as_float(result.get("sig_noise")),
            chromatogram_noise=_as_float(result.get("chro_noise")),
            ion_ratio=_as_float(result.get("actual_ir")),
            expected_ion_ratio=_as_float(result.get("ion_ratio")),
            ion_ratio_window=_as_float(result.get("ir_win")),
            below_detection_limit=_as_bool(result.get("lod_flag")),
            below_quantitation_limit=_as_bool(result.get("loq_flag")),
            below_minimum_area=_as_bool(result.get("min_a_flag")),
            below_minimum_height=_as_bool(result.get("min_h_flag")),
            above_linear_limit=_as_bool(result.get("lol_flag")),
            ion_ratio_out_of_range=_as_bool(result.get("irwin_flag")),
            response_out_of_range=_as_bool(result.get("resp_flag")),
        )


@dataclass(frozen=True)
class CalibrationPoint:
    """One packed TargetLynx calibration point."""

    concentration: float
    response: float
    calculated_concentration: float
    status: int

    @classmethod
    def parse(cls, value: str) -> CalibrationPoint | None:
        parts = value.split()
        if len(parts) != 4:
            return None
        try:
            return cls(float(parts[0]), float(parts[1]), float(parts[2]), int(parts[3]))
        except ValueError:
            return None


@dataclass(frozen=True)
class CalibrationCurve:
    """Calibration configuration and active points for one compound."""

    compound_name: str
    model: str
    weighting: str
    origin: str
    valid: bool
    coefficients: tuple[float, ...]
    points: tuple[CalibrationPoint, ...]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CalibrationCurve:
        number_of_points = _as_int(record.get("num_pts")) or 0
        points: list[CalibrationPoint] = []
        for number in range(1, min(number_of_points, 100) + 1):
            key = f"cali_pt_{number}" if number < 100 else "cali_pt100"
            point = CalibrationPoint.parse(str(record.get(key) or ""))
            if point is not None:
                points.append(point)

        coefficients: list[float] = []
        for value in str(record.get("curv_coefs") or "").split():
            try:
                coefficients.append(float(value))
            except ValueError:
                continue

        return cls(
            compound_name=str(record.get("name") or ""),
            model=str(record.get("c_calc") or ""),
            weighting=str(record.get("weight_fnc") or ""),
            origin=str(record.get("origin") or ""),
            valid=_as_bool(record.get("cc_valid")),
            coefficients=tuple(coefficients),
            points=tuple(points),
        )


ReportValue = str | tuple[str, ...]
_REPORT_SECTION = re.compile(r"--\s*(.+?)\s*--")


class AcquisitionReport(Mapping[str, Mapping[str, ReportValue]]):
    """Sectioned acquisition report embedded in QD6."""

    def __init__(self, text: str) -> None:
        self.text = text
        parsed: dict[str, dict[str, ReportValue]] = {}
        section_name = "general"
        current = parsed.setdefault(section_name, {})

        for line in text.splitlines():
            stripped = line.strip()
            section_match = (
                _REPORT_SECTION.fullmatch(stripped) if stripped.startswith("--") else None
            )
            if section_match:
                name = section_match.group(1).strip().lower()
                if name.startswith("end "):
                    section_name = "general"
                else:
                    section_name = _snake_case(name)
                current = parsed.setdefault(section_name, {})
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            normalized = _snake_case(key)
            if not normalized:
                continue
            value = value.strip()
            existing = current.get(normalized)
            if existing is None:
                current[normalized] = value
            elif isinstance(existing, tuple):
                current[normalized] = (*existing, value)
            else:
                current[normalized] = (existing, value)

        self._sections = MappingProxyType(
            {name: MappingProxyType(values) for name, values in parsed.items()}
        )

    def __getitem__(self, key: str) -> Mapping[str, ReportValue]:
        return self._sections[key]

    def __iter__(self):
        return iter(self._sections)

    def __len__(self) -> int:
        return len(self._sections)


@dataclass(frozen=True)
class AcquisitionInfo:
    """Acquisition metadata and reports for one sample."""

    index: int
    identifier: int | None
    number_of_functions: int
    report: AcquisitionReport
    header_bytes: bytes

    @property
    def header(self) -> Mapping[str, ReportValue]:
        return self.report.get("header", {})

    @property
    def sample_id(self) -> str:
        return str(self.header.get("sample_id") or "")

    @property
    def instrument(self) -> str:
        return str(self.header.get("instrument") or "")

    @property
    def acquired_at(self) -> datetime | None:
        date = self.header.get("acquired_date")
        time = self.header.get("acquired_time")
        if not isinstance(date, str) or not isinstance(time, str):
            return None
        try:
            return datetime.strptime(f"{date} {time}", "%d-%b-%Y %H:%M:%S")
        except ValueError:
            return None

    @classmethod
    def from_section(cls, section: IniSection, index: int) -> AcquisitionInfo:
        identifier: int | None = None
        number_of_functions = 0
        experiment_text = ""
        header_bytes = b""

        for key, value in section.items:
            if key == "ID" and isinstance(value, str):
                identifier = _as_int(value)
            elif key == "NumFunctions" and isinstance(value, str):
                number_of_functions = _as_int(value) or 0
            elif isinstance(value, IniSection):
                for child_key, child_value in value.items:
                    if child_key != "__blob__" or not isinstance(child_value, IniBlob):
                        continue
                    payload = child_value.payload
                    if key == "Experiment" and isinstance(payload, str):
                        experiment_text = payload
                    elif key == "Header":
                        header_bytes = (
                            payload.encode("latin-1")
                            if isinstance(payload, str)
                            else bytes(payload)
                        )

        return cls(
            index=index,
            identifier=identifier,
            number_of_functions=number_of_functions,
            report=AcquisitionReport(experiment_text),
            header_bytes=header_bytes,
        )


_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class AuditEvent:
    """One timestamped event from the TargetLynx audit trail."""

    id: int
    timestamp: datetime | None
    event: str
    name: str
    comment: str
    details: str
    sample_index: int | None
    peak_index: int | None
    area: float | None
    start_time: float | None
    apex_time: float | None
    end_time: float | None
    signed: bool

    @classmethod
    def from_section(cls, section: IniSection) -> AuditEvent:
        values = section.to_dict()
        raw_time = _as_int(values.get("Time"))
        timestamp = None
        if raw_time is not None and raw_time >= 0:
            timestamp = _FILETIME_EPOCH + timedelta(microseconds=raw_time // 10)

        sample_index = _as_int(values.get("SampleID"))
        peak_index = _as_int(values.get("PeakID"))
        return cls(
            id=_as_int(values.get("ID")) or 0,
            timestamp=timestamp,
            event=str(values.get("Event") or ""),
            name=str(values.get("Name") or ""),
            comment=str(values.get("Com") or ""),
            details=str(values.get("Details") or ""),
            sample_index=(
                sample_index
                if sample_index is not None and sample_index >= 0
                else None
            ),
            peak_index=peak_index if peak_index is not None and peak_index >= 0 else None,
            area=_as_float(values.get("Area")),
            start_time=_as_float(values.get("StartRT")),
            apex_time=_as_float(values.get("TopRT")),
            end_time=_as_float(values.get("EndRT")),
            signed=_as_bool(values.get("Sign")),
        )
