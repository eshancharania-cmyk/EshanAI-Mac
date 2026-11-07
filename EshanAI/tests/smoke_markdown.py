"""Unit tests for the markdown block/inline renderer (no tkinter needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ui.markdown import parse  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not cond:
        fails.append(name)


def flat(blocks):
    return [(b.style, "".join(t for t, _ in b.segments)) for b in blocks]


# 1. plain paragraph
b = parse("Hello there\nsecond line")
check("paragraph merges lines", flat(b) == [("text", "Hello there second line")], str(flat(b)))

# 2. headings
b = parse("# Title\n## Sub\n### Tiny")
styles = [x.style for x in b]
check("heading levels", styles == ["heading1", "heading2", "heading3"], str(styles))
check("heading text", "".join(t for t, _ in b[0].segments) == "Title")

# 3. fenced code (spacing between blocks is the renderer's job, not the parser's)
b = parse("before\n```python\nx = 1\nprint(x)\n```\nafter")
check("code block extracted", flat(b) == [
    ("text", "before"),
    ("code", "x = 1\nprint(x)"),
    ("text", "after")], str(flat(b)))
check("code lang kept", b[1].meta == "python", b[1].meta)

# 4. inline emphasis + code
segs = parse("mix **bold**, *ital*, ***both***, and `code` runs")[0].segments
tags = [g for _, g in segs]
check("bold present", "b" in tags, str(segs))
check("italic present", "i" in tags, str(segs))
check("both present", "bi" in tags, str(segs))
check("inline code present", "code" in tags, str(segs))
texts = "".join(t for t, _ in segs)
check("inline literal preserved", texts == "mix bold, ital, both, and code runs", texts)

# 5. emphasis not confused by code stash
segs = parse("use `**not bold**` here")[0].segments
check("code span protects stars", (segs[0][0], segs[0][1]) == ("use ", "") and '*not bold**`' not in segs[0][0], str(segs))

# 6. bullets + numbers (order preserved as written)
b = parse("- alpha\n1. first\n2. second\n- beta")
fl = flat(b)
check("bullets+numbers ordered", fl == [
    ("bullet", "•  alpha"),
    ("number", "1.  first"),
    ("number", "2.  second"),
    ("bullet", "•  beta")], str(fl))

# 7. quote + rule
b = parse("> note\n---")
check("quote", b[0].style == "quote", b[0].style)
check("rule", b[1].style == "rule", b[1].style)

# 8. links + url text
segs = parse("see [OpenAI](https://openai.com) now")[0].segments
link = [t for t, g in segs if g == "link"]
check("link label kept", link == ["OpenAI"], str(segs))

# 9. asterisks without pairs stay literal
segs = parse("2 * 3 = 6")[0].segments
check("unpaired asterisk literal", "".join(t for t, _ in segs) == "2 * 3 = 6", str(segs))

# 10. stress: garbage/binary-ish input must not crash
weird = "\\x00\x00 junk *** <<< >>> \n```\nunclosed\nmore\n```\n# heading with `code`\n- \n---"
b = parse(weird)
check("weird input renders", len(b) >= 4, f"{len(b)} blocks")

# 11. empty input
check("empty -> no blocks", parse("") == [])
check("whitespace -> no blocks", parse("   \n  ") == [])

print()
print(f"markdown: {'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
