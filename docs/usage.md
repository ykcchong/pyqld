# User Guide

## Requirements

`pyqld` supports Python 3.10 and newer. Runtime dependencies are `olefile`
for the OLE2 container and NumPy for signal arrays and numerical calculations.

Install from a checkout:

```bash
python -m pip install ./pyqld
```

For development:

```bash
cd pyqld
python -m pip install -e ".[dev]"
```

## Read a file

`read()` is the normal entry point. It decodes the complete file eagerly and
returns a `Dataset`.

```python
import pyqld

dataset = pyqld.read("batch.qld")

print(dataset.samples)
print(dataset.compounds)
print(dataset.results)
print(dataset.chromatograms)
print(dataset.spectra)
```

Input may be a string or `pathlib.Path`. The file remains closed after reading;
no context manager is required.

## Look up results

Samples may be selected by zero-based index, sample ID, file name, or an
existing `Sample`. Compounds may be selected by index, exact name, or an
existing `Compound`. Existing objects must belong to the same dataset; use the
sample ID, file name, or compound name when looking up an equivalent record in
another file.

Sample IDs are labels and may repeat across injections. A string matching
multiple samples raises `ValueError`; use the zero-based sample index or a
unique raw file name instead. Results are associated with injections using
audited row boundaries and raw file names, with unique sample IDs as a fallback.
Conflicting or ambiguous ownership is never resolved by splitting result rows
equally. `dataset.result_sample(result)` returns its owning `Sample`, or `None`
when unresolved. `dataset.unresolved_results` retains those rows for inspection;
sample-scoped queries affected by them raise `ValueError`.

```python
sample = dataset.sample(0)
compound = dataset.compound("5-HIAA")
result = dataset[sample, compound]

print(result.area)
print(result.height)
print(result.retention_time)
print(result.result)
```

A sample/compound pair can have more than one quantitation result. `result`,
`primary_result` and the area/height/retention-time shortcuts require a unique
active primary (`METH_PEAK == 0`). Deleted rows are excluded. No eligible rows
returns `None`; multiple primaries or active rows without a primary raise
`ValueError`. An absent/invalid primary marker is not treated as zero.

```python
print(result.selection_status)  # primary, missing, deleted, no_primary, ambiguous
if result.selection_status == "primary":
    print(result.result.area)

# Explicit choices for inspecting legacy or unusual result layouts:
row = result.select_result(policy="primary_or_first")  # fallback if no primary
first = result.select_result(policy="first", include_deleted=True)
```

`primary_or_first` still raises on multiple primary rows. `first` intentionally
selects file order. `result.results` retains every row, including deleted rows,
and each row exposes `is_primary` and `is_deleted`.

```python
for quantitation in result.results:
    print(quantitation.integration)
    print(quantitation.quality)
    print(quantitation.concentration, quantitation.response)
```

All table-backed records are read-only mappings with snake_case names:

```python
print(sample.injection_volume)
print(sample["stock_dil"])
print(compound.quantifier_transition)
print(dict(compound))
```

Use `raw_fields` only for format research that requires original vendor names.
Top-level loaded collections such as `samples`, `compounds`, `results`,
`chromatograms`, and `spectra` are immutable tuples. Filtering and pair-level
accessors return new lists that callers may modify.

## Filter results

```python
results = dataset.results_for(
    sample,
    function=compound.function,
    transition=compound.quantifier_transition,
)

for result in dataset.unassigned_results:
    print(result.sample_id, result.function, result.transition)
```

`unassigned_results` retains QD2 rows with no compound name. They can still be
selected by sample, function, and transition.

## Acquisition metadata

An acquisition describes how one sample was measured. Navigate from the sample
to its acquisition, then to a numbered instrument function and its MRM channels:

