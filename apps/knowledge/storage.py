"""Secure upload handling.

* Filenames from clients are never used as paths: storage keys are generated.
* Extension and magic-byte checks must both pass.
* Size limits are enforced before the file is read into memory.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from apps.common.exceptions import APIError

EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


@dataclass(frozen=True)
class ValidatedUpload:
    extension: str
    content_type: str
    size_bytes: int


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"  # docx is a zip container
    lowered = head[:512].lower()
    if b"<!doctype html" in lowered or b"<html" in lowered:
        return "html"
    return None


def validate_upload(upload: UploadedFile) -> ValidatedUpload:
    if upload is None or not upload.name:
        raise APIError("A file is required.", code="file_required")

    suffix = Path(upload.name).suffix.lower()
    if suffix not in settings.KNOWLEDGE_ALLOWED_EXTENSIONS:
        raise APIError(
            "This file type is not allowed.",
            code="unsupported_file_type",
            extra={"allowed": settings.KNOWLEDGE_ALLOWED_EXTENSIONS},
        )

    size = int(getattr(upload, "size", 0) or 0)
    if size <= 0:
        raise APIError("The uploaded file is empty.", code="empty_file")
    if size > settings.KNOWLEDGE_MAX_FILE_BYTES:
        raise APIError(
            "The uploaded file exceeds the size limit.",
            code="file_too_large",
            extra={"max_bytes": settings.KNOWLEDGE_MAX_FILE_BYTES},
        )

    upload.seek(0)
    head = upload.read(1024)
    upload.seek(0)
    signature = _sniff(head)

    expected = {".pdf": "pdf", ".docx": "zip", ".html": "html", ".htm": "html"}.get(suffix)
    if expected and signature != expected:
        raise APIError(
            "The file content does not match its extension.",
            code="content_type_mismatch",
        )
    if suffix in {".txt", ".md", ".markdown"}:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            # Allow BOM-prefixed or partially non-UTF8 text but reject binaries.
            if b"\x00" in head:
                raise APIError("The file does not appear to be text.", code="content_type_mismatch")

    return ValidatedUpload(
        extension=suffix,
        content_type=EXTENSION_CONTENT_TYPES.get(suffix, "application/octet-stream"),
        size_bytes=size,
    )


def build_storage_key(*, organization_id, extension: str) -> str:
    return f"knowledge/{organization_id}/{uuid.uuid4().hex}{extension}"
