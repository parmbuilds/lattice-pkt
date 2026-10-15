"""Text-layer PDF extractor (KTD12): no OCR, so a page without text is a flag, not silence."""

import io

from pypdf import PdfReader, apply_configuration

from app.ingest.receipt import Receipt
from app.ingest.sniff import Limits, Rejected


def extract_pdf(data: bytes, limits: Limits) -> tuple[str, Receipt]:
    receipt = Receipt(notes=["pdf_invisible_text_not_checked"])
    pages = []
    # No stream can honestly declare itself longer than the file it sits in.
    with apply_configuration(maximum_declared_stream_length=len(data)):
        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise Rejected("encrypted")
            if len(reader.pages) > limits.max_pdf_pages:
                raise Rejected("page_limit")
            texts = [page.extract_text() for page in reader.pages]
        except (Rejected, MemoryError):
            raise
        except Exception:  # pypdf raises many types on hostile input
            raise Rejected("malformed") from None
    for number, raw in enumerate(texts, 1):
        location = f"page {number}"
        text = receipt.clean(raw, location).strip()
        if not text:
            receipt.flag("empty_page", location)
        else:
            pages.append(text)
        receipt.section(location, len(text))
    return "\n\n".join(pages), receipt
