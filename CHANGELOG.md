# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

- End-to-end opt-in strict QLD reading with contextual compression/DBF/INI errors
- DBF recovery diagnostics and conservative preservation of unsupported fields
- Native helper type stubs and reader coverage/remaining-gap documentation

- Optional pyOpenMS/mzML and qualified matchms adapters, with per-injection
  identity, explicit unit conversion, strict ownership checks and provenance
- Explicit small-molecule assay metadata and TraML/Skyline transition exports
- Strict TraML semantic validation with an explicit, warned/provenance-recorded
  override for nonconformant native OpenMS writer output
- Linked quantitation reports preserving all result rows, QC, method/calibration
  records and batch audit data, without rescaling stored results
- Public media ownership evidence and strict optional spectrum retention time
- MS/quantitation CLI formats with explicit sample and metadata selection

- Read-only dataset validation with structured errors/warnings for source
  integrity, ownership, result selection, signals and acquisition metadata
- Sample/function/transition chromatogram queries, cached read-only NumPy
  signal arrays and inclusive chromatogram time-window views
- Explicit result-selection policies and selection status
- Calibration fit diagnostics and explicit refitting of selected stored standards
- Documentation for calibration, reintegration, requantitation and result export

- Structured acquisition functions, vendor-number channel lookup, and exact
  transition filtering, with dwell time, cone voltage, delay and compound labels
- Acquisition coverage guide and compact acquisition/report summaries

- Copy-on-write `with_changes()`, QLD serialization, and atomic `save()` APIs
- Fixed-width DBF validation and preservation of unedited OLE streams
- GUI field editing, revision checks, bounded undo/redo, and QLD downloads

- `pyqld.read(path)` as the canonical direct-reading API
- Real-file public API integration coverage
- User, API, architecture, release, and repository documentation
- Pythonic domain interfaces for acquisition metadata, calibration curves,
  audit events, integrations, quality flags, and unnamed results
- Snake-case mapping behavior for all DBF-backed records
- Result filtering, tuple lookup, and iterable chromatogram/spectrum points
- PEP 561 marker for downstream type checking

### Fixed

- INI no-progress on bare CR and section headers spanning later lines
- Guessed float interpretation of unknown binary blobs and undetected array misalignment
- DBF duplicate/suffixed-field collisions and hidden snake-case alias values
- Unsupported DBF columns hiding subsequent fields and short-header unpacking failures
- Case-sensitive QLD stream lookup, silent duplicate-stream replacement, and
  unrelated OLE containers appearing to be empty datasets
- Opaque future-stream payloads omitted from default JSON/directory exports
- Incorrect fixture paths that skipped existing real-file regression tests

### Changed

- Resolve results to injection indexes using audited row boundaries and raw-file
  identity; repeated sample labels no longer merge results or silently select
  the last sample
- Require a unique active primary for result convenience properties; implicit
  first-row fallback now requires an explicit selection policy
- Reject unresolved media ownership and mismatched/nonfinite signal iteration
- Add strict INI structural checks and ensure malformed child headers make progress

- Corrected the documented dBase header layout
- Marked earlier parser packages as legacy research code
- Indexed peak lookup by sample and compound instead of rescanning every peak
- Preferred `QuantitationResult` and `CompoundResult` terminology; `Peak` and
  `SampleCompound` remain compatibility aliases
- Preserved duplicate vendor DBF columns with occurrence-aware field keys
- Scoped chromatograms and spectra to samples using audited table sizes
- Rejected domain objects passed to a different dataset's lookup methods
- Exposed loaded model collections as immutable tuples
- Serialized empty INI streams correctly and assigned QD8 the stable `qd8` label
- Made directory export byte counts, indentation, and split thresholds exact
- Cached immutable integration, quality, calibration, and audit values

## [0.1.0] — 2026-06-01

### Added

- Initial release
- Full LZSS + XOR codec for `.qld` substreams
- dBase-III variant parser (QD1–QD4)
- INI-style section parser (QD5–QD7, qld) with blob support
- High-level object model (`Dataset`, `Sample`, `Compound`, `Peak`, `Chromatogram`, `Spectrum`, `SampleCompound`, `Method`)
- JSON export (single file and directory tree)
- CLI (`pyqld` command)
- Format specification documentation
