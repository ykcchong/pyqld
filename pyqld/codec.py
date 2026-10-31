"""LZSS + XOR codec for Waters TargetLynx ``.qld`` substreams.

Decode pipeline
---------------

.. code-block::text

    raw stream bytes  --(LZSS decompress)-->  encrypted  --(XOR 0x65)-->  plaintext

The LZSS decoder uses a custom block format with 2-bit token codes.
The XOR layer uses a single-byte key ``0x65`` (ASCII ``'e'``).

See :doc:`../docs/format` for the full reverse-engineering write-up.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator

_native_decode: Callable[[bytes, int], bytes] | None
try:
    from ._codec_native import decode as _native_decode
except ImportError:
    _native_decode = None

#: The single-byte XOR key used by ``CQuanFileEncrypt`` (ASCII ``'e'``).
XOR_KEY: int = 0x65


def _compression_error(data: bytes) -> str | None:
    """Validate token boundaries/history without expanding the compressed output."""
    offset = output_size = 0
    while offset < len(data):
        if len(data) - offset < 4:
            return f"truncated block header at byte {offset}"
        flags_size = struct.unpack_from("<I", data, offset)[0]
        if flags_size == 0:
            return None if not any(data[offset:]) else f"invalid block at byte {offset}"
        flags_start = offset + 4
        tokens_header = flags_start + flags_size
        if tokens_header + 4 > len(data):
            return f"truncated flag buffer at byte {offset}"
        tokens_size = struct.unpack_from("<I", data, tokens_header)[0]
        tokens_start = tokens_header + 4
        end = tokens_start + tokens_size
        if end > len(data):
            return f"truncated token buffer at byte {offset}"
        position = tokens_start
        for token in range(flags_size * 4):
            if position == end:
                break
            code = (data[flags_start + token // 4] >> ((token % 4) * 2)) & 3
            size = 1 if code == 0 else 3 if code == 2 else 2
            if position + size > end:
                return f"truncated token at byte {position}"
            if code == 0:
                output_size += 1
            else:
                distance = int.from_bytes(data[position:position + size - 1], "little")
                length = data[position + size - 1]
                if not 0 < distance <= output_size:
                    return f"invalid back-reference at byte {position}"
                output_size += length
            position += size
        if position != end:
            return f"tokens without flags at byte {position}"
        offset = end
    return None


# ---------------------------------------------------------------------------
# LZSS block decoder
# ---------------------------------------------------------------------------


def lzss_decode_block(
    flag_buf: bytes,
    token_buf: bytes,
    out: bytearray,
) -> int:
    """Decode one LZSS block, appending bytes to ``out``.

    Returns the number of bytes consumed from ``token_buf``.

    The flag buffer encodes 2 bits per token (4 tokens per byte, LSB first).
    Token codes:

    * ``0``      — literal: emit ``*token_ptr++``
    * ``1`` / ``3`` — short back-ref: ``dist (u8), len (u8)``; copy ``len``
      bytes from ``output[-dist]``
    * ``2``      — long back-ref: ``dist (u16 LE), len (u8)``; copy ``len``
      bytes from ``output[-dist]``
    """
    ntoks = len(flag_buf) * 4
    tp = 0
    tlen = len(token_buf)

    for bit_index in range(ntoks):
        byte = flag_buf[bit_index >> 2]
        shift = (bit_index & 3) * 2
        code = (byte >> shift) & 3

        if code == 0:
            if tp >= tlen:
                break
            out.append(token_buf[tp])
            tp += 1

        else:
            if code == 2:
                if tp + 2 >= tlen:
                    break
                distance = token_buf[tp] | (token_buf[tp + 1] << 8)
                tp += 2
            else:  # code 1 or 3
                if tp + 1 >= tlen:
                    break
                distance = token_buf[tp]
                tp += 1

            if tp >= tlen:
                break
            length = token_buf[tp]
            tp += 1

            src = len(out) - distance
            if src < 0:
                src = 0  # guard against corrupt distance values
            for k in range(length):
                if src + k < len(out):
                    out.append(out[src + k])

    return tp


def iter_blocks(data: bytes) -> Iterator[tuple[int, bytes, bytes]]:
    """Yield ``(block_offset, flag_buf, token_buf)`` for each LZSS block."""
    off = 0
    n = len(data)

    while off + 4 <= n:
        sz1 = struct.unpack_from("<I", data, off)[0]
        if sz1 == 0 or off + 4 + sz1 + 4 > n:
            return
        flag_off = off + 4
        flag_buf = data[flag_off : flag_off + sz1]
        sz2_off = flag_off + sz1
        sz2 = struct.unpack_from("<I", data, sz2_off)[0]
        tok_off = sz2_off + 4
        if tok_off + sz2 > n:
            return
        token_buf = data[tok_off : tok_off + sz2]
        yield off, flag_buf, token_buf
        off = tok_off + sz2


# ---------------------------------------------------------------------------
# Full stream decoders
# ---------------------------------------------------------------------------


def _lzss_decode_python(data: bytes) -> bytes:
    """Reference decoder and fallback for installations without the extension."""
    out = bytearray()
    for _off, flag_buf, token_buf in iter_blocks(data):
        lzss_decode_block(flag_buf, token_buf, out)
    return bytes(out)


def lzss_decode(data: bytes, *, strict: bool = False) -> bytes:
    """Decompress the LZSS layer of an entire stream payload.

    With ``strict=True``, reject invalid/truncated blocks and references before
    either the Python or native decoder runs. Default recovery is unchanged.

    Parameters
    ----------
    data : bytes
        The raw (compressed) stream bytes from an OLE2 substream.

    Returns
    -------
    bytes
        The LZSS-decompressed (still XOR-encrypted) data.
    """
    if strict and (error := _compression_error(data)) is not None:
        raise ValueError(error)
    if _native_decode is not None and isinstance(data, bytes):
        return _native_decode(data, 0)
    return _lzss_decode_python(data)


def decrypt(buf: bytes, key: int = XOR_KEY) -> bytes:
    """Apply the ``CQuanFileEncrypt`` XOR cipher.

    Parameters
    ----------
    buf : bytes
        The LZSS-decompressed data.
    key : int, optional
        The XOR key (default ``0x65``).

    Returns
    -------
    bytes
        The plaintext data.
    """
    return bytes(b ^ key for b in buf)


def decode_quan_stream(data: bytes, *, strict: bool = False) -> bytes:
    """Full decode of one ``QuanData/DataStream.QDx`` substream.

    Applies LZSS decompression followed by XOR decryption.
    ``strict=True`` rejects malformed compression rather than returning a prefix.

    Parameters
    ----------
    data : bytes
        The raw compressed+encrypted stream bytes.

    Returns
    -------
    bytes
        The fully decoded plaintext (dBase or INI content).
    """
    if strict and (error := _compression_error(data)) is not None:
        raise ValueError(error)
    if _native_decode is not None and isinstance(data, bytes):
        return _native_decode(data, XOR_KEY)
    return decrypt(_lzss_decode_python(data))
