"""Tests for the INI parser."""

from __future__ import annotations

import struct

from pyqld.ini import IniBlob, IniSection, parse_ini


def _build_ini(sections: list[tuple[str, dict[str, str]]], eol: str = "\n") -> bytes:
    """Build a minimal INI document for testing."""
    lines: list[str] = []
    for name, kvs in sections:
        lines.append(f"[{name}]{eol}")
        lines.append(f"{{{eol}")
        for k, v in kvs.items():
            lines.append(f"{k}\t{v}{eol}")
        lines.append(f"}}{eol}")
    return "".join(lines).encode("ascii")


def _build_ini_with_blob(
    section_name: str,
    floats: list[float],
    blob_key: str = "Times",
    eol: str = "\n",
) -> bytes:
    """Build an INI section with a float32 blob."""
    payload = struct.pack(f"<{len(floats)}f", *floats)
    lines: list[str] = []
    lines.append(f"[{section_name}]{eol}")
    lines.append(f"{{{eol}")
    # Blob: marker=1, size, payload
    blob_header = struct.pack("<II", 1, len(payload))
    lines_raw = blob_header + payload + eol.encode("ascii")
    lines.append(f"}}{eol}")
    return lines_raw + f"}}{eol}".encode("ascii")


class TestParseIni:
    def test_empty(self) -> None:
        sections = parse_ini(b"")
        assert sections == []

    def test_single_section(self) -> None:
        data = _build_ini([("Header", {"Key1": "Value1", "Key2": "Value2"})])
        sections = parse_ini(data)
        assert len(sections) == 1
        assert sections[0].name == "Header"
        d = sections[0].to_dict()
        assert d["Key1"] == "Value1"
        assert d["Key2"] == "Value2"

    def test_multiple_sections(self) -> None:
        data = _build_ini(
            [
                ("SectionA", {"X": "1"}),
                ("SectionB", {"Y": "2"}),
            ]
        )
        sections = parse_ini(data)
        assert len(sections) == 2
        assert sections[0].name == "SectionA"
        assert sections[1].name == "SectionB"

    def test_nested_section(self) -> None:
        # Build manually: outer section containing an inner section
        text = (
            "[Outer]\n"
            "{\n"
            "[Inner]\n"
            "{\n"
            "Key\tValue\n"
            "}\n"
            "}\n"
        )
        sections = parse_ini(text.encode("ascii"))
        assert len(sections) == 1
        assert sections[0].name == "Outer"
        d = sections[0].to_dict()
        assert "Inner" in d
        assert d["Inner"]["Key"] == "Value"

    def test_crlf_eol(self) -> None:
        data = _build_ini([("S", {"K": "V"})], eol="\r\n")
        sections = parse_ini(data)
        assert len(sections) == 1
        assert sections[0].to_dict()["K"] == "V"

    def test_duplicate_keys(self) -> None:
        text = "[S]\n{\nK\tV1\nK\tV2\n}\n"
        sections = parse_ini(text.encode("ascii"))
        d = sections[0].to_dict()
        assert d["K"] == ["V1", "V2"]


class TestIniBlob:
    def test_blob_to_dict_bytes(self) -> None:
        blob = IniBlob(size=4, payload=b"\x00\x01\x02\x03", payload_kind="bytes")
        d = blob.to_dict()
        assert d["kind"] == "blob"
        assert d["encoding"] == "base64"

    def test_blob_to_dict_float32(self) -> None:
        blob = IniBlob(size=8, payload=[1.0, 2.0], payload_kind="float32")
        d = blob.to_dict()
        assert d["encoding"] == "float32"
        assert d["data"] == [1.0, 2.0]

    def test_blob_to_dict_text(self) -> None:
        blob = IniBlob(size=5, payload="hello", payload_kind="text")
        d = blob.to_dict()
        assert d["data"] == "hello"


class TestIniSection:
    def test_add_and_to_dict(self) -> None:
        sec = IniSection(name="Test")
        sec.add("A", "1")
        sec.add("B", "2")
        assert sec.to_dict() == {"A": "1", "B": "2"}

    def test_add_nested_section(self) -> None:
        sec = IniSection(name="Parent")
        child = IniSection(name="Child")
        child.add("X", "y")
        sec.add("Child", child)
        d = sec.to_dict()
        assert d["Child"]["X"] == "y"
