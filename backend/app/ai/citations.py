"""The citation check (KTD6): one sentence per line, checked against the passages sent.

A sentence passes when it has markers, every marker names a sent passage, and for each cited
passage its content words are mostly that passage's and it carries as many negation words as
the passage line closest to it, so an inverted step fails. Word overlap cannot tell a changed
number or a changed meaning that keeps the wording; an entailment check is the next step.
"""

import html
import re
from collections.abc import Mapping

MARKER = re.compile(r"\[(P\d+)\]")
# Tuned on tests/data/citation_labels.json: no labeled unsupported sentence passes.
THRESHOLD = 0.8
_WORD = re.compile(r"[a-z0-9]+(?:['.-][a-z0-9]+)*")
_UNIT = re.compile(r"(?<=[.!?])\s+|\n")
NEGATIONS = frozenset(
    {"not", "no", "never", "without", "none", "nor", "neither", "nothing", "cannot"}
)
# Function words only: quantities and order (all, once, every, above) carry a step's meaning.
STOPWORDS = frozenset(
    """a also an and are as at be because been being but by can could did do does doing for
    from had has have having he her here hers him his how i if in into is it its itself just
    may me might must my of on or our she should so such than that the their them then there
    these they this those to too us very was we were what when where which while who whom why
    will with would you your""".split()
)


def _stem(word: str) -> str:
    # A light suffix strip so "replica"/"replicas" and "expire"/"expired" match.
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word.removesuffix("e") if len(word) > 3 else word


def _words(text: str) -> tuple[set[str], int]:
    """Content words, and the number of negation words."""
    tokens = _WORD.findall(text.lower())
    negations = sum(t in NEGATIONS or t.endswith("n't") for t in tokens)
    content = {_stem(t) for t in tokens if t not in STOPWORDS and t not in NEGATIONS}
    return content, negations


def _supports(sentence: set[str], negations: int, passage: str) -> bool:
    if not sentence or len(sentence & _words(passage)[0]) < THRESHOLD * len(sentence):
        return False
    closest = max(_UNIT.split(passage), key=lambda unit: len(sentence & _words(unit)[0]))
    return _words(closest)[1] == negations


def check(line: str, sent: Mapping[str, str]) -> dict:
    """Check one answer line against the passages sent, keyed by id ("P1": text)."""
    line = html.unescape(line)  # passages reach the model escaped (KTD8)
    markers = list(dict.fromkeys(MARKER.findall(line)))
    text = " ".join(MARKER.sub("", line).split())
    words, negations = _words(text)
    supported = bool(markers) and all(
        m in sent and _supports(words, negations, sent[m]) for m in markers
    )
    return {"text": text, "markers": markers, "supported": supported}


def complete_lines(buffer: str) -> tuple[list[str], str]:
    """Split streamed text into its completed non-blank lines and the unfinished rest."""
    *lines, rest = buffer.split("\n")
    return [line.strip() for line in lines if line.strip()], rest


def outcome(checked: list[dict]) -> str:
    if not any(s["supported"] for s in checked):
        return "not_covered"  # R10
    return "answered" if all(s["supported"] for s in checked) else "partly_supported"
