"""Safe array access, time windows and sample-scoped trace queries."""

import pickle

import numpy as np
import pytest

from pyqld import Chromatogram, Dataset, MRMTransition, QLDFile, Spectrum
from pyqld.dbf import DBFTable
from pyqld.ini import IniBlob, IniSection


def _signal(kind="Chromatogram", x=(0.0, 1.0, 2.0), y=(0.0, 4.0, 0.0)):
    section = IniSection(kind, [("Function", "3"), ("Trace", "100 > 50")])
    for key, values in [
        ("Times" if kind == "Chromatogram" else "Masses", x),
        ("Intensities", y),
    ]:
        section.add(
            key,
            IniSection(key, [("__blob__", IniBlob(4 * len(values), list(values), "float32"))]),
        )
    return (Chromatogram if kind == "Chromatogram" else Spectrum)(section, 0)


@pytest.mark.parametrize("kind", ["Chromatogram", "Spectrum"])
def test_numpy_arrays_are_cached_and_cannot_be_made_writeable(kind) -> None:
    signal = _signal(kind)
    x, y = signal.to_numpy()
    assert signal.to_numpy()[0] is x
    assert signal.to_numpy()[1] is y
    assert x.dtype == np.float64
    with pytest.raises(ValueError):
        y[1] = 99
    with pytest.raises(ValueError):
        y.setflags(write=True)
    copy = signal.intensities
    copy[1] = 99
    assert signal.intensities[1] == 4
    assert list(signal) == [(0, 0), (1, 4), (2, 0)]
    restored = pickle.loads(pickle.dumps(signal))
    assert not restored.to_numpy()[0].flags.writeable
    assert np.array_equal(restored.to_numpy()[1], y)


def test_time_window_is_inclusive_and_returns_readonly_views() -> None:
    trace = _signal()
    x, y = trace.time_window(0.5, 2)
    assert list(x) == [1, 2]
    assert list(y) == [4, 0]
    assert np.shares_memory(y, trace.to_numpy()[1])
    assert not y.flags.writeable
    assert len(trace.time_window(3, 4)[0]) == 0
    assert list(trace.time_window(1, 1)[0]) == [1]
    for start, end in [(2, 1), (float("nan"), 1)]:
        with pytest.raises(ValueError, match="boundaries"):
            trace.time_window(start, end)
    with pytest.raises(ValueError, match="increasing"):
        _signal(x=(1, 0, 2)).time_window(0, 2)


@pytest.mark.parametrize("kind", ["Chromatogram", "Spectrum"])
def test_corrupt_signals_raise_instead_of_truncating(kind) -> None:
    signal = _signal(kind, x=(1, 2, 3), y=(1, 2))
    with pytest.raises(ValueError, match="lengths differ"):
        list(signal)
    with pytest.raises(ValueError, match="lengths differ"):
        signal.to_numpy()
    with pytest.raises(ValueError, match="nonfinite"):
        _signal(kind, y=(1, float("nan"), 3)).to_numpy()


def test_direct_chromatogram_queries_do_not_require_compounds() -> None:
    traces = [_signal().section for _ in range(4)]
    dataset = Dataset(
        QLDFile(
            path="test",
            tables={
                "QD1": DBFTable(2, [], [{"ID": "first"}, {"ID": "second"}]),
                "QD5": traces,
            },
        )
    )
    assert not dataset.compounds
    assert dataset.chromatograms_for(0, function=3, transition="1e2>50") == list(
        dataset.chromatograms[:2]
    )
    assert dataset.chromatograms_for(1, transition=MRMTransition.parse("100>50")) == list(
        dataset.chromatograms[2:]
    )
    with pytest.raises(ValueError, match="ambiguous chromatogram"):
        dataset.chromatogram(0, function=3, transition="100>50")
    assert dataset.chromatogram(0, function=4, transition="100>50") is None
    assert dataset.chromatograms_for(0, transition="TIC") == []


def test_unresolved_media_is_an_error_in_queries() -> None:
    dataset = Dataset(
        QLDFile(
            path="test",
            tables={
                "QD1": DBFTable(2, [], [{"ID": "first"}, {"ID": "second"}]),
                "QD5": [_signal().section],
                "QD7": [_signal("Spectrum").section],
            },
        )
    )
    with pytest.raises(ValueError, match="ownership"):
        dataset.chromatograms_for(0)
    with pytest.raises(ValueError, match="ownership"):
        dataset.chromatogram(0, function=3, transition="100>50")
    with pytest.raises(ValueError, match="ownership"):
        dataset.spectra_for(0)
