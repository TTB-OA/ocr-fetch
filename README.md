# ocr-fetch

Download files from a URL and convert documents to Markdown/text. Extracted from the
[ocr-pipeline](https://github.com/TTB-OA/ocr-pipeline) project so it can be reused across projects.

Supported inputs: PDF (text-based, with OCR fallback for scanned documents), DOC, DOCX, PPTX,
HTML, XML/XSD, CSV, XLS, XLSX, plain text/Markdown, images (JPG/PNG/BMP via OCR), and ZIP
archives containing any of the above.

## Install

As a git dependency with `uv`:

```powershell
uv add "ocr-fetch @ git+https://github.com/TTB-OA/ocr-fetch.git"
```

Or pin to a tag:

```toml
[project]
dependencies = ["ocr-fetch"]

[tool.uv.sources]
ocr-fetch = { git = "https://github.com/TTB-OA/ocr-fetch.git", tag = "v0.1.0" }
```

### System dependencies

Install these separately and make sure they are on `PATH`:

| Tool | Needed for | Link |
| --- | --- | --- |
| Tesseract-OCR | Image OCR (required) and scanned-PDF OCR (fallback) | https://github.com/tesseract-ocr/tesseract |
| Poppler | Rendering PDF pages for OCR | https://poppler.freedesktop.org/ |
| Pandoc | `.doc` conversion fallback | https://pandoc.org/ |

Missing tools are detected up front: required ones produce a `[Dependency Error: ...]` marker,
optional ones only log a warning and the converter falls back to another method.

## Usage

```python
from ocr_fetch import convert_file_to_markdown, download_and_convert_file, is_conversion_error

# Convert a local file
markdown, method = convert_file_to_markdown("report.pdf")

# Download a URL and convert it in one step
markdown, raw_path, method = download_and_convert_file(
    "https://example.com/ruling.pdf",
    raw_download_dir="temp/downloads",
    file_name_override="ruling.pdf",
)
if markdown is None:
    ...  # download or conversion failed; `method` says which stage
```

Converters never raise. On failure they return a marker string; test it with
`is_conversion_error(content)`.

Files with no registered converter are sniffed: text-like content is read as plain text
(`method='plaintext'`); binary content (NUL bytes / control-byte density) returns a
`[Conversion Error: Unsupported binary file type ...]` marker rather than decoded garbage.

### Public API

| Name | Purpose |
| --- | --- |
| `download_and_convert_file(url, raw_download_dir, file_name_override)` | Download, save, and convert. Returns `(markdown, raw_path, method)`. |
| `convert_file_to_markdown(file_path, content_type=None)` | Convert a local file. Returns `(markdown, method)`. |
| `is_conversion_error(content)` | True when content is a conversion/dependency error marker. |
| `can_convert(file_ext=None, content_type=None)` | True when a converter is registered for the extension or MIME type. |
| `supported_extensions()` | Sorted tuple of registered extensions (e.g. `('.bmp', '.csv', ...)`). |
| `looks_like_text(file_path)` | Binary sniff used by the unknown-type fallback. |
| `get_parse_method_name(file_ext, content_type=None)` | Expected method name for an extension/MIME type. |
| `sanitize_filename(name, ext_hint=None)` | Windows-safe filename. |
| `register_converter(extension, method_name, converter, mime_types=None)` | Add or override a format. |
| `build_download_session()` | The retrying `requests.Session` used for downloads. |

### ZIP archives

`.zip` (or `application/zip`) inputs are expanded to a temp directory, each member is run
through the registry, and the results are joined under `## <member path>` headers with a
`# <archive name>` title. Members that are skipped or fail are noted inline as `> Skipped: ...`
/ `> Conversion failed ...`; the archive as a whole is an error only if no member converts.

Guards (module constants in `ocr_fetch.archives`): `MAX_ARCHIVE_MEMBERS=200`,
`MAX_MEMBER_BYTES=50 MiB`, `MAX_TOTAL_UNCOMPRESSED_BYTES=200 MiB`, `MAX_COMPRESSION_RATIO=100`.
Encrypted members, nested archives, and entries with absolute or `..` paths are skipped.
Member names are never used to build on-disk paths.

### Adding a format

```python
from ocr_fetch import register_converter

def convert_rtf(file_path: str) -> tuple[str, str]:
    ...
    return content, "my_rtf_reader"

register_converter(".rtf", "my_rtf_reader", convert_rtf, mime_types=["application/rtf"])
```

### Parse method names

`pypdf_text`, `pypdf_text_low_quality`, `pytesseract_ocr_pdf`, `pytesseract_ocr`,
`markitdown_docx`, `markitdown_pptx`, `markitdown_doc`, `pypandoc_doc`, `docx2txt_doc`,
`binary_text_doc`, `html2text`, `pandas_markdown`, `plaintext`, `xml_formatted`, `zip_archive`.

### Logging

All modules log to the `ocr_fetch` logger. Configure it from the host application:

```python
import logging
logging.getLogger("ocr_fetch").setLevel(logging.INFO)
```

## Development

```powershell
uv sync
uv run pytest
```

Tests that need Tesseract or Poppler are skipped automatically when those tools are missing.
Set `TEST_URL` (and optionally `TEST_FILENAME`) to enable the live download test.