```python
acquisition = dataset.acquisition(sample)
if acquisition is not None:
    print(acquisition)  # compact summary; binary header bytes are omitted
    print(acquisition.instrument)
    print(acquisition.acquired_at)
    for function in acquisition.functions:
        print(function.number, function.function_type, function.ionization_mode)
        print(function.start_time, function.end_time)  # Decimal minutes, or None
        for channel in function.mrm_channels:
            print(channel.number, channel.transition, channel.compound_name)
            print(channel.dwell_time, channel.cone_voltage, channel.collision_energy)
```

`functions` and `mrm_channels` are immutable tuples in report order. Function
and channel **numbers are vendor numbers**, usually starting at 1; tuple indexes
start at 0. Use `function(number)` and `channel(number)` to avoid confusing them.
Missing numbers raise `KeyError`. An absent QD6 sample record returns `None`
from `dataset.acquisition(sample)`.

```python
acquisition = dataset.acquisition(sample)
if acquisition is not None:
    # Use the compound's function and transition, without parsing report strings.
    channels = acquisition.channels_for(
        function=compound.function,
        transition=compound.quantifier_transition,
    )
    for channel in channels:
        function = acquisition.function(channel.function)
        assert function.channel(channel.number) is channel
        print(channel.delay)  # report text, e.g. "Auto" or "0.005" seconds
```

`channels_for()` accepts either a numeric MRM label or `MRMTransition`, uses
exact numeric identity, and retains all matches. No matches returns `()`;
non-MRM labels such as `TIC` raise `ValueError`. Channel numbers and compound
labels do not assign quantifier/qualifier roles; those come from the
quantitation method. Multiple matching channels need explicit disambiguation.
Use the pair's chromatogram properties to retrieve the corresponding signal.

### Coverage and raw reports

The acquisition API is a partial interpretation of the embedded report, not a
complete instrument-method API:

| Data | Recommended access | Coverage |
| --- | --- | --- |
| Sample identity, instrument, acquisition time | `acquisition.sample_id`, `.instrument`, `.acquired_at` | Typed convenience properties; time has no inferred timezone |
| Function type, ionization mode, time window | `acquisition.functions` or `.function(number)` | Recognized `Function N` blocks; missing/unrecognized time windows give `None` |
| MRM masses, dwell, cone voltage, collision energy, delay, compound label | `function.mrm_channels` or `acquisition.channels_for(...)` | Recognized MRM tables; numeric values are `Decimal`, delay remains text |
| Non-MRM function settings | `function.text` | Type/mode/time available; no typed scan or SIR channel model |
| Calibration, tune, LC/pump/autosampler settings | `acquisition.report.get("calibration", {})`, etc. | Generic section mappings; repeated keys become tuples and tabular settings are not fully structured |
| Exact report and binary header | `acquisition.report.text`, `acquisition.header_bytes` | Preserved for inspection; binary header has no typed interpretation |

`acquisition.number_of_functions` is the count declared in QD6;
`len(acquisition.functions)` counts recognized report blocks. They need not
agree in incomplete or unfamiliar reports. Empty channels mean no recognized
MRM evidence, not proof that no acquisition happened. Malformed or unsupported
MRM tables raise `ValueError` when channels are requested. Functions can still
be inspected without parsing their channel tables; duplicate function numbers
raise `ValueError` when functions are requested.

Prefer structured function objects over `report["function"]`: the legacy
mapping merges repeated field names across functions and cannot reliably
associate those values with their function numbers. The mapping remains
available for compatibility. `report.text` preserves the original report,
and `function.text` preserves the individual function block.

## Calibration and audit metadata

```python
curve = dataset.method[compound.name].calibration
for point in curve.points:
    print(point.concentration, point.response)

for event in dataset.method.audit_events:
    print(event.timestamp, event.event, event.details)
```

### Fit and apply an explicit calibration

`CalibrationCurve` describes stored vendor metadata. `LinearCalibration` is an
executable external-standard model: `response = slope * concentration + intercept`.
It supports linear least squares with `none`, `1/x`, or `1/x^2` weighting. It
does not automatically reproduce vendor models, origin rules, internal-standard
responses or QC flags.

