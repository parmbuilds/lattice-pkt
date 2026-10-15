"""Word extractor (KTD12): body from top-level paragraphs, hidden layers from the raw OOXML.

Character accounting compares every `w:t` in the package with what reached the body plus what
was flagged; the rest (text boxes, headers, footnotes) is flagged as unaccounted content.
"""

import io
import zipfile

import docx
from docx.oxml.parser import parse_xml  # lxml with resolve_entities=False

from app.ingest.receipt import Receipt
from app.ingest.sniff import Rejected

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAIN = "word/document.xml"
_TRACKED = {
    f"{W}ins": "tracked_insertion",
    f"{W}del": "tracked_deletion",
    f"{W}moveFrom": "tracked_move",
    f"{W}moveTo": "tracked_move",
}
_OFF = {"0", "false", "off"}


def _hidden_run_kind(run) -> str | None:
    props = run.find(f"{W}rPr")
    if props is None:
        return None
    vanish = props.find(f"{W}vanish")
    if vanish is not None and vanish.get(f"{W}val", "true").lower() not in _OFF:
        return "hidden_run"
    color = props.find(f"{W}color")
    if color is not None and (color.get(f"{W}val") or "").upper() == "FFFFFF":
        return "white_text"
    return None


def _text(element) -> str:
    return "".join(t.text or "" for t in element.iter(f"{W}t", f"{W}delText"))


def _chars(texts) -> int:
    return sum(len(t.text or "") for t in texts)


def extract_docx(archive: zipfile.ZipFile, data: bytes) -> tuple[str, Receipt]:
    receipt = Receipt()
    try:
        paragraphs, body_chars = _body(docx.Document(io.BytesIO(data)))
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                _scan(
                    name, parse_xml(archive.read(name)), body_chars if name == MAIN else 0, receipt
                )
    except (Rejected, MemoryError):
        raise
    except Exception:  # python-docx and lxml raise many types on hostile input
        raise Rejected("malformed") from None
    body = receipt.clean("\n\n".join(paragraphs), MAIN)
    receipt.section(MAIN, len(body))
    return body, receipt


def _body(document) -> tuple[list[str], int]:
    """Visible runs of top-level paragraphs, and how many `w:t` characters they hold."""
    paragraphs, chars = [], 0
    for paragraph in document.paragraphs:
        runs = [
            run
            for run in paragraph._p.xpath("./w:r | ./w:hyperlink/w:r")
            if _hidden_run_kind(run) is None
        ]
        chars += sum(_chars(run.findall(f"{W}t")) for run in runs)
        text = "".join(run.text for run in runs)
        if text.strip():
            paragraphs.append(text)
    return paragraphs, chars


def _scan(name: str, root, body_chars: int, receipt: Receipt) -> None:
    """Flag the hidden layers of one part, then anything neither in the body nor flagged."""
    if root.tag == f"{W}comments":
        for comment in root.iter(f"{W}comment"):
            receipt.flag("comment", name, excerpt=_text(comment))
        return
    flagged = set()
    for change in root.iter(*_TRACKED):
        receipt.flag(_TRACKED[change.tag], name, excerpt=_text(change))
        flagged.update(change.iter(f"{W}t"))
    for run in root.iter(f"{W}r"):
        if kind := _hidden_run_kind(run):
            receipt.flag(kind, name, excerpt=_text(run))
            flagged.update(run.iter(f"{W}t"))
    for table in root.iter(f"{W}tbl"):
        if next(table.iterancestors(f"{W}tbl"), None) is None:
            receipt.flag("dropped_table", name)
        flagged.update(table.iter(f"{W}t"))
    unaccounted = _chars(root.iter(f"{W}t")) - body_chars - _chars(flagged)
    if unaccounted > 0:
        receipt.flag("unaccounted_content", name, unaccounted)
