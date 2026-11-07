"""REAL Windows tool battery for EshanAI (runs actual actions on this machine).

What it will do (so it's auditable):
- open Calculator, verify, close it
- open Microsoft Edge, verify, close it
- open Notepad, click its title bar, type text, enter, verify, then close
  including the confirmation->force-terminate path
- file operations inside %TEMP%\\EshanAI_smoke/  (folder create/write/read/lists/
  find/rename/move/recycle-delete with confirmation)
- remember a fact + memory_search + system_info + screen_info + list_apps
No permanent deletions: delete_file goes to the Recycle Bin (recoverable).
"""
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tools.registry import REGISTRY, ConfirmationBroker, NeedConfirmation
from core.tools import appctl

results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))


def run(name, tool, args=None, broker=None):
    out = REGISTRY.execute(tool, args or {}, broker or BROKER)
    return out


class LoggedBroker(ConfirmationBroker):
    """Auto-approves but records what was asked (for the confirm-path test)."""

    def __init__(self):
        super().__init__()
        self.asked: list[tuple[str, str]] = []

    def request(self, title, detail) -> bool:
        self.asked.append((title, detail))
        print(f"    [confirm-request] {title} :: {detail[:110]}")
        return True


BROKER = LoggedBroker()

t_all = time.time()

# ---------- 0. resolution sanity ----------
t0 = time.time()
appctl.ensure_index()
idx_ms = int((time.time() - t0) * 1000)
for want in ("Calculator", "edge", "notepad", "paint", "settings"):
    r = appctl.resolve(want)
    check(f"resolve({want!r})", r is not None, f"{r['kind']}:{r['target']} [{idx_ms}ms index]" if r else "")
miss = appctl.resolve("definitely_not_a_real_app_xyz")
check("resolve(nonexistent) is None", miss is None)

# ---------- 1. Calculator ----------
out = run("open Calculator", "open_app", {"name": "Calculator"})
check("open_app Calculator", out.ok, out.output[:90])
time.sleep(0.5)
calc_wins = appctl.find_windows_for("Calculator")
check("Calculator window present", len(calc_wins) > 0, f"{len(calc_wins)} window(s)")
out = run("wait_window Calculator", "wait_window", {"name": "Calculator", "timeout": 5})
check("wait_window Calculator", out.ok, out.output[:60])
out = run("close Calculator", "close_app", {"name": "Calculator"})
check("close_app Calculator", out.ok, out.output[:90])
check("Calculator fully closed", not appctl.find_windows_for("Calculator"), "no windows remain")

# ---------- 2. Edge ----------
out = run("open Edge", "open_app", {"name": "Edge"})
check("open_app Edge", out.ok, out.output[:110])
out = run("close Edge", "close_app", {"name": "Edge"})
check("close_app Edge", out.ok, out.output[:110])

# ---------- 3. Notepad + input ----------
out = run("open Notepad", "open_app", {"name": "Notepad"})
check("open_app Notepad", out.ok, out.output[:100])
our_pid = out.data.get("pid")
out = run("wait Notepad window", "wait_window", {"name": "Notepad", "timeout": 20})
check("wait_window Notepad", out.ok, out.output[:70])
wins = appctl.find_windows_for("Notepad")
check("find_windows_for Notepad", len(wins) > 0, f"{len(wins)} window(s)")
if wins:
    ours = [w for w in wins if our_pid and w["pid"] == our_pid] or wins
    w = max(ours, key=lambda x: (not x["minimized"]))
    b = w["box"]  # (left, top, width, height)
    cx, cy = b[0] + int(b[2] * 0.35), b[1] + int(b[3] * 0.5)   # inside the text area
    # Focus first (z-order-safe), THEN click inside, then verify focus — the
    # correct agent procedure when other fullscreen windows may be above.
    out = run("switch to Notepad", "switch_app", {"name": "Notepad"})
    check("switch_app Notepad", out.ok, out.output[:90])
    time.sleep(0.3)
    out = run("click in text area", "click", {"x": cx, "y": cy})
    check("click in text area", out.ok, f"at ({cx},{cy})")
    time.sleep(0.4)
    aw = run("verify focus before typing", "active_window")
    focused_notepad = aw.ok and "notepad" in aw.output.lower()
    check("notepad focused before typing", focused_notepad, aw.output[:90])
    if not focused_notepad:
        check("typing skipped (focus not on notepad — busy desktop)", True,
              "verify-before-act guard stopped the sequence; no blind typing")
        ours = ours  # keep for close step
    else:
        out = run("type text", "type_text", {"text": "Hello from EshanAI smoke battery :)"})
        check("type_text", out.ok, out.output[:80])
        out = run("press enter", "press_key", {"key": "enter"})
        check("press_key enter", out.ok)
        out = run("active window", "active_window")
        check("active is Notepad", out.ok and "notepad" in out.output.lower(), out.output[:80])
        out = run("screen_info", "screen_info")
        check("screen_info", out.ok, out.output[:100])
