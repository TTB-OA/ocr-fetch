"""MIME type to file extension mapping and content-type normalization."""

MIME_TO_EXTENSION: dict[str, str] = {
    'application/pdf': '.pdf',
    'text/html': '.html',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'text/plain': '.txt',
    'application/xml': '.xml',
    'text/xml': '.xml',
    'text/csv': '.csv',
    'application/vnd.ms-excel': '.xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/bmp': '.bmp',
}


def normalize_content_type(content_type: str | None) -> str | None:
    """Strip charset/parameters and lowercase a Content-Type header value."""
    if not content_type:
        return None
    return content_type.split(';')[0].strip().lower() or None
