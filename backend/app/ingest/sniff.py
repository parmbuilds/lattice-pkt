"""Content sniffing (KTD11 step 3): the type comes from the bytes, never the filename alone."""

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

TEXT_EXTENSIONS = {".md": "md", ".markdown": "md", ".txt": "txt", ".html": "html", ".htm": "html"}


@dataclass(frozen=True)
class Limits:
    max_pdf_pages: int
    max_zip_members: int
    max_zip_member_bytes: int
    max_zip_total_bytes: int
    max_zip_ratio: int
    timeout_seconds: float
    memory_bytes: int
    concurrency: int

    @classmethod
    def from_settings(cls, settings) -> "Limits":
        return cls(
            max_pdf_pages=settings.extract_max_pdf_pages,
            max_zip_members=settings.extract_max_zip_members,
            max_zip_member_bytes=settings.extract_max_zip_member_bytes,
            max_zip_total_bytes=settings.extract_max_zip_total_bytes,
            max_zip_ratio=settings.extract_max_zip_ratio,
            timeout_seconds=settings.extract_timeout_seconds,
            memory_bytes=settings.extract_memory_bytes,
            concurrency=settings.extract_concurrency,
        )


class Rejected(Exception):
    """An upload refused with a reason the uploader sees: 415 for unsupported types, else 422."""

    def __init__(self, reason: str, status: int = 422):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def sniff(data: bytes, filename: str, limits: Limits) -> tuple[str, bytes | str | zipfile.ZipFile]:
    """Return the detected type and what its extractor reads: bytes, text or a bounded zip."""
    if data.startswith(b"%PDF-"):
        return "pdf", data
    if data.startswith(b"PK\x03\x04"):
        archive = open_zip(data, limits)
        if "word/document.xml" not in archive.namelist():
            raise Rejected("unsupported", 415)
        return "docx", archive
    kind = TEXT_EXTENSIONS.get(PurePosixPath(filename).suffix.lower())
    if kind is None:
        raise Rejected("unsupported", 415)
    try:
        # utf-8-sig drops a leading byte-order mark; strict UTF-8 cannot yield lone surrogates.
        return kind, data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Rejected("encoding") from None


def open_zip(data: bytes, limits: Limits) -> zipfile.ZipFile:
    """Check the central directory against the limits before any member is read (KTD12).

    Reads through zipfile stop at each member's declared size, so these checks bound them.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise Rejected("malformed") from None
    members = archive.infolist()
    if len(members) > limits.max_zip_members:
        raise Rejected("zip_limit")
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
            raise Rejected("unsafe_zip")
        if member.file_size > limits.max_zip_member_bytes:
            raise Rejected("zip_limit")
        if member.file_size > limits.max_zip_ratio * max(member.compress_size, 1):
            raise Rejected("zip_limit")
    if sum(member.file_size for member in members) > limits.max_zip_total_bytes:
        raise Rejected("zip_limit")
    return archive
