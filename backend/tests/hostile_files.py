"""Deterministic hostile upload fixtures (U3), shared with the benchmark as ground truth (KTD17).

Every builder returns the same bytes on every call: zip members carry a fixed timestamp and the
PDFs are written by hand with computed cross-reference offsets.
"""

import io
import zipfile

# Text: zero-width space, a right-to-left override pair and tag-block characters (6 in total).
INVISIBLE_TEXT = "Rotate\u200b keys.\u202eLIAME\u202c Tag\U000e0041\U000e0042\U000e007f done.\n"
BOM_SOFT_HYPHEN_TEXT = b"\xef\xbb\xbf" + "Co\u00adoperate with ops.\n".encode()
INVALID_UTF8 = b"caf\xe9 au lait\n"

MARKDOWN_WITH_COMMENT = b"# Runbook\n\nVisible.\n<!-- note: COMMENT-SECRET -->\nMore.\n"

HIDDEN_HTML = b"""<html><head><title>Ops</title></head><body>
<p>Visible one.</p><div hidden>HIDDEN-DIV</div><script>SCRIPT_SECRET()</script>
<!-- COMMENT-SECRET --><span style="display: none">STYLE-HIDDEN</span>
<p class="lead">Visible two.</p></body></html>"""

LINKED_STYLESHEET_HTML = (
    b'<html><head><link rel="stylesheet" href="site.css"></head>'
    b'<body><p class="lead">Visible.</p></body></html>'
)


def wiki_html(css: str = ".toc { margin: 0 1em } h1 { font-weight: bold }") -> bytes:
    """A routine wiki export: class attributes and a stylesheet block."""
    return (
        f'<html><head><style>{css}</style></head><body><div class="mw-content">'
        f'<h1 class="title">Failover</h1><p class="lead">Switch regions.</p></div></body></html>'
    ).encode()


# PDF ----------------------------------------------------------------------------------------


def _pdf(objects: list[bytes], trailer: bytes = b"") -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R %s>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        trailer,
        xref,
    )
    return bytes(out)


def _stream(content: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)


def pdf(*pages: str | None, trailer: bytes = b"", extra: list[bytes] | None = None) -> bytes:
    """One page per argument; None is a page with no text layer (an image-only scan)."""
    first_page = 4  # 1 catalog, 2 page tree, 3 font
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages)),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content = b"" if text is None else b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text.encode()
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R >> >> >>" % (first_page + 2 * i + 1)
        )
        objects.append(_stream(content))
    return _pdf(objects + (extra or []), trailer)


def encrypted_pdf() -> bytes:
    encrypt = b"<< /Filter /Standard /V 1 /R 2 /O <%s> /U <%s> /P -4 >>" % (b"ab" * 32, b"cd" * 32)
    file_id = b"01" * 16
    return pdf(
        "Locked.", extra=[encrypt], trailer=b"/Encrypt 6 0 R /ID [<%s> <%s>] " % (file_id, file_id)
    )


# Zip and DOCX -------------------------------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def zip_of(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            member = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, data)
    return buffer.getvalue()


def docx(body: str, comments: str | None = None) -> bytes:
    """A minimal Word package around raw `w:body` XML, with an optional comments part."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/comments.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
        "</Relationships>"
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}" '
        'xmlns:v="urn:schemas-microsoft-com:vml">'
        f"<w:body>{body}</w:body></w:document>"
    )
    members = {
        "[Content_Types].xml": content_types.encode(),
        "_rels/.rels": rels.encode(),
        "word/document.xml": document.encode(),
    }
    if comments is not None:
        members["word/comments.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><w:comments xmlns:w="{_W}">'
            f"{comments}</w:comments>"
        ).encode()
        members["word/_rels/document.xml.rels"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Target="comments.xml" Type="http://schemas.'
            b'openxmlformats.org/officeDocument/2006/relationships/comments"/>'
            b"</Relationships>"
        )
    return zip_of(members)


def _p(*runs: str) -> str:
    return f"<w:p>{''.join(runs)}</w:p>"


def _r(text: str, props: str = "") -> str:
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


CLEAN_DOCX_BODY = _p(_r("Plain paragraph.")) + _p(_r("Second "), _r("paragraph."))

HIDDEN_DOCX_BODY = (
    _p(
        _r("Visible intro."),
        '<w:r><w:commentReference w:id="0"/></w:r>',
        f'<w:ins w:id="1" w:author="a">{_r("INSERTED-SECRET")}</w:ins>',
        '<w:del w:id="2" w:author="a"><w:r><w:delText>DELETED-SECRET</w:delText></w:r></w:del>',
    )
    + _p(
        _r("Visible "),
        _r("VANISH-SECRET", "<w:vanish/>"),
        _r("WHITE-SECRET", '<w:color w:val="FFFFFF"/>'),
        _r("end."),
    )
    + f"<w:tbl><w:tr><w:tc>{_p(_r('TABLE-CELL'))}</w:tc></w:tr></w:tbl>"
    + _p(
        "<w:r><w:pict><v:shape><v:textbox><w:txbxContent>"
        f"{_p(_r('TEXTBOX-SECRET'))}"
        "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
    )
)
HIDDEN_DOCX_COMMENTS = f'<w:comment w:id="0" w:author="a">{_p(_r("COMMENT-SECRET"))}</w:comment>'


def clean_docx() -> bytes:
    return docx(CLEAN_DOCX_BODY)


def hidden_docx() -> bytes:
    return docx(HIDDEN_DOCX_BODY, HIDDEN_DOCX_COMMENTS)


def many_member_zip(count: int = 2000) -> bytes:
    return zip_of({f"word/m{i}.xml": b"" for i in range(count)})


def ratio_bomb_zip() -> bytes:
    # About 1000:1: a megabyte of zeros deflates to roughly a kilobyte.
    return zip_of({"word/document.xml": b"\0" * 1_000_000})


def traversal_zip() -> bytes:
    return zip_of({"word/document.xml": b"<x/>", "../evil": b"x"})
