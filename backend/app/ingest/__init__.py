"""Upload ingestion (U3): bytes plus a filename become a type, a body and a receipt, or a rejection.

Everything here is pure and database-free; `runner.extract_in_child` runs `extract` under
resource limits for the upload route.
"""

import platform
from dataclasses import dataclass
from importlib.metadata import version

from app.ingest.docx import extract_docx
from app.ingest.html import extract_html
from app.ingest.pdf import extract_pdf
from app.ingest.receipt import Receipt
from app.ingest.sniff import Limits, Rejected, sniff
from app.ingest.text import extract_markdown, extract_text

__all__ = ["Extraction", "Limits", "Receipt", "Rejected", "extract"]


@dataclass(frozen=True)
class Extraction:
    detected_type: str  # pdf, docx, html, md or txt
    parser: str
    parser_version: str
    body: str
    receipt: Receipt

    def to_dict(self) -> dict:
        return {
            "detected_type": self.detected_type,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "body": self.body,
            "receipt": self.receipt.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Extraction":
        return cls(
            detected_type=str(data["detected_type"]),
            parser=str(data["parser"]),
            parser_version=str(data["parser_version"]),
            body=str(data["body"]),
            receipt=Receipt.from_dict(data["receipt"]),
        )


def extract(data: bytes, filename: str, limits: Limits) -> Extraction:
    detected_type, content = sniff(data, filename, limits)
    match detected_type:
        case "pdf":
            body, receipt = extract_pdf(content, limits)
            parser, parser_version = "pypdf", version("pypdf")
        case "docx":
            body, receipt = extract_docx(content, data)
            parser, parser_version = "python-docx", version("python-docx")
        case "html":
            body, receipt = extract_html(content)
            parser, parser_version = "html.parser", platform.python_version()
        case "md":
            body, receipt = extract_markdown(content)
            parser, parser_version = "markdown-comments", platform.python_version()
        case _:
            body, receipt = extract_text(content)
            parser, parser_version = "text", platform.python_version()
    return Extraction(detected_type, parser, parser_version, body, receipt)
