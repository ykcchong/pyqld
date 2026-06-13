"""Tests for the LZSS + XOR codec."""

from __future__ import annotations

from pyqld.codec import (
    XOR_KEY,
    decode_quan_stream,
    decrypt,
    iter_blocks,
    lzss_decode,
    lzss_decode_block,
)


class TestXorKey:
    def test_xor_key_value(self) -> None:
        assert XOR_KEY == 0x65

    def test_xor_key_is_ascii_e(self) -> None:
        assert chr(XOR_KEY) == "e"


class TestDecrypt:
    def test_decrypt_roundtrip(self) -> None:
        original = b"Hello, World!"
        encrypted = bytes(b ^ XOR_KEY for b in original)
        assert decrypt(encrypted) == original

    def test_decrypt_empty(self) -> None:
        assert decrypt(b"") == b""

    def test_decrypt_custom_key(self) -> None:
        original = b"test"
        encrypted = bytes(b ^ 0xAA for b in original)
        assert decrypt(encrypted, key=0xAA) == original


class TestLzssDecodeBlock:
    def test_literal_only(self) -> None:
        # 4 tokens, all literals (code 0)
        flag_buf = b"\x00"  # 00 00 00 00 → 4 literals
        token_buf = b"ABCD"
        out = bytearray()
        consumed = lzss_decode_block(flag_buf, token_buf, out)
        assert out == b"ABCD"
        assert consumed == 4

    def test_short_back_ref(self) -> None:
        # Two tokens: code 0 (literal), then code 1 (short back-ref)
        # LSB first: bits 0-1 = code 0, bits 2-3 = code 1
        # byte = 0 | (1 << 2) = 0x04
        flag_buf = bytes([0x04])
        token_buf = b"A" + bytes([1, 3])  # literal 'A', then dist=1 len=3
        out = bytearray()
        consumed = lzss_decode_block(flag_buf, token_buf, out)
        # 'A' then copy 3 bytes from output[-1] → "AAAA"
        assert out == b"AAAA"
        assert consumed == 3  # 1 (literal) + 2 (dist+len)

    def test_long_back_ref(self) -> None:
        # 5 literals "ABCDE", then code 2 (long back-ref)
        # byte 0: codes 0,0,0,0 = 0x00 (4 literals)
        # byte 1: codes 0,2,0,0 = 0x08 (1 literal, then long back-ref)
        flag_buf = bytes([0x00, 0x08])
        token_buf = b"ABCDE" + bytes([5, 0, 3])  # dist=5 (u16 LE), len=3
        out = bytearray()
        consumed = lzss_decode_block(flag_buf, token_buf, out)
        assert out == b"ABCDEABC"
        assert consumed == 8  # 5 (literals) + 3 (dist16+len)


class TestIterBlocks:
    def test_no_blocks_on_empty(self) -> None:
        blocks = list(iter_blocks(b""))
        assert blocks == []

    def test_single_block(self) -> None:
        # Build a minimal block: sz1=1, flag_buf=0x00, sz2=2, token_buf=AB
        data = (
            b"\x01\x00\x00\x00"  # sz1 = 1
            b"\x00"               # flag_buf (1 byte)
            b"\x02\x00\x00\x00"  # sz2 = 2
            b"AB"                 # token_buf (2 bytes)
        )
        blocks = list(iter_blocks(data))
        assert len(blocks) == 1
        off, flag_buf, token_buf = blocks[0]
        assert off == 0
        assert flag_buf == b"\x00"
        assert token_buf == b"AB"


class TestLzssDecode:
    def test_empty_input(self) -> None:
        assert lzss_decode(b"") == b""

    def test_roundtrip_literal_block(self) -> None:
        # Build a block with 2 literal tokens
        data = (
            b"\x01\x00\x00\x00"  # sz1 = 1
            b"\x00"               # flag_buf: 4 literal tokens
            b"\x02\x00\x00\x00"  # sz2 = 2
            b"Hi"                 # token_buf
        )
        result = lzss_decode(data)
        assert result == b"Hi"


class TestDecodeQuanStream:
    def test_empty(self) -> None:
        assert decode_quan_stream(b"") == b""

    def test_known_roundtrip(self) -> None:
        # Encode "Hello" through the pipeline and verify decode
        plaintext = b"Hello"
        encrypted = bytes(b ^ XOR_KEY for b in plaintext)
        # Build a simple LZSS block with literals
        # Need ceil(5/4) = 2 flag bytes for 5 tokens
        flag_buf = bytes([0x00, 0x00])  # 8 literal tokens
        data = (
            bytes([len(flag_buf), 0, 0, 0])  # sz1
            + flag_buf
            + bytes([len(encrypted), 0, 0, 0])  # sz2
            + encrypted
        )
        assert decode_quan_stream(data) == plaintext
