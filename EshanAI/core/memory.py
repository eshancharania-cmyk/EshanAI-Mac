"""Long-term memory: reliable save + cheap relevance retrieval (no embeddings)."""
from __future__ import annotations

import re
import time

from . import db

_SAVE_PATTERNS = [
    r"^\s*(please\s+)?(always\s+|now\s+)?remember\s+(this|that|it[,\s]|:\s?)",
    r"^\s*(please\s+)?(always\s+)?remind\s+me\s+that\s+",
    r"^\s*save\s+(this|that|this\s+fact|that\s+fact)\b",
    r"^\s*store\s+(this|that)\b",
    r"^\s*note\s+(this|that)\s+down\b:",
    r"^\s*(keep\s+in\s+mind|my\s+name\s+is|call\s+me)\b",
    r"^\s*i\s+(?:really\s+)?(?:prefer|like|love|hate|dislike)\b",
    r"^\s*i\s+am\s+working\s+on\b",
    r"^\s*my\s+(favourite|favorite)\b",
]
_SAVE_RE = re.compile("|".join(_SAVE_PATTERNS), re.IGNORECASE)

_STRIP_PREFIX = re.compile(
    r"^\s*(please\s+)?(always\s+|now\s+)?"
    r"(remember\s+(this:|that:|it[:,]?|this|that)|note\s+(this|that)\s+down:|"
    r"save\s+(this|that)(\s+for\s+me)?[:]?|store\s+(this|that)[:]?|keep\s+in\s+mind:?)\s*",
    re.IGNORECASE,
)


def looks_like_save_request(text: str) -> bool:
    return bool(_SAVE_RE.match(text))


def fact_from_save_request(text: str) -> str | None:
    """'remember this: I take iron at 9am' -> 'I take iron at 9am'."""
    stripped = _STRIP_PREFIX.sub("", text).strip()
    if not stripped or len(stripped) < 3:
        return None
    return stripped[:280]


def save_fact(content: str, source: str = "user") -> int | None:
    """Dedupe, tidy and store a fact. Returns memory id or None."""
    content = content.strip()
    if not content:
        return None
    content = re.sub(r"\s+", " ", content)
    if len(content) > 280:
        content = content[:277] + "…"
    # avoid junk like "OK", "here"
    if len(content.split()) < 2:
        return None
    return db.add_memory(content, source)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']{2,}", text.lower())}


def relevant(text: str, limit: int = 5) -> list[str]:
    """Most relevant memories for a user message (keyword overlap, recency boost)."""
    mems = db.get_memories(limit=500)
    if not mems:
        return []
    qt = _tokens(text)
    if not qt:
        return []
    df = {}
    docs = []
    for m in mems:
        toks = _tokens(m["content"])
        docs.append((m, toks))
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n = max(1, len(mems))
    now = time.time()
    scored = []
    for m, toks in docs:
        if not toks:
            continue
        score = 0.0
        for t in qt & toks:
            if t in df and len(toks) > 0:
                score += 1.5 / (1 + df[t] / n)
        if score <= 0:
            continue
        age_h = (now - m["last_used"]) / 3600.0
        score += 0.25 / (1 + age_h / 24)
        scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    out = [m["content"] for _, m in scored[:limit]]
    if out:
        db.mark_memory_used([m["id"] for _, m in scored[:limit]])
    return out
