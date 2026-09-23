"""ZIP archive conversion, including zip-bomb and path-traversal guards."""

import zipfile
from pathlib import Path

import pytest

from ocr_fetch import archives, convert_file_to_markdown, is_conversion_error
from ocr_fetch.archives import convert_zip_to_markdown


def _make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_zip_converts_text_and_xml_members(tmp_path: Path):
    archive = _make_zip(tmp_path / "bundle.zip", {
        "docs/readme.txt": b"hello from txt",
        "schema/catair.xsd": b"<?xml version='1.0'?><xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'><xs:element name='a'/></xs:schema>",
        "notes.md": b"# heading\nbody",
    })
    content, method = convert_file_to_markdown(str(archive))
    assert method == "zip_archive"
    assert not is_conversion_error(content)
    assert content.startswith("# bundle.zip")
    assert "## docs/readme.txt" in content and "hello from txt" in content
    assert "## schema/catair.xsd" in content and "```xml" in content
    assert "## notes.md" in content and "# heading" in content


def test_zip_via_content_type_without_extension(tmp_path: Path):
    archive = _make_zip(tmp_path / "download", {"a.txt": b"payload"})
    content, method = convert_file_to_markdown(str(archive), content_type="application/zip")
    assert method == "zip_archive"
    assert "payload" in content


def test_zip_skips_binary_member_but_succeeds_on_others(tmp_path: Path):
    archive = _make_zip(tmp_path / "mixed.zip", {
        "good.txt": b"kept",
        "blob.bin": b"\x00\x01\x02" * 100,
    })
    content, _ = convert_zip_to_markdown(str(archive))
    assert not is_conversion_error(content)
    assert "kept" in content
    assert "## blob.bin" in content and "Conversion failed" in content


def test_zip_with_no_convertible_members_is_error(tmp_path: Path):
    archive = _make_zip(tmp_path / "junk.zip", {"blob.bin": b"\x00\xff" * 50})
    content, method = convert_zip_to_markdown(str(archive))
    assert is_conversion_error(content)
    assert method == "zip_archive"


def test_zip_rejects_path_traversal_members(tmp_path: Path):
    archive = _make_zip(tmp_path / "evil.zip", {
        "../../escape.txt": b"nope",
        "/abs/path.txt": b"nope",
        "C:\\win\\path.txt": b"nope",
        "ok.txt": b"fine",
    })
    content, _ = convert_zip_to_markdown(str(archive))
    assert content.count("> Skipped: unsafe path") == 3
    assert "fine" in content
    assert not (tmp_path.parent / "escape.txt").exists()


def test_zip_skips_nested_archives(tmp_path: Path):
    inner = _make_zip(tmp_path / "inner.zip", {"deep.txt": b"deep"})
    archive = _make_zip(tmp_path / "outer.zip", {"inner.zip": inner.read_bytes(), "top.txt": b"top"})
    content, _ = convert_zip_to_markdown(str(archive))
    assert "> Skipped: nested archive" in content
    assert "deep" not in content.replace("Skipped", "")
    assert "top" in content


def test_zip_member_count_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archives, "MAX_ARCHIVE_MEMBERS", 2)
    archive = _make_zip(tmp_path / "many.zip", {f"{i}.txt": b"x" for i in range(3)})
    content, _ = convert_zip_to_markdown(str(archive))
    assert is_conversion_error(content)
    assert "limit 2" in content


def test_zip_total_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archives, "MAX_TOTAL_UNCOMPRESSED_BYTES", 10)
    archive = _make_zip(tmp_path / "big.zip", {"a.txt": b"a" * 20})
    content, _ = convert_zip_to_markdown(str(archive))
    assert is_conversion_error(content)
    assert "uncompressed bytes" in content


def test_zip_member_size_cap_skips_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 10)
    monkeypatch.setattr(archives, "MAX_COMPRESSION_RATIO", 10_000)
    archive = _make_zip(tmp_path / "member.zip", {"large.txt": b"z" * 20, "small.txt": b"ok"})
    content, _ = convert_zip_to_markdown(str(archive))
    assert "## large.txt" in content and "Skipped: member exceeds" in content
    assert "ok" in content


def test_zip_suspicious_compression_ratio_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(archives, "MAX_COMPRESSION_RATIO", 5)
    archive = _make_zip(tmp_path / "bomb.zip", {"zeros.txt": b"a" * 100_000, "real.txt": b"real content here"})
    content, _ = convert_zip_to_markdown(str(archive))
    assert "> Skipped: suspicious compression ratio" in content
    assert "real content here" in content


def test_corrupt_zip_returns_error(tmp_path: Path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04not really a zip")
    content, method = convert_file_to_markdown(str(bad))
    assert is_conversion_error(content)
    assert method == "zip_archive"
