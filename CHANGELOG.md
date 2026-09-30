# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added

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

### Changed

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