```python
from pyqld import LinearCalibration

calibration = LinearCalibration.fit(
    concentrations=[1, 2, 3], responses=[12, 22, 32], weighting="1/x",
)
print(calibration.slope, calibration.intercept)
print(calibration.diagnostics.r_squared, calibration.diagnostics.rmse)
print(calibration.diagnostics.residuals)
print(calibration.concentration(42))  # approximately 4
```

Diagnostics include response residuals, predicted responses, back-calculated
standard concentrations, relative errors in percent (`None` at zero level),
the concentration range and selected point indexes when fitting stored points.
R² and RMSE are unweighted summaries even when the fit is weighted. They do not
decide whether a calibration is acceptable. Directly constructed models such
as `LinearCalibration(slope=10, intercept=2)` have `diagnostics=None`.

Stored points can be refitted only by explicitly selecting their zero-based
indexes. Review `curve.points` and their status codes first:

```python
curve = dataset.method[compound.name].calibration
for index, point in enumerate(curve.points):
    print(index, point.concentration, point.response, point.status)

# Example: after reviewing a curve with at least three suitable points.
calibration = LinearCalibration.from_curve(
    curve, point_indexes=[0, 1, 2], weighting="1/x", response="area",
)
```

This creates a new linear fit; vendor coefficients, model/origin codes, validity
and point-status codes are not interpreted. There is no automatic conversion
of arbitrary vendor curves. `concentration_factor` multiplies the final
calculated concentration; diagnostics and `predict_response()` use unscaled
standard concentrations.

### Requantitation versus reintegration

```python
# Recalculate responses/concentrations from existing areas or heights.
updated = dataset.requantitate(calibration, compound=compound)

# Or reintegrate one owned row from its chromatogram, then quantify it.
pair = dataset[sample, compound]
row = pair.result  # requires a unique active primary
if row is not None:
    updated = dataset.edit_peak(
        row, start=1.0, end=1.5, calibration=calibration,
        baseline="linear", area_scale=60,
    )

updated.export_results("results.csv", compound=compound)
updated.save("reprocessed.qld")
```

Choose boundaries within the trace. Integration uses minutes; `area_scale=60`
converts intensity-minutes to intensity-seconds, while the default `1` retains
intensity-minutes. `baseline` is `linear` or `zero`. Neither operation modifies
the original dataset. Requantitation defaults to named, active primary rows;
`result_indexes=[...]` selects explicit rows. Use a compound filter when
calibrations differ between compounds. Peak editing uses the resolved injection
and requires exactly one matching chromatogram. Both operations support
external-standard (`ES`) rows only and reject deleted rows.

These operations update stored results, mark a processing note and do not
recompute vendor QC, qualifier ratios or audit signatures. `with_changes()`
alone edits stored fields without performing either numerical operation.

## Chromatograms and spectra

```python
chromatogram = result.quantifier_chromatogram
if chromatogram is not None:
    for time, intensity in chromatogram:
        print(time, intensity)

for spectrum in result.spectra(rt_window=0.2):
    for mass, intensity in spectrum:
        print(spectrum.time, mass, intensity)
```

Retrieve traces without a compound using the sample, function and transition:

```python
traces = dataset.chromatograms_for(sample, function=3, transition="100>50")
trace = dataset.chromatogram(sample, function=3, transition="100>50")
if trace is not None:
    times, intensities = trace.to_numpy()
    window_times, window_values = trace.time_window(1.0, 1.5)
```

`chromatograms_for()` retains all matches in file order, including duplicates,
for inspection. `chromatogram()` requires a unique match and applies the same
acquisition checks as pair-level access. Both accept `MRMTransition` or a
string, including non-MRM strings such as `TIC`. Unresolved media ownership
raises `ValueError`, distinct from a missing trace (`None` or an empty list).

