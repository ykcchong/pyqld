# API Reference

All names below are importable from `pyqld` unless a module is shown.

This page describes the in-process Python library, not Flask endpoints.
For browser use, see the [web interface guide](../../webapp/README.md).
For HTTP clients, see the [HTTP API reference](../../webapp/HTTP_API.md),
including JSON schemas, revision handling, and curl examples.

## Primary API

### `read(path, *, strict=False) -> Dataset`

Read, decode, parse, and index one TargetLynx `.qld` file. This is the preferred
entry point for applications.

`strict=True` rejects malformed compression, DBF layouts, INI structure and
wrong payload types in known streams. It does not certify metadata semantics
or eliminate inferred ownership; call `validate()` separately. Containers
without recognized QLD streams and duplicate canonical stream names always
raise `ValueError`. Stream keys normalize to `QD1`, `QD2`, ..., and `qld`.

### `Dataset`

| Attribute or method | Result |
| --- | --- |
| `Dataset.from_path(path, *, strict=False)` | Equivalent constructor to `read(path, strict=strict)` |
| `samples` | Ordered `tuple[Sample, ...]` from QD1 |
| `results` | Ordered `tuple[QuantitationResult, ...]` from QD2 |
| `peaks` | Compatibility alias for `results` |
| `compounds` | Ordered `tuple[Compound, ...]` from QD3 |
| `chromatograms` | Ordered `tuple[Chromatogram, ...]` from QD5 |
| `acquisitions` | Parsed `tuple[AcquisitionInfo, ...]` from QD6 |
| `spectra` | Ordered `tuple[Spectrum, ...]` from QD7 |
| `method` | `Method` view over QD4 and the audit stream |
| `sample(key)` | Find a sample by index, ID, or file name |
| `get_sample(key)` | Find a sample or return `None` if missing/foreign; ambiguous strings raise `ValueError` |
| `compound(key)` | Find a compound by index or exact name |
| `get_compound(key)` | Find a compound or return `None` |
| `dataset[sample, compound]` | Build a `CompoundResult` view |
| `pairs()` | Iterate every sample/compound combination |
| `results_for(...)` | Filter by sample, compound, function, transition, or name presence |
| `unassigned_results` | QD2 rows without a compound name |
| `unresolved_results` | Rows whose owning injection cannot be established safely |
| `result_sample(result)` | Owning `Sample`, or `None` if unresolved; rejects foreign rows |
| `acquisition(sample)` | Acquisition metadata corresponding to a sample |
| `chromatograms_for(sample, *, function=None, transition=None)` | All matching stored traces in file order, including duplicates |
| `chromatogram(sample, *, function, transition)` | Unique acquisition-checked trace, `None` if missing, `ValueError` if ambiguous |
| `spectra_for(sample, *, function=None)` | Stored spectra for an injection |
| `media_ownership("QD5" \| "QD7")` | `audited`, `single_sample`, `inferred`, `unresolved`, or `empty` evidence for signal ownership |
| `validate()` | Immutable `ValidationReport`; does not modify data or raise on reported issues |
| `compound_results(*, sample=None, compound=None)` | List of named result dictionaries, including injection index, file name and result index |
| `export_results(path, *, format="csv", sample=None, compound=None)` | Export named results as CSV/JSON; existing destination raises `FileExistsError` |

Loaded collections are immutable so lookup indexes remain consistent.
`Sample` and `Compound` objects passed to lookup methods must belong to that
dataset; use IDs or names to resolve equivalent objects in another dataset.

Sample ID labels can repeat. `sample(key)` raises `ValueError` when a string
matches multiple sample IDs/file names. Use an index, an owned `Sample`, or a
unique file name. Result grouping uses resolved sample indexes, audited
`Peaks/PeakList` row counts and `RAW_NAME`, with unique IDs as a fallback.
Result rows are never split into equal groups to infer injection ownership.
Queries for a sample affected by unresolved results raise `ValueError`; raw
rows remain available in `results` and `unresolved_results`.

### Result selection

`CompoundResult.result`, `primary_result` and `primary_peak` require a unique
non-deleted primary row. The area/height/retention-time shortcuts use that same
rule. Empty/all-deleted selections return `None`; no primary among active rows
or multiple primary rows raise `ValueError`.

`pair.select_result(*, policy="primary", include_deleted=False)` accepts:

