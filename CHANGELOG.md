# Changelog

All notable changes to this project will be documented in this file.

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