`to_numpy()` on chromatograms and spectra returns cached, read-only float64
arrays (time/intensity or mass/intensity). The first call builds them; later
calls reuse them. Existing list properties still return independent copies.
`time_window(start, end)` returns read-only views of stored points within the
inclusive interval in minutes, without interpolation. Out-of-range windows
are empty. Nonfinite signals, unequal array lengths, and invalid window bounds
raise `ValueError`; signal iteration no longer silently truncates arrays.

Chromatograms are associated using sample-major stream order plus the method's
function and trace fields. Spectra are associated by function; `rt_window`
optionally filters around the pair's primary retention time.

### Numeric MRM matching and acquisition evidence

MRM transitions are matched as exact `Decimal` precursor/product m/z pairs,
within the owning sample and acquisition function. For example, `568.20 > 347.20`,
`568.2>347.2`, and `5.682e2 > 3.472e2` identify the same transition. This applies
to chromatogram lookup, result filtering, and peak editing. No mass tolerance
or rounding is applied: genuinely different masses remain distinct.
Other traces, such as `TIC`, retain case-sensitive text matching. Original labels,
decoded signal arrays, and raw streams remain unchanged.

```python
from pyqld import MRMTransition

transition = MRMTransition.parse(compound.quantifier_transition)
if transition is not None:
    print(transition.precursor_mz, transition.product_mz)  # Decimal values

acquisition = dataset.acquisition(sample)
if acquisition is not None:
    for channel in acquisition.channels_for(function=compound.function):
        print(channel.function, channel.number, channel.transition)
        print(channel.ionization_mode, channel.collision_energy)
```

For recognized `Function N` / `MRM of N channels` tables in the embedded
acquisition report, chromatogram lookup additionally checks that the numeric
transition is declared for that sample's function. Channel numbers are not
quantifier/qualifier roles: those roles come from the quantitation method.
A missing quantifier is returned as `None`, never replaced with the qualifier.

Multiple stored chromatograms for the same sample/function/transition, or
multiple acquisition channels with that same function/transition, raise
`ValueError` rather than choosing the first. A mismatch with a recognized
acquisition table also raises `ValueError`. A recognized but malformed or
unsupported MRM table raises `ValueError` when channels or an MRM chromatogram
are requested. All stored traces remain available through
`dataset.chromatograms` for inspection.

If acquisition metadata is absent, its format is unrecognized, or the requested
function has no recognized MRM table, numeric matching still works but is
**not acquisition-validated**. `acquisition.mrm_channels` exposes only recognized
channels (an empty tuple when none are recognized). Parsing a transition label
alone is not independent acquisition evidence; even the embedded report describes
configured channels, not a verification against the original raw instrument file.

## Iterate a batch

```python
for result in dataset.pairs():
    if result.area is not None:
        print(result.sample.id, result.compound.name, result.area)
```

`pairs()` visits every sample/compound combination, including combinations with
no detected peak.

## Validate a dataset

```python
report = dataset.validate()
for issue in report.issues:
    print(issue.severity, issue.code, issue.stream, issue.index, issue.message)
report.raise_for_errors()  # optional; warnings alone do not raise
```

Validation is read-only and returns an immutable `ValidationReport` with
`errors`, `warnings`, `is_valid`, and JSON-ready `to_dict()`. Checks cover retained
compressed source structure, decoded INI structure and declared table row
counts, result ownership and numeric fields, primary selection, media layouts,
signal lengths/counts/time order/finite values, and recognized acquisition
tables and conflicts. Error indexes are zero-based positions in the indicated
stream collection; absent indexes refer to a stream or dataset issue.

Repeated sample labels and explicitly inferred equal-sized media groups are
warnings when safe indexed access remains possible. Unresolved ownership,
ambiguous primaries, malformed recognized MRM tables and corrupt arrays are
errors. Missing signals and active rows without a primary are warnings that
require an explicit choice before those values can be used. Validation does
not certify vendor compatibility or independently verify original raw files.

Low-level `parse_ini(data, strict=True)` also raises on malformed sections,
truncated blobs and missing braces. Its default remains tolerant.