| Policy | Behavior |
| --- | --- |
| `primary` | Require exactly one primary among eligible rows |
| `primary_or_first` | Use the unique primary, or the first eligible row if no primary exists; multiple primaries still raise |
| `first` | Explicitly select file order, regardless of primary markers |

`pair.selection_status` is `primary`, `missing`, `deleted`, `no_primary` or
`ambiguous`. `pair.results` retains all rows. `QuantitationResult.is_primary`
requires a present, valid zero `METH_PEAK`; `is_deleted` checks vendor deletion
and DBF-row deletion flags. This replaces the previous implicit first-row
fallback. Use an explicit policy when migrating code that needs it.

### Signal arrays and direct queries

`Chromatogram.to_numpy()` and `Spectrum.to_numpy()` return cached, read-only
`tuple[NDArray[float64], NDArray[float64]]` containing coordinates/intensities.
Array storage cannot be made writeable. Existing list properties continue to
return copies. Pickling rebuilds caches on demand. Unequal/nonfinite arrays
raise `ValueError`, including during iteration or `len()`.

`Chromatogram.time_window(start, end)` returns read-only views of stored points
inside the inclusive interval in minutes. No interpolation occurs. Bounds must
be finite and ordered; times must be strictly increasing. Out-of-range windows
return empty arrays.

Dataset trace queries accept a `MRMTransition` or string, and apply exact
numeric identity for numeric labels. The plural query returns all matches for
inspection; the singular query applies acquisition validation and rejects
duplicates. Unresolved media ownership raises `ValueError` in chromatogram
and spectrum queries. Raw top-level collections remain available.

### Validation

