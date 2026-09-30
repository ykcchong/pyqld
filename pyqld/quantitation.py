"""Explicit peak integration and linear external-standard quantitation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .model import Compound, Dataset, QuantitationResult, Sample


@dataclass(frozen=True)
class LinearCalibration:
    """Response = slope * concentration + intercept, followed by a factor."""

    slope: float
    intercept: float = 0.0
    concentration_factor: float = 1.0
    response: str = "area"

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value)
            for value in (
                self.slope,
                self.intercept,
                self.concentration_factor,
            )
        ):
            raise ValueError("calibration values must be finite")
        if self.slope <= 0 or self.concentration_factor <= 0:
            raise ValueError("slope and concentration factor must be positive")
        if self.response not in ("area", "height"):
            raise ValueError("response must be area or height")

    def concentration(self, response: float) -> float:
        if not math.isfinite(response) or response < 0:
            raise ValueError("response must be finite and nonnegative")
        value = (response - self.intercept) / self.slope * self.concentration_factor
        if not math.isfinite(value):
            raise ValueError("calculated concentration is not finite")
        return value

    @classmethod
    def fit(
        cls,
        concentrations: Sequence[float],
        responses: Sequence[float],
        *,
        weighting: str = "none",
        concentration_factor: float = 1.0,
        response: str = "area",
    ) -> LinearCalibration:
        """Fit explicit standards with none, 1/x, or 1/x^2 least-squares weights."""
        levels = np.asarray(concentrations, dtype=float)
        values = np.asarray(responses, dtype=float)
        if (
            levels.ndim != 1
            or values.shape != levels.shape
            or len(levels) < 2
            or not np.all(np.isfinite(levels))
            or not np.all(np.isfinite(values))
            or np.any(levels < 0)
            or np.any(values < 0)
            or np.ptp(levels) == 0
        ):
            raise ValueError("need finite, nonnegative standards at two distinct levels")
        if weighting not in ("none", "1/x", "1/x^2"):
            raise ValueError("unsupported calibration weighting")
        weights = None
        if weighting != "none":
            if np.any(levels <= 0):
                raise ValueError("weighted calibration needs positive concentrations")
            weights = 1 / (np.sqrt(levels) if weighting == "1/x" else levels)
        coefficients = np.polynomial.Polynomial.fit(levels, values, 1, w=weights).convert().coef
        return cls(
            float(coefficients[1]), float(coefficients[0]), concentration_factor, response
        )


@dataclass(frozen=True)
class PeakIntegration:
    start_time: float
    end_time: float
    apex_time: float
    area: float
    height: float
    start_height: float
    end_height: float
    baseline: str
    area_scale: float


def integrate_peak(
    times: Sequence[float],
    intensities: Sequence[float],
    start: float,
    end: float,
    *,
    baseline: str = "linear",
    area_scale: float = 1.0,
) -> PeakIntegration:
    """Integrate positive signal above baseline; times and area use minutes.

    ``area_scale=60`` converts intensity-minutes to intensity-seconds.
    The apex is the highest baseline-corrected point, not a fitted peak.
    """
    coordinates = np.asarray(times, dtype=float)
    signal = np.asarray(intensities, dtype=float)
    if (
        coordinates.ndim != 1
        or signal.shape != coordinates.shape
        or len(coordinates) < 2
        or not np.all(np.isfinite(coordinates))
        or not np.all(np.isfinite(signal))
        or np.any(np.diff(coordinates) <= 0)
    ):
        raise ValueError("trace needs matching finite arrays and strictly increasing times")
    if (
        not all(math.isfinite(value) for value in (start, end, area_scale))
        or not coordinates[0] <= start < end <= coordinates[-1]
        or area_scale <= 0
    ):
        raise ValueError(
            "boundaries must be ordered inside the trace; area scale must be positive"
        )
    if baseline not in ("linear", "zero"):
        raise ValueError("baseline must be linear or zero")
    selected = np.concatenate(
        ([start], coordinates[(coordinates > start) & (coordinates < end)], [end])
    )
    values = np.interp(selected, coordinates, signal)
    baseline_values = (
        np.interp(selected, [start, end], [values[0], values[-1]])
        if baseline == "linear"
        else np.zeros_like(values)
    )
    corrected = values - baseline_values
    crossing = np.flatnonzero(corrected[:-1] * corrected[1:] < 0)
    zeros = selected[crossing] - corrected[crossing] * (
        selected[crossing + 1] - selected[crossing]
    ) / (corrected[crossing + 1] - corrected[crossing])
    expanded = np.sort(np.concatenate((selected, zeros)))
    positive = np.maximum(np.interp(expanded, selected, corrected), 0)
    area = float(np.trapezoid(positive, expanded)) * area_scale
    apex = int(np.argmax(corrected))
    height = float(corrected[apex])
    if height <= 0 or area <= 0 or not math.isfinite(area):
        raise ValueError("selected window has no finite positive peak above baseline")
    return PeakIntegration(
        start,
        end,
        float(selected[apex]),
        area,
        height,
        float(baseline_values[0]),
        float(baseline_values[-1]),
        baseline,
        area_scale,
    )


def _stored_values(dataset: Dataset, values: dict[str, Any]) -> dict[str, Any]:
    fields = {field.key.lower(): field for field in dataset.qld.tables["QD2"].fields}
    stored = {}
    for key, value in values.items():
        if key not in fields:
            raise ValueError(f"result table lacks required field {key}")
        field = fields[key]
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"nonfinite calculated value for {key}")
            value = format(value, f".{field.decimals}f")
        field.encode(value)
        stored[key] = value
    return stored


def _calculated_values(
    dataset: Dataset,
    result: QuantitationResult,
    calibration: LinearCalibration,
    *,
    area: float | None = None,
    height: float | None = None,
) -> dict[str, Any]:
    if result.get("r_calc") != "ES":
        raise ValueError(f"row {result.index}: only external-standard responses are supported")
    if result.get("deleted_pk") or result.get("__deleted__"):
        raise ValueError(f"row {result.index}: cannot quantify a deleted peak")
    signal = (
        (result.area if area is None else area)
        if calibration.response == "area"
        else (result.height if height is None else height)
    )
    if signal is None:
        raise ValueError(f"row {result.index}: missing {calibration.response}")
    values: dict[str, Any] = {
        "response": signal,
        "abs_resp": signal,
        "anal_conc": calibration.concentration(signal),
        "valid_conc": True,
        "r_ah": "A" if calibration.response == "area" else "H",
        "mod_text": "pyqld linear external calibration; vendor QC not recomputed",
    }
    return _stored_values(dataset, values)


def requantitate(
    dataset: Dataset,
    calibration: LinearCalibration,
    *,
    sample: int | str | Sample | None = None,
    compound: int | str | Compound | None = None,
    result_indexes: Sequence[int] | None = None,
) -> Dataset:
    """Recalculate stored responses/concentrations, without reintegrating traces.

    Without explicit indexes, select named primary, non-deleted rows.
    One calibration applies to the entire selection; use a compound filter
    when compounds require different calibration models.
    """
    from .model import Dataset

    selected = dataset.results_for(sample, compound=compound, named=True)
    if result_indexes is not None:
        if (
            not result_indexes
            or any(type(index) is not int for index in result_indexes)
            or len(set(result_indexes)) != len(result_indexes)
        ):
            raise ValueError("provide distinct integer result indexes")
        selected = [result for result in selected if result.index in result_indexes]
        if len(selected) != len(result_indexes):
            raise ValueError("result indexes do not belong to the selected named results")
    else:
        selected = [
            result
            for result in selected
            if result.is_primary
            and not result.get("deleted_pk")
            and not result.get("__deleted__")
        ]
    if not selected:
        raise ValueError("no eligible results selected")
    changes = {
        result.index: _calculated_values(dataset, result, calibration) for result in selected
    }
    return Dataset(dataset.qld.with_rows("QD2", changes))


def edit_peak(
    dataset: Dataset,
    result: QuantitationResult,
    start: float,
    end: float,
    calibration: LinearCalibration,
    *,
    baseline: str = "linear",
    area_scale: float = 1.0,
) -> Dataset:
    """Reintegrate an existing result and recalculate its response/concentration."""
    from .model import Dataset

    if (
        not 0 <= result.index < len(dataset.results)
        or dataset.results[result.index] is not result
    ):
        raise ValueError("result belongs to a different dataset")
    pair = dataset[result.sample_id, result.compound_name]
    matches = [
        trace
        for trace in pair.chromatograms
        if trace.trace.strip() == result.transition.strip()
        and trace.function == result.function
    ]
    if len(matches) != 1:
        raise ValueError("result must have exactly one matching chromatogram")
    boundaries = _stored_values(dataset, {"start_rt": start, "end_rt": end})
    trace = matches[0]
    peak = integrate_peak(
        trace.times,
        trace.intensities,
        float(boundaries["start_rt"]),
        float(boundaries["end_rt"]),
        baseline=baseline,
        area_scale=area_scale,
    )
    values = _stored_values(
        dataset,
        {
            **boundaries,
            "found_rt": peak.apex_time,
            "st_height": peak.start_height,
            "en_height": peak.end_height,
            "area": peak.area,
            "height": peak.height,
            "st_scan": None,
            "en_scan": None,
            "found_scan": None,
        },
    )
    values.update(
        _calculated_values(
            dataset,
            result,
            calibration,
            area=float(values["area"]),
            height=float(values["height"]),
        )
    )
    return Dataset(dataset.qld.with_rows("QD2", {result.index: values}))