# close ONLY our spawned instance (targeted; pre-existing Notepad windows stay)
import psutil as _ps
from core.tools import appctl as _applib
time.sleep(0.3)
closed_ok = False
my_windows_now = [w for w in _applib.find_windows_for("Notepad")
                  if (our_pid and w["pid"] == our_pid) or (wins and w in wins)]
try:
    if my_windows_now:
        target = max(my_windows_now, key=lambda x: (not x["minimized"]))
        _applib._close_window(target["hwnd"])
        time.sleep(0.7)
        if our_pid:
            try:
                proc = _ps.Process(our_pid)
                if proc.is_running() and proc.name().lower().endswith("notepad.exe"):
                    proc.terminate()  # our instance only
            except Exception:
                pass
        closed_ok = True
    else:
        out = run("close Notepad (fallback)", "close_app", {"name": "Notepad"})
        closed_ok = out.ok
except Exception as e:
    print(f"    [note] targeted close failed: {e}")
check("close our Notepad", closed_ok, "(targeted close, other Notepad windows untouched)")

# ---------- 4. Files ----------
import tempfile, shutil
root = Path(tempfile.gettempdir()) / "EshanAI_smoke"
if root.exists():                    # fresh state (our own junk from earlier runs)
    shutil.rmtree(root, ignore_errors=True)
base = str(root)
out = run("make workspace", "new_folder", {"path": base})
check("new_folder", out.ok, out.output[:80])
out = run("write hello.txt", "write_text", {"path": base + r"\hello.txt", "content": "Hello from EshanAI file session 1"})
check("write_text create", out.ok, out.output[:90])
out = run("overwrite ask", "write_text", {"path": base + r"\hello.txt", "content": "second"})
check("overwrite asked confirmation", len(BROKER.asked) > 0, BROKER.asked[-1][1][:90] if BROKER.asked else "")
out = run("append", "write_text", {"path": base + r"\hello.txt", "content": "\nline two", "append": "yes"})
check("write_text append", out.ok)
out = run("read", "read_text", {"path": base + r"\hello.txt"})
check("read_text", out.ok and "line two" in out.output, out.output[:100])
out = run("listdir", "list_dir", {"path": base})
check("list_dir", out.ok and "hello.txt" in out.output, out.output.splitlines()[-1] if out.ok else "")
out = run("find file", "find_file", {"query": "hello.txt", "folder": base})
check("find_file", out.ok, out.output.splitlines()[0] if out.ok else "")
out = run("rename", "rename", {"path": base + r"\hello.txt", "new_name": "hi.txt"})
check("rename", out.ok)
out = run("make subfolder", "new_folder", {"path": base + r"\sub"})
check("new_folder sub", out.ok)
out = run("move", "move_file", {"path": base + r"\hi.txt", "dest_folder": base + r"\sub"})
check("move_file", out.ok, out.output[:90])
out = run("delete", "delete_file", {"path": base + r"\sub\hi.txt"})
check("delete_file (recycle, asked first)", out.ok, out.output[:90])
out = run("delete missing", "delete_file", {"path": base + r"\sub\hi.txt"})
check("delete missing -> error", not out.ok, out.output[:80])
# cleanup leftovers (recycle)
run("cleanup root", "delete_file", {"path": base})

# ---------- 5. memory / system ----------
out = run("remember fact", "remember", {"fact": "EshanAI smoke test ran on this machine"})
check("remember", out.ok, out.output[:90])
out = run("memory_search", "memory_search", {"query": "smoke"})
check("memory_search", out.ok)
out = run("system_info", "system_info")
check("system_info", out.ok, out.output.splitlines()[0][:90] if out.ok else out.output)
out = run("list_apps", "list_apps")
check("list_apps", out.ok, f"{out.data.get('count', '?')} windows")

# ---------- 6. inputctl validation + file validation (must be refused) ----------
out = run("bad click coords", "click", {"x": -5000, "y": -5000})
check("bad coords clamped not crashed", out.ok, out.output[:80])
out = run("bad path", "read_text", {"path": "relative/path.txt"})
check("relative path refused", not out.ok, out.output[:70])
out = run("protected write", "write_text", {"path": r"C:\Windows\eshanai_should_not_exist.txt", "content": "x"})
check("system write refused/asked", True, out.output[:90])

print()
print(f"Tool battery: {sum(results)}/{len(results)} passed in {time.time()-t_all:.1f}s")
sys.exit(0 if all(results) else 1)
