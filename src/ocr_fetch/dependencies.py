"""Python package and external system tool availability checks.

System dependencies (install separately, and ensure they are on PATH):
- Tesseract-OCR: https://github.com/tesseract-ocr/tesseract (required by pytesseract)
- Poppler: https://poppler.freedesktop.org/ (required by pdf2image)
- Pandoc: https://pandoc.org/ (required by pypandoc)
"""

import importlib.util
import shutil

from .mime import MIME_TO_EXTENSION

DEPENDENCY_REQUIREMENTS: dict[str, list[str]] = {
    '.csv': ['tabulate'],
    '.docx': ['mammoth'],
    '.xls': ['xlrd', 'tabulate'],
    '.xlsx': ['openpyxl', 'tabulate'],
}

SYSTEM_DEPENDENCY_REQUIREMENTS: dict[str, list[str]] = {
    '.jpg': ['tesseract'],
    '.jpeg': ['tesseract'],
    '.png': ['tesseract'],
    '.bmp': ['tesseract'],
}

PDF_OCR_SYSTEM_TOOLS = ['tesseract', 'pdftoppm|pdftocairo']


def missing_dependencies_for_extension(file_ext: str) -> list[str]:
    """Return missing package names for a given file extension."""
    required = DEPENDENCY_REQUIREMENTS.get(file_ext, [])
    return [pkg for pkg in required if importlib.util.find_spec(pkg) is None]


def is_system_tool_available(tool_name: str) -> bool:
    """Support aliases in tool names (e.g., 'pdftoppm|pdftocairo')."""
    if '|' in tool_name:
        return any(shutil.which(c.strip()) for c in tool_name.split('|'))
    return shutil.which(tool_name) is not None


def resolve_effective_extension(file_ext: str, content_type: str | None) -> str:
    """Resolve effective extension when filename extension is missing/unreliable."""
    if file_ext:
        return file_ext
    return MIME_TO_EXTENSION.get(content_type or '', '')


def collect_system_dependency_status(file_ext: str, content_type: str | None) -> dict[str, list[str]]:
    """Return converter-aware required/optional missing system dependency lists."""
    effective_ext = resolve_effective_extension(file_ext, content_type)
    required_tools: list[str] = []
    optional_tools: list[str] = []

    if effective_ext in {'.jpg', '.jpeg', '.png', '.bmp'}:
        required_tools.extend(SYSTEM_DEPENDENCY_REQUIREMENTS.get(effective_ext, []))

    if effective_ext == '.pdf':
        optional_tools.extend(PDF_OCR_SYSTEM_TOOLS)
    if effective_ext == '.doc':
        optional_tools.append('pandoc')

    return {
        'required_missing': [t for t in required_tools if not is_system_tool_available(t)],
        'optional_missing': [t for t in optional_tools if not is_system_tool_available(t)],
    }
