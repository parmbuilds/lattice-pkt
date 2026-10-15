"""Plain text and Markdown extractors (KTD12)."""

import re

from app.ingest.receipt import Receipt

# shortcut: a comment inside a code span or fence is removed too, and an unclosed one runs to the
# end; both err towards hiding and flagging. Use a CommonMark parser if that misleads reviewers.
_COMMENT = re.compile(r"<!--(.*?)(?:-->|\Z)", re.DOTALL)


def extract_text(text: str) -> tuple[str, Receipt]:
    receipt = Receipt()
    body = receipt.clean(text, "body")
    receipt.section("body", len(body))
    return body, receipt


def extract_markdown(text: str) -> tuple[str, Receipt]:
    receipt = Receipt()

    def drop(match: re.Match) -> str:
        receipt.flag("comment", "body", excerpt=match.group(1))
        return ""

    # Clean first so an invisible character cannot hide a comment opener from the pattern.
    body = _COMMENT.sub(drop, receipt.clean(text, "body"))
    receipt.section("body", len(body))
    return body, receipt
