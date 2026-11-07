"""Unit tests for the incremental JSON reply scanner (no Ollama needed)."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.brain import PartialReply, _extract_object

fails = []


def check(name, cond, extra=""):
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
    if not ok:
        fails.append(name)


def streamed(text: str, piece: int = 3):
    """Feed text in small chunks exactly like Ollama would."""
    p = PartialReply()
    deltas = []
    for i in range(0, len(text), piece):
        deltas.append(p.feed(text[i:i + piece]))
    return p, deltas, "".join(deltas)


# 1. plain reply assembled from deltas
p, deltas, got = streamed('{"reply": "Hello there, friend."}', 2)
check("plain reply streaming", got == "Hello there, friend.", f"got={got!r}")

# 2. escapes decoded, '\n' inside reply
raw = '{"reply": "Say \\"hi\\"\\nthen continue"}'
p, _, got = streamed(raw, 7)
check("escapes decode", got == 'Say "hi"\nthen continue', f"got={got!r}")

# 3. tool-only response: no visible deltas, tool captured
p, _, got = streamed('{"tool": "open_app", "args": {"name": "Calculator"}}', 4)
check("tool captured", p.tool == "open_app", f"tool={p.tool}")
check("tool-only: no text deltas", got == "", f"got={got!r}")

# 4. reply containing JSON-looking text inside the string
raw = '{"reply": "Use {\\"reply\\": \\"x\\"} to answer."}'
p, _, got = streamed(raw, 5)
check("inner escaped json not confused", got == 'Use {"reply": "x"} to answer.', f"got={got!r}")

# 5. partial trailing escape doesn't crash, partial text emitted before the escape completes
p, _, got = streamed('{"reply": "ab\\u00', 1)
check("cut unicode escape handled", got == "ab", f"got={got!r}")
p, _, got = streamed('{"reply": "ab\\', 1)
check("trailing backslash cut", got == "ab", f"got={got!r}")
p, _, got = streamed('{"reply": "ab\u00e9', 1)
check("complete unicode escape decodes", got == "ab\u00e9", f"got={got!r}")

# 6. tool first, reply after (transient progress visible)
raw = '{"tool":"open_app","args":{"name":"Notepad"},"reply":"Opening"}'
p, _, got = streamed(raw, 3)
check("tool captured when reply later", p.tool == "open_app", f"tool={p.tool}")
check("reply delta after tool", got == "Opening", f"got={got!r}")

# 7. finalize parse of complete doc
obj = _extract_object('{"reply": "final"}')
check("finalize reply", obj.get("reply") == "final")
obj = _extract_object('noise```json{"tool": "open_app", "args": {"name":"X"}}```')
check("finalize with fences+noise", obj.get("tool") == "open_app", f"obj={obj}")

# 8. multi-object trash tolerance
obj = _extract_object('{"reply": "a"} garbage trailing')
check("trailing garbage ok", obj.get("reply") == "a")

# 9. empty returns error not crash
try:
    _extract_object("   ")
    check("empty -> error", False)
except Exception:
    check("empty -> error", True)

print()
print(f"partial scanner: {'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
