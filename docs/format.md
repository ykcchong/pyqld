# QLD File Format Specification

## Overview

The `.qld` file is a proprietary binary container used by Waters TargetLynx to store quantitation results from LC-MS/MS analyses.

| Property | Value |
|----------|-------|
| **Vendor** | Waters Corporation (MassLynx / TargetLynx) |
| **Container** | Microsoft OLE2 Compound Document |
| **Compression** | Custom LZSS block codec |
| **Encryption** | Single-byte XOR (`0x65`) |
| **Decoded formats** | dBase-III variant tables, INI-style sections |

## OLE2 Container

`.qld` files are standard Microsoft Compound Document Files (OLE2). The relevant substreams live under `QuanData/DataStream.`:

| Stream Path | Label | Content |
|-------------|-------|---------|
| `QuanData/DataStream.QD1` | samples | Sample list (dBase) |
| `QuanData/DataStream.QD2` | peaks | Peak results (dBase) |
| `QuanData/DataStream.QD3` | compounds | Compound definitions (dBase) |
| `QuanData/DataStream.QD4` | method_compounds | Method compound parameters (dBase) |
| `QuanData/DataStream.QD5` | chromatograms | `[Chromatogram]` sections (INI) |
| `QuanData/DataStream.QD6` | sample_info | `[SampleInfo]` sections (INI) |
| `QuanData/DataStream.QD7` | spectra | `[Spectrum]` sections (INI) |
| `QuanData/DataStream.QD8` | qd8 | Reserved/unknown sections; currently empty |
| `QuanData/DataStream.qld` | audit | `[Header]` audit trail (INI) |

## Substream Codec

Each data substream is encoded with a two-layer pipeline:

```
plaintext → XOR 0x65 → encrypted → LZSS compress → stream bytes
```

To **decode**, reverse both layers:

```
stream bytes → LZSS decompress → encrypted → XOR 0x65 → plaintext
```

### LZSS Block Format

The compressed payload is a concatenation of independent blocks:

| Field | Type | Description |
|-------|------|-------------|
| `sz1` | `u32` LE | Flag bitmap size in bytes |
| `flag_buf` | `u8[sz1]` | 2-bit flags, 4 tokens per byte, LSB first |
| `sz2` | `u32` LE | Token data size in bytes |
| `token_buf` | `u8[sz2]` | Literal bytes and back-reference arguments |

#### Token Codes (2 bits each)

| Code | Meaning | Token Data | Output |
|:----:|---------|------------|--------|
| `0` | Literal | 1 byte `b` | Emit `b` |
| `1`, `3` | Short back-ref | `dist (u8)`, `len (u8)` | Copy `len` bytes from `output[-dist]` |
| `2` | Long back-ref | `dist (u16 LE)`, `len (u8)` | Copy `len` bytes from `output[-dist]` |

### XOR Encryption

After LZSS decompression, every byte is XOR-ed with `0x65` (ASCII `'e'`).

## Decoded Formats

### dBase-III Variant (QD1–QD4)

```
file header (32 bytes):
    u8[4] magic/version (starts 03 7E)
    u32   nrecs
    u16   header length (record-data offset)
    u16   record size
    u8[20] reserved

field records (32 bytes each):
    char[11]   name (null-padded ASCII)
    char       type ('C', 'N', 'L')
    u8[4]      reserved
    u8         width
    u8         decimals
    u8[14]     padding

terminator: u8 = 0x0D

records (nrecs × record_size bytes):
    u8         deletion flag (0x00 = live, 0x2A = deleted)
    ASCII field values, padded to each field's width
```

### INI-Style Sections (QD5–QD8, qld)

```
file        := section*
section     := "[" name "]" LE "{" LE body "}" LE
body        := (kv | section | blob)*
kv          := key "\t" value LE
blob        := <u32 marker=1> <u32 size> <size bytes payload> LE
```

Blobs contain either packed `float32` arrays (for `Times`, `Masses`, `Intensities`, etc.) or ASCII text.

Unknown blob payloads are retained as bytes and represented as base64 by the
JSON serializer. QD8 is observed as empty in current fixtures, but readers must
not assume it is absent.