## Raw tables and sections

Use `open_qld()` when the high-level relationships are not needed:

```python
from pyqld import open_qld

qld = open_qld("batch.qld")
print(qld.stream_names())
print(qld.tables["QD1"].records)
print(qld.tables["QD5"][0].to_dict())
```

`qld.streams` contains decoded plaintext bytes. `qld.tables` contains
`DBFTable` objects for QD1-QD4 and lists of `IniSection` for QD5-QD8 and `qld`.

## Edit And Save QLD

Edits return a new dataset, leaving the original unchanged:

```python
dataset = pyqld.read("batch.qld")
edited = dataset.with_changes(dataset.samples[0], file_text="Reviewed sample")
edited = edited.with_changes(edited.results[0], area=125.5)
edited.save("batch-edited.qld")
```

Pass a record belonging to the current dataset and snake_case field names.
Sample, compound, method-compound, and quantitation-result records are supported.
Values must fit their existing DBF field widths: text is printable ASCII,
numbers must be finite, and logical fields require booleans. Invalid edits fail
without changing the original. Relationship keys (including IDs, names, file
names, functions, transitions, and primary-peak markers) are protected.

At the lower level, use `qld.with_changes("QD1", 0, file_text="Reviewed")`,
`qld.to_bytes()`, or `qld.save(path)`. Saving refuses existing destinations
unless `overwrite=True` is supplied, and commits the output atomically.
An unedited file is saved byte-for-byte; edited files preserve untouched OLE
streams and directory metadata. The writer supports version-3 OLE containers.

This is stored-value editing, not quantitation recalculation. Changing area,
integration boundaries, calibration, or concentration does not update dependent
values or create vendor audit events. Rows and raw trace arrays cannot yet be
added, removed, or edited. Modified streams may grow because they use literal
LZSS blocks. Native TargetLynx acceptance has not been verified; retain originals
and validate edited copies in the vendor software before operational use.

## Export JSON

```python
from pyqld import export_dir, export_json

export_json("batch.qld", "batch.json", indent=2)
export_dir("batch.qld", "batch", indent=2, split_sections=True)
```

Set `include_raw=True` only when decoded stream bytes are required. In a single
JSON document they are base64 encoded; directory export writes binary files.
Large chromatogram and spectrum streams can be split into one JSON file per
section with `split_sections=True`.

## Pickle and Spyder

Datasets, record objects, compound-result views, chromatograms, and acquisition
reports support Python `pickle` and `cloudpickle` (used by Spyder's Variable
Explorer). Record aliases and acquisition-report mappings remain read-only
after restoration, and references within a serialized dataset remain linked.

```python
import pickle

restored = pickle.loads(pickle.dumps(dataset))
print(restored.samples[0].id)
```

Only unpickle trusted data: loading a pickle can execute arbitrary code.
Pickles are version-sensitive Python object snapshots, not a stable archival
format. Use QLD for preserving source data or JSON for interoperable exports.
`cloudpickle` is optional at runtime and is included in the development
dependencies for compatibility tests.

After updating pyqld for Spyder, restart Spyder and its kernel so both processes
use the updated package. If they use different Python environments, update
pyqld in both.

## Command line

```text
pyqld INPUT [OUTPUT]
pyqld INPUT --list
pyqld INPUT --out-dir DIR [--split-sections]
```

Useful options are `--indent N`, `--include-raw`, `--split-threshold N`, and
`--out-dir DIR`. Use `OUTPUT` equal to `-` to write JSON to standard output.

## Errors and limits

* A missing path raises `FileNotFoundError` through `olefile`.
* A non-OLE or damaged container raises an `olefile` parsing exception.
* Unknown decoded blob types are preserved as base64 rather than discarded.
* Loading is eager. Large batches require memory for decoded streams and object
  wrappers at the same time.
* Names are exact and case-sensitive.
* QD8 is normally empty but is retained when present.
