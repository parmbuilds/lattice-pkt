"""HTML extractor (KTD12): keep visible text, drop and flag hidden subtrees."""

import re
from html.parser import HTMLParser

from app.ingest.receipt import Receipt

_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track",
    "wbr",
}  # fmt: skip
_BLOCK = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
    "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav",
    "ol", "p", "pre", "section", "table", "td", "th", "title", "tr", "ul",
}  # fmt: skip
_DROPPED = {"script": "script", "template": "hidden_element", "noscript": "hidden_element"}
# Declarations that can hide text; matched on lowercased CSS with whitespace removed.
_HIDING = re.compile(
    r"display:none|visibility:hidden|opacity:0(?:\.0*)?(?![.\d])|font-size:0(?:\.0*)?(?![.\d])"
    r"|(?:left|right|top|bottom|text-indent|margin-left|margin-top):-\d{3,}"
)
_INLINE_HIDDEN = re.compile(r"display:none|visibility:hidden")


def _css(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


class _Visible(HTMLParser):
    def __init__(self, receipt: Receipt):
        super().__init__(convert_charrefs=True)
        self.receipt = receipt
        self.out: list[str] = []
        self.stack: list[str] = []
        self.hidden_at: int | None = None  # stack depth of the outermost hiding element
        self.hidden_kind = ""
        self.hidden_text: list[str] = []
        self.ambiguous = 0

    def _hiding_kind(self, tag: str, attrs: dict[str, str | None]) -> str | None:
        if tag in _DROPPED:
            return _DROPPED[tag]
        if tag == "style":
            return "style"
        style = _css(attrs.get("style") or "")
        if (
            "hidden" in attrs
            or (attrs.get("aria-hidden") or "").strip().lower() == "true"
            or _INLINE_HIDDEN.search(style)
        ):
            return "hidden_element"
        if _HIDING.search(style):
            self.ambiguous += 1
        return None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.hidden_at is None:
            if tag == "link" and "stylesheet" in (attributes.get("rel") or "").lower().split():
                self.ambiguous += 1
            kind = self._hiding_kind(tag, attributes)
            if kind is not None and tag not in _VOID:
                self.hidden_at, self.hidden_kind, self.hidden_text = len(self.stack), kind, []
            elif tag in _BLOCK:
                self.out.append("\n")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - 1 - self.stack[::-1].index(tag)
        del self.stack[depth:]
        if self.hidden_at is not None and depth <= self.hidden_at:
            self._close_hidden()
        elif self.hidden_at is None and tag in _BLOCK:
            self.out.append("\n")

    def _close_hidden(self):
        text = "".join(self.hidden_text)
        if self.hidden_kind == "style":
            # A stylesheet is not content; it only matters if it can hide something.
            if _HIDING.search(_css(text)):
                self.ambiguous += 1
        else:
            self.receipt.flag(self.hidden_kind, "body", excerpt=text)
        self.hidden_at = None

    def handle_data(self, data):
        if self.hidden_at is not None:
            self.hidden_text.append(data)
        elif "pre" in self.stack:
            self.out.append(data)
        else:
            self.out.append(re.sub(r"\s+", " ", data))

    def handle_comment(self, data):
        self.receipt.flag("comment", "body", excerpt=data)

    def close(self):
        super().close()
        if self.hidden_at is not None:
            self._close_hidden()


def extract_html(text: str) -> tuple[str, Receipt]:
    receipt = Receipt()
    parser = _Visible(receipt)
    parser.feed(text)
    parser.close()
    if parser.ambiguous:
        receipt.flag("visibility_ambiguous", "body", parser.ambiguous)
    body = re.sub(r"[ \t]*\n[ \t]*", "\n", "".join(parser.out))
    body = re.sub(r"\n{2,}", "\n\n", body).strip()
    body = receipt.clean(body, "body")
    receipt.section("body", len(body))
    return body, receipt
