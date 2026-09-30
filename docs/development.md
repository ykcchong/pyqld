# Architecture and Development

## Decode path

One call to `pyqld.read(path)` follows this path:

1. `qld._iter_ole_streams` opens the OLE2 container with `olefile` and selects
   `QuanData/.../DataStream.QD*` and `DataStream.qld` entries.
2. `codec.decode_quan_stream` expands each custom LZSS block and applies XOR
   key `0x65`.
3. `qld.open_qld` identifies the decoded payload by its dBase magic.
4. `dbf.parse_dbf` parses QD1-QD4; `ini.parse_ini` parses all other streams.
5. `model.Dataset` wraps records and builds sample, compound, peak-pair, and
   chromatogram lookup indexes.

The dependency direction is deliberately one-way:

```text
cli -> qld -> codec/dbf/ini
       model -> qld + dbf/ini
       __init__ -> public re-exports
```

## Module ownership

| Module | Responsibility |
| --- | --- |
| `codec.py` | LZSS blocks and XOR transformation |
| `dbf.py` | Decoded QD1-QD4 field descriptors and records |
| `ini.py` | Nested sections, scalar values, and binary blobs |
| `qld.py` | OLE traversal, stream dispatch, JSON serialization |
| `model.py` | Domain wrappers and cross-stream relationships |
| `cli.py` | Argument parsing and terminal output |
| `__init__.py` | Stable public API only |

The old root packages are not dependencies of `pyqld`. Do not copy fixes into
them unless maintaining a historical experiment.

Peak rows are indexed by `(sample_id, compound_name)`. Chromatograms are
indexed per sample by `(function, trace)`. Keep those indexes internal so the
public record order remains identical to stream order.

## Tests

```bash
cd pyqld
python -m pip install -e ".[dev]"
python -m pytest
ruff check pyqld tests
mypy pyqld
```

Unit tests construct small codec, dBase, and INI payloads. `test_api.py` also
opens a repository fixture when both the fixture and `olefile` are available.
Keep synthetic tests for edge cases and at least one real-file check for changes
to stream traversal, parsing, or model relationships.

## Release check

```bash
python -m pytest
ruff check pyqld tests
mypy pyqld
python -m pip wheel . --no-deps --wheel-dir dist
python -m venv /tmp/pyqld-check
/tmp/pyqld-check/bin/pip install dist/pyqld-*.whl
/tmp/pyqld-check/bin/pyqld --help
```

Before changing a decoded field interpretation, compare it against more than
one real file. Vendor versions and acquisition methods can change optional
fields while preserving the stream envelope.