`ValidationIssue` is immutable with `code`, `severity` (`error`/`warning`),
`message`, optional `stream`, and optional zero-based `index`.
`ValidationReport` provides immutable `issues`, `errors` and `warnings` tuples,
`is_valid` (no errors), `raise_for_errors()`, and JSON-ready `to_dict()`.
Warnings do not make the report invalid. Validation covers structural integrity,
ownership, result selection, signal arrays and recognized acquisition metadata.
It preserves tolerant loading and does not certify vendor compatibility.
See [validation behavior](usage.md#validate-a-dataset) for coverage and limits.

### Numerical processing

* `LinearCalibration(slope, intercept=0, concentration_factor=1, response="area")`
  represents a positive-slope linear external-standard model. Response is
  `area` or `height`. `concentration(response)` inverts the model and applies
  the factor; `predict_response(concentration)` uses unscaled standards.
* `LinearCalibration.fit(concentrations, responses, *, weighting="none",
  concentration_factor=1, response="area")` fits explicit finite, nonnegative
  standards at two or more distinct levels. Weighting is `none`, `1/x`, or
  `1/x^2`; inverse weighting requires positive concentrations.
* `LinearCalibration.from_curve(curve, *, point_indexes, weighting="none",
  concentration_factor=1, response="area")` refits explicitly selected stored
  points. Indexes must be distinct in-range Python integers. Vendor coefficients,
  model/origin/validity and point-status codes are not interpreted.
* Fitted models expose `diagnostics: CalibrationDiagnostics`; manually
  constructed models default to `None`. Diagnostics contain `number_of_points`,
  `weighting`, `r_squared` (`None` when response variance is zero), `rmse`,
  `residuals`, `predicted_responses`, `back_calculated_concentrations`,
  `relative_errors` in percent (`None` at zero level), `concentration_range`,
  and source `point_indexes` when refitting. R²/RMSE are unweighted, and standard
  concentrations in diagnostics are before the final concentration factor.
* `integrate_peak(times, intensities, start, end, *, baseline="linear",
  area_scale=1)` returns immutable `PeakIntegration`. It integrates positive
  baseline-corrected signal with interpolated boundaries and zero crossings.
  Times are minutes; `area_scale=60` converts area to intensity-seconds.
* `dataset.edit_peak(result, start, end, calibration, *, baseline="linear",
  area_scale=1)` reintegrates and quantifies an owned result using its resolved
  injection and unique matching trace. Baseline is `linear` or `zero`.
* `dataset.requantitate(calibration, *, sample=None, compound=None,
  result_indexes=None)` recalculates concentrations from existing areas/heights.
  Defaults select named, non-deleted primaries and reject ambiguous ownership
  or multiple primaries per injection/compound. Explicit indexes deliberately
  select rows. One calibration applies to the selection; filter by compound
  when compounds require different models.

Numerical dataset operations return a new dataset atomically. They support
external-standard (`ES`) rows only, reject deleted rows, and update a processing
note. They do not recompute vendor QC, ion ratios or audit signatures. Raw
`with_changes()` does not perform numerical processing. See the
[calibration workflows](usage.md#fit-and-apply-an-explicit-calibration).

### Editing

#### `Dataset.with_changes(record, **values) -> Dataset`

Return a new dataset with replacements in one existing record. `record` must
be a `Sample`, `QuantitationResult`, `Compound`, or `MethodCompound` owned by
that exact dataset instance. The original dataset is unchanged, and indexes
and domain projections are rebuilt in the returned dataset.

Use actual table field keys, not convenience-property names. For example,
change a sample description with `file_text=...`, not `description=...`.
For a result, use `found_rt` rather than `retention_time`, and `anal_conc`
rather than `concentration`. `dict(record)` exposes available snake_case keys;
the table's `fields` metadata provides types and fixed byte widths.

```python
import pyqld

original = pyqld.read("batch.qld")
edited = original.with_changes(original.samples[0], file_text="Reviewed sample")
edited = edited.with_changes(edited.samples[0], bottle="2")
output = edited.save("batch-edited.qld")
```

These fields must exist in the input file. When chaining edits, select the
record from the latest dataset. Passing `original.samples[0]` to
`edited.with_changes(...)` raises `ValueError`, even when the identifiers are
the same. Keeping earlier dataset references gives an application its own
copy-on-write history; the Python API itself has no revision counter or
undo/redo manager.

Several fields in one record can be updated atomically:

```python
pair = edited[0, 0]
record = pair.result
if record is not None:
    edited = edited.with_changes(record, start_rt="1.10", end_rt="1.40")
```

This changes stored boundaries only. It does not reintegrate the chromatogram
or calculate a new area. One invalid replacement rejects the entire call.
The public editor does not support multi-record transactions.

#### `QLDFile.with_changes(table: str, index: int, **values) -> QLDFile`

Lower-level equivalent that returns an edited file object without constructing
dataset indexes. `table` must be exactly `QD1`, `QD2`, `QD3`, or `QD4`, and
`index` is an absolute zero-based record index in that table.

```python
qld = pyqld.open_qld("batch.qld")
updated = qld.with_changes("QD1", 0, file_text="Reviewed sample")
payload = updated.to_bytes()
updated.save("batch-low-level-edit.qld")
```

Field keys are matched case-insensitively. Duplicate vendor names are
distinguished by occurrence suffixes such as `_2`; use `DBFField.key` or the
record mapping key. Direct mutations of `qld.tables`, raw records, or sections
are not a supported save mechanism: the writer tracks changes made through
`with_changes()`. Treat parsed structures as read-only when using the writer.

#### `Dataset.save(path, *, overwrite: bool = False) -> Path`
#### `QLDFile.save(path, *, overwrite: bool = False) -> Path`
#### `QLDFile.to_bytes() -> bytes`

`path` accepts `str` or `pathlib.Path`. The parent directory must already exist.
Save serializes the file, writes a temporary sibling, flushes it, and commits
the destination atomically. By default an existing target raises
`FileExistsError`; `overwrite=True` explicitly allows replacement. This does
not create a backup or change the in-memory object's original source path.

`to_bytes()` performs the same container serialization without writing to
disk, suitable for attachments and other transports. A dataset exposes it as
`dataset.qld.to_bytes()`, not `dataset.to_bytes()`.

An untouched loaded file serializes to its exact original bytes. Edited files
patch only selected DBF field bytes and preserve unmodified OLE stream payloads
and directory metadata while rebuilding the container. Modified streams use
literal LZSS blocks and may be larger. Saving edited containers currently
supports version-3 OLE with 512-byte sectors and 64-byte mini-sectors.

#### Validation and exceptions

| Condition | Result |
| --- | --- |
| Foreign record passed to `Dataset.with_changes()` | `ValueError` |
| Unsupported/missing table or missing original container | `ValueError` |
| Row index outside the table | `IndexError` |
| Unknown field key | `KeyError` |
| Protected relationship field | `ValueError` |
| Invalid type, non-finite number, non-ASCII text, or field-width overflow | `ValueError` |
| Incomplete table or invalid size headers | `ValueError` |
| Edited container has unsupported sector format | `ValueError` during serialization |
| Destination exists and overwrite is false | `FileExistsError` |
| Missing parent, filesystem permissions, or other I/O failure | Corresponding `OSError` subclass |

`C` fields accept printable ASCII strings, including an empty string. `N`
fields accept finite numeric values or numeric strings; `None` and `""` encode
a missing value. `L` fields require Python `bool`. Numeric text is encoded
without rounding to the declared decimal count and must fit the field width
after expansion to fixed decimal notation. Reading numeric values still uses
integers/floats and therefore does not promise arbitrary decimal precision.

Protected names are `ID`, `SAMPLE_ID`, `NAME`, `FILE_NAME`, `RAW_NAME`,
`RAW_FUNC`, `QUAN_TRACE`, `SEC_TRACE`, `SEC_EXIST`, and `METH_PEAK`, including
repeated occurrences. There is no supported override for this protection.

Editing requires `read()` or `open_qld()` to retain the original container;
constructing a `QLDFile` from parsed tables alone is not sufficient. No row
creation/deletion, raw trace editing, recalculation, or vendor audit/signature
generation is provided. Native TargetLynx acceptance is unverified. See
[editing guidance](usage.md#edit-and-save-qld) for preservation limits and
scientific cautions.

### Record Fields

`Sample`, `Compound`, `MethodCompound`, and `QuantitationResult` are read-only
`Mapping` objects. Iteration, indexing, attribute fallback, `get()`, and
`to_dict()` use snake_case names. `raw_fields` is a read-only mapping of the
original vendor names for diagnostics.

Common convenience properties:

DBF duplicate keys avoid collisions with literal vendor names and the internal
deletion marker. `DBFField.key` gives the actual unique record key. Snake-case
alias collisions retain the legacy last-column base alias while earlier
columns receive unique suffixed aliases, so iteration/`to_dict()` no longer
lose those values. `raw_fields` retains the DBF keys.

| Class | Properties |
| --- | --- |
| `Sample` | `id`, `file_name`, `description`, `sample_type`, `vial`, `injection_volume` |
| `Compound` | `name`, `cas_number`, `units`, `function`, `quantifier_transition`, `qualifier_transition`, `expected_retention_time` |
| `QuantitationResult` | `sample_id`, `analyte_name`, `function`, `transition`, `area`, `height`, `retention_time`, `concentration`, `response`, `integration`, `quality` |
| `Chromatogram` | `function`, `trace`, `times`, `intensities`, metadata properties, iteration over `(time, intensity)` |
| `Spectrum` | `function`, `time`, `retention_time`, `points`, `masses`, `intensities`, iteration over `(mass, intensity)` |

`Spectrum.retention_time` is the strict RT accessor in minutes: missing/blank
values return `None`; malformed, negative, or nonfinite values raise `ValueError`.
Zero is valid. The legacy `time` accessor is unchanged.

`media_ownership()` describes the existing layout without changing lookup
behavior. Valid audited counts take precedence; a nonempty unaudited single
injection is `single_sample`; equal-count multi-injection grouping is `inferred`.
Malformed audited layouts are `unresolved`, even for one injection. A valid
layout with no signals is `empty`.

### Domain values

* `Integration`: peak boundaries, scans, area, and height.
* `Quality`: quality score, noise, ion ratio, limits, and named failure flags.
* `AcquisitionInfo`: sample ID, instrument, acquisition time, parsed report,
    declared function count, preserved binary header, `functions`, and `mrm_channels`.
* `AcquisitionFunction`: immutable numbered function with `function_type`,
    `ionization_mode`, `start_time` and `end_time` (`Decimal` minutes or `None`),
    `mrm_channels`, and original block `text`. Non-MRM functions expose no MRM
    channels; their settings remain available in `text`.
* `MRMTransition`: immutable `precursor_mz` and `product_mz` as positive, finite
    `Decimal` values. `MRMTransition.parse(label)` returns a transition for a
    numeric `precursor > product` label, or `None` for other syntax. Numeric
    nonpositive masses raise `ValueError`; construction requires `Decimal`
    values, not binary floats. Equality and hashing ignore decimal formatting
    without applying a mass tolerance.
* `AcquisitionChannel`: immutable `function`, `number`, `transition`,
    `ionization_mode`, `collision_energy`, `dwell_time` (seconds), `cone_voltage`
    (volts), `delay`, and `compound_name` from a recognized embedded MRM table.
    Energy, dwell and voltage use `Decimal`; `delay` preserves report text
    (e.g. `"Auto"` or numeric seconds). `number` is the acquisition channel
    number, not a quantifier/qualifier designation. Missing strings are `""`.
    The original five-argument constructor remains valid; newly added dwell
    and voltage fields default to `None` for manually constructed channels.
* `CalibrationCurve` and `CalibrationPoint`: curve model, weighting,
    coefficients, validity, concentrations, and responses.
* `AuditEvent`: UTC timestamp, action, user text, sample/peak indexes, changed
    integration boundaries, and signature state.
* `CompoundResult`: results, primary result, chromatograms, spectra, and common
    area/height/retention-time shortcuts for one sample and compound.

`Peak` and `SampleCompound` remain compatibility aliases for
`QuantitationResult` and `CompoundResult`.

`AcquisitionInfo.mrm_channels` and `AcquisitionReport.mrm_channels` return
`tuple[AcquisitionChannel, ...]`. These lazily parse recognized `Function N` /
`MRM of N channels` acquisition tables and reject malformed, incomplete, or
duplicate tables with `ValueError`. Unrecognized reports return an empty tuple;
a missing function is not evidence that its transition was validated.

### Acquisition navigation

Both `AcquisitionInfo` and `AcquisitionReport` expose the following:

| Attribute or method | Result |
| --- | --- |
| `functions` | Cached immutable `tuple[AcquisitionFunction, ...]` in report order |
| `function(number)` | Function with that vendor number; `KeyError` if absent |
| `mrm_channels` | Flat tuple of recognized MRM channels across all functions |
| `channels_for(*, function=None, transition=None)` | All channels matching the optional filters; `()` if none |

`AcquisitionFunction.channel(number)` looks up an MRM channel by vendor number,
raising `KeyError` if absent. Function/channel numbers are not zero-based tuple
positions. Functions can be inspected before their channel tables are parsed;
duplicate function numbers raise `ValueError` on function access.

The transition filter accepts `MRMTransition` or a numeric MRM string, compares
exact numeric identities, and retains duplicate matches. Non-MRM strings raise
`ValueError`. `channels_for()` validates all reported MRM tables, just like
`mrm_channels`, even when filtering by function. To inspect only a particular
function's channels, use `function(number).mrm_channels`.

These are partial report views: `number_of_functions` is QD6's declared count,
which is independent of the number of recognized function blocks. LC settings,
instrument calibration, tune reports and non-MRM channel tables are not fully
typed. Use the generic report mappings or preserved report/block text for those
settings. The legacy `report["function"]` merges repeated keys across functions;
prefer `functions` to keep their settings associated. See the
[acquisition coverage guide](usage.md#coverage-and-raw-reports).

Pair-level chromatogram lookups use sample + function + exact numeric MRM
identity, or a text key for non-MRM traces. They validate against the embedded
acquisition table when one is recognized for that function. Ambiguous stored
traces, ambiguous acquisition channels, and acquisition conflicts raise
`ValueError` with sample/function/trace context. Missing traces return `None`
through individual accessors and are omitted from the pair's list, without
changing quantifier/qualifier roles. Raw `Dataset.chromatograms` still retains
all stored traces. See [matching details](usage.md#numeric-mrm-matching-and-acquisition-evidence).

## File and export API

### MS interoperability

These functions are available from `pyqld`. Optional engines are imported only
when used. Install `pyqld[openms]` for pyOpenMS 3.6+ (below 4), or
`pyqld[matchms]` for matchms. Each extra has its own Python/platform requirements;
the core package's Python minimum is unchanged.

#### `to_openms(dataset, sample, *, allow_inferred_ownership=False, spectrum_metadata=None)`

Returns `OpenMSConversion(experiment, report)`: an independent mutable
`pyopenms.MSExperiment` and a JSON-ready provenance dictionary.

* One injection per experiment; `sample` uses the normal dataset lookup rules.
* Converts **all retained MRM chromatograms** for that injection. Non-MRM labels,
  duplicate/ambiguous traces, conflicting acquisition channels, invalid/empty
  arrays, and non-increasing/negative chromatogram times raise `ValueError`.
* RT is converted from minutes to seconds. Intensities are not normalized;
  OpenMS stores them as float32. Coordinates use float64. Overflow is rejected.
  Configured Decimal masses are preserved in metadata; distinct transitions
  that collapse to the same float64 pair within a function are rejected.
* Native IDs are `pyqld_sample_<index>_qd5_<source_index>` (or `qd7` for spectra).
  They are unique within the source dataset, not globally across files.
* Precursor/product m/z and recognized polarity are mapped. Dwell time is in
  seconds and cone voltage in volts. Reported collision energy is preserved as
  metadata with **unverified units**, not silently asserted to be eV.
* Inferred ownership requires explicit opt-in, recorded in the report.
  Unresolved ownership cannot be overridden.
* Spectra are excluded by default and the omission is reported. To include
  them, `spectrum_metadata` must map **every QD7 index belonging to the selected
  injection**, and no others, to `SpectrumMetadata`.

The report includes the source path, a SHA-256 fingerprint of length-delimited,
name-sorted decoded streams (or `None` for structures without retained streams),
injection identity, units, ownership evidence, source indexes, processing text,
acquisition report text/instrument/local timestamp (no inferred timezone),
conversion warnings, and dataset validation diagnostics. The decoded-stream
fingerprint is not a hash of the original container file. Dataset diagnostics
may include unrelated result-selection problems; structural errors and errors
in selected signals block conversion. A successful export does not certify
complete acquisition coverage or native TargetLynx compatibility.

#### `export_mzml(dataset, sample, path, *, allow_inferred_ownership=False, spectrum_metadata=None) -> Path`

Writes mzML and a mandatory sibling `<path>.json` provenance report, including
the mzML SHA-256. Uses the same conversion rules as `to_openms`. Both paths must
be absent; existing files or symlinks raise `FileExistsError`. The parent must
exist. Files are staged before publication and files published by the current
call are removed if publication fails. Multi-file visibility is not atomic.

#### `SpectrumMetadata`

Frozen metadata supplied from independently verified acquisition information:

| Field | Requirement |
| --- | --- |
| `ms_level` | Positive integer, not boolean |
| `spectrum_type` | `centroid` or `profile` |
| `polarity` | `positive` or `negative` |
| `evidence` | Nonempty description/reference for the supplied interpretation |
| `precursor_mz` | Positive finite m/z; required for MS level > 1 |
| `precursor_charge` | Optional nonzero integer, consistent with polarity |
| `collision_energy_ev` | Optional finite nonnegative energy in eV |
| `isolation_lower_offset`, `isolation_upper_offset` | Optional finite nonnegative offsets in Th |

MS1 metadata must not contain precursor information. Missing/invalid spectrum
RT, unsorted/nonpositive masses, or conflicting known acquisition polarity
blocks conversion. The library records caller evidence but cannot independently
certify it. This API does not establish that QD7 represents raw fragmentation
spectra, nor reconstruct absent spectra.

#### `to_matchms(dataset, sample, metadata, *, allow_inferred_ownership=False)`

Returns a list of independent `matchms.Spectrum` objects. `metadata` has the
same complete QD7-index mapping as above. Requires verified **centroid MS2**
spectra and nonnegative intensities. Preserves stored peaks without sorting,
normalization, centroiding, or matchms metadata harmonization. RT metadata is
in seconds; source identity, ownership, and evidence are retained.

#### `quantitation_report(dataset, sample, *, allow_inferred_ownership=False)`
#### `export_quantitation(dataset, sample, path, *, allow_inferred_ownership=False) -> Path`

Return/write a JSON-ready report with all injection result rows, including
unnamed, deleted, and non-primary rows. Preserve raw mapped result fields, QC,
integration, concentration units, compound/method records, calibration values,
and the **whole batch's** parsed audit data. No result selection or recalculation
occurs; result RT stays in minutes and stored areas are not rescaled. Signal IDs
match mzML IDs, with explicit `unique`, `missing`, or `ambiguous` link status.
Unresolved result ownership raises. JSON writing rejects nonfinite values and
refuses existing destinations. Neither function requires an optional MS engine.

#### Targeted assays

`AssayTransition` is frozen and contains explicit caller-supplied fields:
`compound_index`, `role` (`quantifier`/`qualifier`), `precursor_charge`,
`product_charge`, `library_intensity`, `retention_time_seconds`, and `evidence`.
Charges are nonzero integers; intensity/RT must be finite and nonnegative;
evidence must be nonempty. Roles must be declared by the compound and map to a
unique stored trace. Duplicate assignments, inconsistent group metadata, and
charges conflicting with known acquisition polarity raise.

* `to_targeted_experiment(dataset, sample, assays, *, allow_inferred_ownership=False)`
  returns an OpenMS `TargetedExperiment` using **small-molecule compounds**.
* `export_traml(dataset, sample, assays, path, *, allow_inferred_ownership=False,
  allow_nonconformant=False)` validates the staged TraML with OpenMS before
  publishing that library and `<path>.json` provenance.
* `export_skyline(dataset, sample, assays, path, *, allow_inferred_ownership=False)`
  writes a Skyline small-molecule transition CSV and `<path>.json` provenance,
  without requiring pyOpenMS. Explicit RT is converted to **minutes**.

Only explicitly supplied assay transitions are exported; selections/omissions
are recorded. Native IDs match chromatogram mzML. No charges, spectral-library
intensities, or normalized/iRT values are inferred from QLD results. Retention
times are local times, not iRT. These files do not reproduce TargetLynx's
quantitation algorithm or audit/signature state.

**TraML is strict by default.** With pyOpenMS 3.6, the native writer's
small-molecule charge-state and library-intensity annotations can fail its own
semantic validator, despite successful OpenMS read-back and targeted processing.
Invalid output raises `ValueError` with the diagnostics and publishes neither
file. `allow_nonconformant=True` explicitly permits that output, emits
`UserWarning`, and records `semantic_validation` (`is_valid`, `errors`,
`warnings`) and the override in the sidecar. No charges are dropped and no
intensity units are invented to bypass the validator. This override is for
deliberate consumer-specific interchange, not standards-conformance certification.
Direct `to_targeted_experiment` and Skyline export are unaffected.

### `open_qld(path, *, strict=False) -> QLDFile`

Return the decoded streams and parsed low-level structures without building the
object-model indexes.

### `QLDFile`

* `path`: resolved source path as a string.
* `streams`: stream basename to decoded plaintext bytes.
* `tables`: stream basename to `DBFTable`, `list[IniSection]`, or `None` for an
  unrecognized nonempty payload; its bytes remain in `streams`.
* `stream_names()`: decoded names in OLE directory order.
* `label_for(stream)`: stable human-readable export label.
* `to_dict(include_raw=False)`: JSON-ready representation.

### JSON functions

* `to_json(qld, indent=None, include_raw=False) -> str`
* `export_json(qld_path, json_path, indent=None, include_raw=False, strict=False) -> Path`
* `export_dir(qld_path, out_dir, indent=None, include_raw=False,
  split_sections=False, split_threshold=50, strict=False) -> Path`

`strict` is forwarded to the reader. Uninterpreted streams always include their
raw payload: `raw_b64` in JSON or a linked binary file in directory exports,
even when `include_raw=False`. Empty INI streams remain ordinary empty sections.

## Parser API

* `decode_quan_stream(data, *, strict=False) -> bytes`: LZSS decompress, then XOR decrypt.
* `lzss_decode(data, *, strict=False) -> bytes`: decompress only.
* `decrypt(data, key=0x65) -> bytes`: XOR transform only.
* `parse_dbf(data, *, strict=False) -> DBFTable`: parse a decoded QD1-QD4 table.
* `parse_ini(data) -> list[IniSection]`: parse decoded QD5-QD8/audit data.
  Pass `strict=True` to reject truncated blobs and malformed section structure.

Strict codec checks run identically before native or Python decoding.
`DBFTable.issues` records recovered layout problems and unsupported field types;
strict parsing raises instead. Unknown DBF field types retain their fixed-width
values as text without hiding later columns; their semantics are not decoded
and editing those fields is refused. Headers shorter than 32 bytes always raise.

`DBFTable.records` is a list of dictionaries. `IniSection.items` is an ordered
list of key/value pairs so repeated keys are preserved. `IniSection.to_dict()`
promotes repeated keys to lists. `IniBlob.payload_kind` is `text`, `float32`, or
`bytes`.
Only recognized array sections are interpreted as float32. Unknown non-text
blobs remain bytes regardless of length. Misaligned known arrays remain bytes
in tolerant mode and raise in strict mode. CR, LF, CRLF and mixed line endings
are accepted; other explicit `eol` values raise.

The parser-level functions are public for research and custom extraction, but
normal applications should use `read()`.
