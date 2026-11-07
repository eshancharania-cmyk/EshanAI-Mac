"""Phase 2 UI gate: boot the real EshanAI window, drive it programmatically,
run REAL model conversations through the UI, verify chips / streaming /
titles / history, screenshot into dev_data. Desktop-safe tools only.

Run from workdir:  python -u tests\\smoke_ui.py     (~2-5 min)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "dev_data"
os.environ["ESHANAI_DATA"] = str(DATA)
DATA.mkdir(exist_ok=True)
for f in ("eshanai.db", "eshanai.db-wal", "eshanai.db-shm"):
    p = DATA / f
    if p.exists():
        p.unlink()

from core import db                      # noqa: E402
from core import ollama_client as oc     # noqa: E402
from core.brain import wait_titles_done  # noqa: E402
from ui.app import EshanAIApp            # noqa: E402

fails = []
t_start = time.time()


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          f"{(' — ' + str(extra)) if extra else ''}")
    if not cond:
        fails.append(name)


app = EshanAIApp()

# ------------------------------------------------------------- pump helpers

def pump_for(seconds: float):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.update()
        time.sleep(0.02)


def wait_for(name, pred, timeout=240.0, poll=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        app.update()
        time.sleep(poll)
    print(f"    [timeout] {name} not satisfied after {timeout:.0f}s")
    return False


# ------------------------------------------------------------- 1. boot state
conv0 = app.conv_id
check("boot: conversation exists", db.get_conversation(conv0) is not None)
boot_txt = app.composer.input.get("1.0", "end-1c")
check("boot: composer placeholder (no user text)",
      boot_txt.strip() == "" or "Ask anything" in boot_txt, boot_txt[:40])
chip = wait_for("ollama chip", lambda: "Ollama" in app._topbar_chip_lbl.cget("text")
                or app._topbar_chip_lbl.cget("text") == "", timeout=8)
pump_for(1.0)
check("boot: ollama reported", app._ollama_ok is not None, f"chip={app._topbar_chip_lbl.cget('text')!r}")

# ------------------------------------------------------------- 2. math turn
delta_count = [0]
_orig_append = app.chat.append_stream


def counting_append(text):
    delta_count[0] += 1
    return _orig_append(text)


app.chat.append_stream = counting_append

turn1 = app.conv_id
app.send_text("What is 17 * 24? Answer in one short sentence.")
check("send: busy engaged", app._busy and app.composer.busy)
ok1 = wait_for("math final", lambda: not app._busy, timeout=240)
check("send: turn finished", ok1)
rows = app.chat._rows
check("send: user + assistant rows exist", len(rows) >= 2, f"{len(rows)} rows")
final1 = db.get_messages(turn1)
assist1 = [m["content"] for m in final1 if m["role"] == "assistant"]
check("math answer present", any("408" in c for c in assist1), str(assist1)[:120])
check("streaming deltas received", delta_count[0] > 0, f"{delta_count[0]} deltas")
tools1 = [m for m in final1 if m["role"] == db.TOOL]
check("math turn used no tools", not tools1, f"{len(tools1)} tool rows")
model1 = next((m["model"] for m in final1 if m["role"] == "assistant"), "")
check("main model used", "qwen" in model1.lower(), model1)

# ------------------------------------------------------------- 3. tool turn
app.send_text("Use the system_info tool and tell me this PC's CPU model.")
ok2 = wait_for("sysinfo final", lambda: not app._busy, timeout=240)
check("sysinfo turn finished", ok2)
msgs2 = db.get_messages(turn1)
sysinfo_chip = [m for m in msgs2 if m["role"] == db.TOOL
                and str((m.get("meta") or {}).get("tool", "")) == "system_info"
                and str((m.get("meta") or {}).get("status")) == "ok"]
check("system_info chip ok", bool(sysinfo_chip), f"{[(m.get('meta') or {}).get('tool') for m in msgs2 if m['role'] == db.TOOL]}")
assist2 = [m["content"] for m in msgs2 if m["role"] == "assistant"][-1]
check("cpu mentioned honestly", any(k in assist2.lower() for k in ("core", "i3", "i5", "i7", "i9",
      "ultra", "celeron", "pentium", "processor", "cpu")), assist2[:90])
check("chips visible in UI", len(app.chat._rows) >= 4, f"{len(app.chat._rows)} rows")

# ------------------------------------------------------------- 4. remember turn
app.send_text("Remember that my test badge is Silver Falcon 22.")
ok3 = wait_for("remember final", lambda: not app._busy, timeout=240)
check("remember turn finished", ok3)
mems = db.get_memories(50)
check("memory saved", any("Silver Falcon 22" in m["content"] for m in mems),
      str([m["content"] for m in mems])[:120])

# recall (same conversation is fine for UI path)
app.send_text("What test badge did I just tell you?")
ok4 = wait_for("recall final", lambda: not app._busy, timeout=240)
check("recall turn finished", ok4)
msgs4 = db.get_messages(turn1)
assist4 = [m["content"] for m in msgs4 if m["role"] == "assistant"][-1]
check("recall echoes badge", "silver falcon 22" in assist4.lower(), assist4[:110])

# ------------------------------------------------------------- 5. titles
title_ok = wait_titles_done(90)
pump_for(1.0)
title = db.get_conversation(turn1)["title"]
check("auto-title generated", title_ok and title and title != "New conversation", title)
row = app.sidebar._rows.get(turn1)
check("sidebar title updated", row is not None and row["label"].cget("text") == title,
      row["label"].cget("text") if row else None)

# ------------------------------------------------------------- 6. switch + history
app.new_chat()
pump_for(0.5)
check("new chat switches", app.conv_id != turn1 and not app.chat._rows)
app.sidebar._select(turn1)
pump_for(1.2)
check("history re-render after switch", len(app.chat._rows) >= 5, f"{len(app.chat._rows)} rows")

# ------------------------------------------------------------- 7. screenshot
sf = app.winfo_fpixels("1i") / 96.0
x, y = app.winfo_rootx(), app.winfo_rooty()
w, h = app.winfo_width(), app.winfo_height()
try:
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    time.sleep(0.25)
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=(int(x * sf), int(y * sf),
                               int((x + w) * sf), int((y + h) * sf)))
    out = DATA / "ui_screenshot.png"
    img.save(out)
    app.attributes("-topmost", False)
    check("screenshot saved", out.exists() and out.stat().st_size > 10_000,
          f"{out.name} {out.stat().st_size // 1024} KB")
except Exception as e:
    check("screenshot saved", False, e)

# ------------------------------------------------------------- 8. memory panel
try:
    app.open_memory()
    time.sleep(0.6)
    mem_dlg = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
    check("memory panel opened", bool(mem_dlg), str(len(mem_dlg)))
    for d in mem_dlg:
        try:
            d.grab_release()
            d.destroy()
        except Exception:
            pass
except Exception as e:
    check("memory panel opened", False, e)

pump_for(0.6)
elapsed = time.time() - t_start
app._on_close()
print()
print(f"UI gate: {'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}"
      f"  ({elapsed:.0f}s)")
sys.exit(1 if fails else 0)
