"""Markdown renderer for chat messages -> styled segments for CTkTextbox tags.

Pure logic (no tkinter), unit-testable. Supports the subset a chat assistant
needs: headings, bold/italic, inline code, fenced code blocks, bullet/numbered
lists, links, blockquotes, horizontal rules. Kept deliberately simple and
crash-proof: unknown/odd input renders as plain text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# rendered segment = (text, style) where style is a tag name for the textbox:
INLINE_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_INLINE = re.compile(
    r"(\*\*\*(?P<bi>.+?)\*\*\*|\*\*(?P<b>.+?)\*\*|(?<!\*)\*(?P<i>[^*\n]+?)\*(?!\*)|"
    r"`(?P<c>[^`\n]+)`)")


@dataclass
class Block:
    style: str                     # text|code|quote|bullet|number|heading1..3|rule|spacer
    segments: list                 # list[(text, tag)] for text-ish blocks
    meta: str = ""                 # number for 'number', lang for 'code'


def _inline(text: str) -> list:
    """Split inline markdown into (text, tag) segments. tags: ''|b|i|bi|code|link."""
    out: list = []
    # stash code spans and links first so emphasis can't mangle them
    codes: list[str] = []

    def stash_code(match: re.Match) -> str:
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", stash_code, text)
    links: list[tuple[str, str]] = []

    def stash_link(match: re.Match) -> str:
        links.append((match.group(1), match.group(2)))
        return f"\x01{len(links) - 1}\x01"

    text = INLINE_LINK.sub(stash_link, text)

    token = re.compile(
        r"\*\*\*(?P<bi>.+?)\*\*\*|\*\*(?P<b>.+?)\*\*|(?<!\*)\*(?P<i>[^*\n]+?)\*(?!\*)")
    pos = 0
    for m in token.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], ""))
        if m.group("bi") is not None:
            out.append((m.group("bi"), "bi"))
        elif m.group("b") is not None:
            out.append((m.group("b"), "b"))
        elif m.group("i") is not None:
            out.append((m.group("i"), "i"))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], ""))

    # unstash links and code
    final = []
    for seg, tag in out:
        parts = re.split(r"([\x00\x01]\d+[\x00\x01])", seg)
        for p in parts:
            if len(p) > 2 and p[0] in "\x00\x01" and p[-1] in "\x00\x01":
                idx = int(p[1:-1])
                if p[0] == "\x00":
                    final.append((codes[idx], "code"))
                else:
                    label, url = links[idx]
                    final.append((label, "link"))
            else:
                final.append((p, tag if p else ""))
    return [(t, g) for t, g in final if t]


_FENCE = re.compile(r"^```([\w+-]*)\s*$")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBER = re.compile(r"^\s*(\d{1,2})[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")


def parse(markdown: str) -> list[Block]:
    """Parse markdown text into renderable Blocks."""
    blocks: list[Block] = []
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        fence = _FENCE.match(stripped)
        if fence:
            lang = fence.group(1)
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i].rstrip())
                i += 1
            i += 1                       # skip closing fence (or EOF)
            blocks.append(Block("code", [("\n".join(code_lines) or " ", "code")], lang))
            continue
        if not stripped:
            i += 1
            continue
        m = _HEADING.match(stripped)
        if m:
            level = len(m.group(1))
            blocks.append(Block(f"heading{level}", _inline(m.group(2).strip())))
            i += 1
            continue
        if _RULE.match(stripped):
            blocks.append(Block("rule", []))
            i += 1
            continue
        m = _BULLET.match(line)
        if m:
            content = m.group(1)
            indent = "   " if line[:2].strip() == "" else ""
            blocks.append(Block("bullet", [(indent + "•  ", "bullet")] + _inline(content)))
            i += 1
            continue
        m = _NUMBER.match(line)
        if m:
            blocks.append(Block("number", [(f"{m.group(1)}.  ", "bullet")] + _inline(m.group(2))))
            i += 1
            continue
        m = _QUOTE.match(line)
        if m:
            blocks.append(Block("quote", [("│  ", "quote")] + _inline(m.group(1))))
            i += 1
            continue
        # paragraph: merge consecutive plain lines
        para = [line]
        while i + 1 < n:
            nxt = lines[i + 1]
            s = nxt.strip()
            if (not s or _FENCE.match(s) or _HEADING.match(s) or _RULE.match(s)
                    or _BULLET.match(nxt) or _NUMBER.match(nxt) or _QUOTE.match(nxt)):
                break
            para.append(nxt)
            i += 1
        blocks.append(Block("text", _inline(" ".join(p.strip() for p in para))))
        i += 1
    return blocks


def has_table(markdown: str) -> bool:
    """Cheap detector: a | row followed by a |--| row."""
    lines = [l.strip() for l in (markdown or "").splitlines()]
    for a, b in zip(lines, lines[1:]):
        if ("|" in a and "|" in b and re.match(r"^[\s|:-]+$", b) and "-" in b):
            return True
    return False
