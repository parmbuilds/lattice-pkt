"""Extraction receipts (R7, R8, KTD12, KTD13): what a parser kept, dropped and left unaccounted.

The permanent part (sections, flags, notes and a digest of each hidden excerpt) is hashed; the
excerpt text itself is purgeable, so an acknowledgement still verifies after a purge.
"""

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass, field

SOFT_HYPHEN = "\u00ad"


@dataclass(frozen=True)
class Section:
    index: int  # 1-based page or section number
    chars: int
    flags: tuple[str, ...]


@dataclass(frozen=True)
class Flag:
    kind: str
    location: str
    count: int


@dataclass(frozen=True)
class Hidden:
    kind: str
    location: str
    text: str  # escaped, so invisible characters show as \u escapes


@dataclass
class Receipt:
    sections: list[Section] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    hidden: list[Hidden] = field(default_factory=list)

    def flag(self, kind: str, location: str, count: int = 1, excerpt: str | None = None) -> None:
        """Record `count` more of `kind` at `location`, merged with earlier ones."""
        for i, existing in enumerate(self.flags):
            if (existing.kind, existing.location) == (kind, location):
                self.flags[i] = Flag(kind, location, existing.count + count)
                break
        else:
            self.flags.append(Flag(kind, location, count))
        if excerpt is not None:
            self.hidden.append(Hidden(kind, location, escape(excerpt)))

    def section(self, location: str, chars: int) -> None:
        """Close a page or section; call after its flags are recorded."""
        kinds = tuple(sorted({flag.kind for flag in self.flags if flag.location == location}))
        self.sections.append(Section(len(self.sections) + 1, chars, kinds))

    def clean(self, text: str, location: str) -> str:
        """The one invisible-character filter every extractor passes its body through (KTD12)."""
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace(SOFT_HYPHEN, "")
        removed = [c for c in text if _invisible(c)]
        if not removed:
            return text
        self.flag("invisible_chars", location, len(removed), "".join(removed))
        return "".join(c for c in text if not _invisible(c))

    def permanent(self) -> dict:
        """The part stored on the intake row forever; excerpts appear only as digests."""
        return {
            "sections": [asdict(section) for section in self.sections],
            "flags": [asdict(flag) for flag in self.flags],
            "notes": list(self.notes),
            "hidden": [
                {
                    "kind": hidden.kind,
                    "location": hidden.location,
                    "sha256": hashlib.sha256(hidden.text.encode()).hexdigest(),
                }
                for hidden in self.hidden
            ],
        }

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self.permanent(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Receipt":
        return cls(
            sections=[Section(s["index"], s["chars"], tuple(s["flags"])) for s in data["sections"]],
            flags=[Flag(**flag) for flag in data["flags"]],
            notes=list(data["notes"]),
            hidden=[Hidden(**hidden) for hidden in data["hidden"]],
        )


def _invisible(c: str) -> bool:
    # Cf covers zero-width characters, bidirectional controls and the tag block U+E0000-E007F.
    # NUL and lone surrogates cannot be stored in a Postgres text column.
    return c == "\0" or unicodedata.category(c) in ("Cf", "Cs")


def escape(text: str) -> str:
    """Show non-printing characters as escapes so a reviewer can see them."""
    return "".join(
        c if c.isprintable() or c in "\n\t" else c.encode("unicode_escape").decode("ascii")
        for c in text
    )
