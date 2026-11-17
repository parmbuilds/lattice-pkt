#!/usr/bin/env python3
"""Regenerate the plan-decisions block in docs/decisions.md.

Copies every bullet under a "Key Decisions" or "Key Technical Decisions"
heading in docs/plans/*.md verbatim. Only the text between the BEGIN and END
markers is rewritten, so the "Recorded decisions" log below it is kept.

Usage:
  python3 scripts/sync_decisions.py           # rewrite docs/decisions.md
  python3 scripts/sync_decisions.py --check   # exit 1 if it is out of date
Standard library only.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANS = ROOT / "docs" / "plans"
OUT = ROOT / "docs" / "decisions.md"
BEGIN = "<!-- BEGIN GENERATED: plan decisions -->"
END = "<!-- END GENERATED: plan decisions -->"

SECTION = re.compile(r"^(#{2,6}) (Key (?:Technical )?Decisions)\s*$")
HEADING = re.compile(r"^(#{1,6}) ")

HEADER = """# Decisions

Decisions that govern this project, with the reasons and alternatives given in the plans. Read this before designing, choosing a library or stack, changing an interface, or touching authorization, audit, or data handling.

- The block between the markers is copied from `docs/plans/*.md`. Do not edit it. Change the plan, then run `make decisions`.
- Decisions made in conversation that are not yet in a plan go in the log at the end.
"""

LOG = """## Recorded decisions

Decisions made outside a plan, newest last. Each entry gives the date, the decision, the reason, and where it was agreed.
"""


def sections(lines):
    """Yield (heading, line_no, entries) for each decision section.

    An entry is a top-level bullet plus the indented lines under it.
    """
    i = 0
    while i < len(lines):
        m = SECTION.match(lines[i])
        if not m:
            i += 1
            continue
        level = len(m.group(1))
        start = i + 1
        entries, current = [], None
        i += 1
        while i < len(lines):
            line = lines[i]
            h = HEADING.match(line)
            if h and len(h.group(1)) <= level:
                break
            if line.startswith("- "):
                if current:
                    entries.append(current)
                current = [line]
            elif line.strip() and current is not None:
                current.append(line)
            i += 1
        if current:
            entries.append(current)
        yield m.group(2), start, entries


def render():
    out = []
    for path in sorted(PLANS.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        blocks = [b for b in sections(lines) if b[2]]
        if not blocks:
            continue
        title = next((l[2:].strip() for l in lines if l.startswith("# ")), path.stem)
        title = re.sub(r"\s+-\s+Plan$", "", title)
        rel = path.relative_to(ROOT / "docs").as_posix()
        out.append(f"## {title}\n")
        out.append(f"Source: [{path.name}]({rel})\n")
        for heading, line_no, entries in blocks:
            out.append(f"### {heading} (plan line {line_no})\n")
            for entry in entries:
                out.append("\n".join(l.rstrip() for l in entry) + "\n")
    body = "\n".join(out) if out else "No decisions found in docs/plans/.\n"
    return f"{BEGIN}\n\n{body}\n{END}"


def build(existing):
    block = render()
    if existing is None:
        return f"{HEADER}\n{block}\n\n{LOG}"
    if BEGIN not in existing or END not in existing:
        sys.exit(f"{OUT.relative_to(ROOT)} has no generated markers; add them or delete the file.")
    head, rest = existing.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{block}{tail}"


def main():
    existing = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    new = build(existing)
    if "--check" in sys.argv:
        if new != existing:
            print(f"{OUT.relative_to(ROOT)} is out of date; run make decisions")
            sys.exit(1)
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return
    if new != existing:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(new, encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    else:
        print(f"{OUT.relative_to(ROOT)} already up to date")


if __name__ == "__main__":
    main()
