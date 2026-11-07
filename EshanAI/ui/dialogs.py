"""Dialogs: confirmation broker bridge, settings, memory manager, small modals."""
from __future__ import annotations

import threading

import customtkinter as ctk

from . import theme


# ================================================================ broker

class UIConfirmationBroker:
    """Bridges blocking agent-thread confirmations into the Tk event loop.

    Agent thread: request(title, detail) -> parks on a threading.Event.
    UI thread (30ms pump): pop_pending() -> show modal -> resolve(ok).
    Works even if two tool confirmations stack (Pareto: brain runs one loop).
    """

    def __init__(self, timeout_s: float = 180):
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._queue: list[dict] = []

    def request(self, title: str, detail: str) -> bool:
        evt = threading.Event()
        item = {"title": title, "detail": detail, "evt": evt, "ok": False}
        with self._lock:
            self._queue.append(item)
        evt.wait(timeout=self.timeout_s)
        with self._lock:
            self._queue.remove(item)
        return bool(item["ok"])

    def pop_pending(self) -> dict | None:
        with self._lock:
            return self._queue[0] if self._queue else None


# ================================================================ helpers

def _finish(dialog: ctk.CTkToplevel, value):
    dialog.result = value
    try:
        dialog.grab_release()
    except Exception:
        pass
    dialog.destroy()


def _show(dialog: ctk.CTkToplevel, master):
    dialog.transient(master)
    dialog.attributes("-topmost", True)
    dialog.update_idletasks()
    w, h = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    try:
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass
    dialog.deiconify()
    dialog.focus_force()
    dialog.grab_set()


class ConfirmDialog(ctk.CTkToplevel):
    """Modal Yes-or-No panel; call .wait_dialog() after _show."""

    def __init__(self, master, title: str, detail: str,
                 ok_text: str = "Yes", cancel_text: str = "No"):
        super().__init__(master, fg_color=theme.SIDEBAR_BG)
        self.title("")
        self.resizable(False, False)
        if title.startswith("⚠"):
            head_col = theme.WARN
            title = title[1:].strip()
        else:
            head_col = theme.TEXT
        ctk.CTkLabel(self, text=title, font=theme.f(14, "bold"),
                     text_color=head_col, justify="left").pack(padx=22, pady=(18, 6),
                                                               anchor="w")
        ctk.CTkLabel(self, text=detail, font=theme.f(11.5), text_color=theme.TEXT_2,
                     justify="left", wraplength=380).pack(padx=22, pady=(0, 12),
                                                          anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 16))
        ctk.CTkButton(row, text=cancel_text, width=96, height=34, corner_radius=8,
                      fg_color="transparent", border_width=1,
                      border_color=theme.BORDER, hover_color=theme.CHAT_BG,
                      text_color=theme.TEXT_2, font=theme.f(11.5),
                      command=lambda: _finish(self, False)).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text=ok_text, width=96, height=34, corner_radius=8,
                      fg_color=theme.OK, hover_color="#4E9C72", text_color="#082514",
                      font=theme.f(11.5, "bold"),
                      command=lambda: _finish(self, True)).pack(side="right")
        self.result = None

    def wait_dialog(self):
        _show(self, self.master)
        self.wait_window()
        return bool(self.result)


class TextEntryDialog(ctk.CTkToplevel):
    """One-line text prompt (rename conversation)."""

    def __init__(self, master, title: str, value: str = ""):
        super().__init__(master, fg_color=theme.SIDEBAR_BG)
        self.title("")
        self.resizable(False, False)
        ctk.CTkLabel(self, text=title, font=theme.f(13.5, "bold"),
                     text_color=theme.TEXT, justify="left").pack(padx=22, pady=(18, 8),
                                                                 anchor="w")
        self.entry = ctk.CTkEntry(self, width=340, height=34, corner_radius=8,
                                  fg_color=theme.EVENT_BG, border_color=theme.BORDER,
                                  font=theme.f(12), text_color=theme.TEXT)
        self.entry.pack(padx=22, pady=(0, 4))
        if value:
            self.entry.insert(0, value)
        self.entry.bind("<Return>", lambda e: _finish(self, self.entry.get().strip()))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(8, 16))
        ctk.CTkButton(row, text="Cancel", width=96, height=32, corner_radius=8,
                      fg_color="transparent", border_width=1,
                      border_color=theme.BORDER, hover_color=theme.CHAT_BG,
                      text_color=theme.TEXT_2, font=theme.f(11.5),
                      command=lambda: _finish(self, None)).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text="Save", width=96, height=32, corner_radius=8,
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_LIGHT,
                      text_color="#FFFFFF", font=theme.f(11.5, "bold"),
                      command=lambda: _finish(self, self.entry.get().strip()))\
            .pack(side="right")
        self.result = None

    def wait_dialog(self):
        _show(self, self.master)
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.wait_window()
        return self.result


class ConfirmBrokerModal:
    """One-shot wrapper for the confirmation pump: shows ConfirmDialog and
    resolves the pending broker item on any result."""

    @staticmethod
    def display(master, item: dict) -> None:
        ok = ConfirmDialog(master, item["title"], item["detail"]).wait_dialog()
        item["ok"] = ok
        try:
            item["evt"].set()
        except Exception:
            pass


# ================================================================ memory

class MemoryDialog(ctk.CTkToplevel):
    """Persistent memory manager: view / add / delete facts."""

    def __init__(self, master, db):
        super().__init__(master, fg_color=theme.SIDEBAR_BG)
        self.db = db
        self.title("EshanAI — Memory")
        self.geometry("600x520")
        self.minsize(520, 380)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(head, text="Memory", font=theme.f(16, "bold"),
                     text_color=theme.TEXT).pack(side="left")
        ctk.CTkLabel(head, text="Facts EshanAI injects into every relevant turn",
                     font=theme.f(10.5), text_color=theme.TEXT_3).pack(side="left", padx=10)

        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(fill="x", padx=18, pady=(0, 8))
        self.new_var = ctk.CTkEntry(add_row, placeholder_text="Add a fact…",
                                    height=34, corner_radius=9,
                                    fg_color=theme.EVENT_BG, border_color=theme.BORDER,
                                    font=theme.f(11.5), text_color=theme.TEXT,
                                    placeholder_text_color=theme.TEXT_3)
        self.new_var.pack(side="left", fill="x", expand=True)
        self.new_var.bind("<Return>", self._add)
        ctk.CTkButton(add_row, text="Add", width=78, height=34, corner_radius=9,
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_LIGHT,
                      text_color="#FFFFFF", font=theme.f(11.5, "bold"),
                      command=self._add).pack(side="left", padx=(8, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=theme.CHAT_BG, corner_radius=10,
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.TEXT_3)
        self.list_frame.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        ctk.CTkButton(self, text="Clear all memory", height=30, corner_radius=8,
                      fg_color="transparent", border_width=1,
                      border_color=theme.ERR, hover_color="#3A1D20",
                      text_color=theme.ERR, font=theme.f(11),
                      command=self._clear_all).pack(padx=18, pady=(0, 16))

        self._reload()

    def _reload(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        rows = self.db.get_memories(500)
        if not rows:
            ctk.CTkLabel(self.list_frame, text="No memories saved yet.",
                         font=theme.f(11.5), text_color=theme.TEXT_3).pack(pady=26)
            return
        for m in rows:
            row = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2, padx=8)
            txt = m["content"].replace("\n", " ")[:220]
            ctk.CTkLabel(row, text=txt, font=theme.f(11.5), text_color=theme.TEXT,
                         anchor="w", justify="left", wraplength=460).pack(
                side="left", fill="x", expand=True, pady=7)
            ctk.CTkButton(row, text="✕", width=30, height=26, corner_radius=6,
                          fg_color="transparent", hover_color="#3A1D20",
                          text_color=theme.ERR, font=theme.f(11, "bold"),
                          command=lambda mid=m["id"]: self._del(mid)).pack(side="right")

    def _add(self, event=None):
        txt = self.new_var.get().strip()
        if txt:
            self.db.add_memory(txt, source="user")
            self.new_var.delete(0, "end")
            self._reload()

    def _del(self, mem_id: int):
        self.db.delete_memory(mem_id)
        self._reload()

    def _clear_all(self):
        if ConfirmDialog(self, "⚠ Clear all memory?", 
                         "Every saved fact will be permanently deleted.").wait_dialog():
            for m in self.db.get_memories(500):
                self.db.delete_memory(m["id"])
            self._reload()


# ================================================================ settings

class SettingsDialog(ctk.CTkToplevel):
    """Behavior settings. Applied to next turn; font scale rebuilds the chat UI."""

    def __init__(self, master, config, font_scale: float):
        super().__init__(master, fg_color=theme.SIDEBAR_BG)
        self.cfg = config
        self.title("EshanAI — Settings")
        self.geometry("520x540")
        self.minsize(500, 460)
        self.result = None
        self._scale = font_scale

        pad = {"padx": 20, "anchor": "w"}
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Settings", font=theme.f(16, "bold"),
                     text_color=theme.TEXT).pack(padx=20, pady=(16, 8), anchor="w")

        box = ctk.CTkFrame(self, fg_color=theme.CHAT_BG, corner_radius=10)
        box.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        # temperature
        ctk.CTkLabel(box, text="Creativity (temperature)",
                     font=theme.f(11.5), text_color=theme.TEXT_2).pack(**pad, pady=(14, 2))
        self.temp = ctk.CTkSlider(box, from_=0.0, to=1.4, number_of_steps=28,
                                  button_color=theme.ACCENT, progress_color=theme.ACCENT)
        self.temp.set(float(self.cfg.get("generation.temperature", 0.5)))
        self.temp.pack(fill="x", padx=20, pady=(0, 10))
        self._temp_lbl = ctk.CTkLabel(box, text="", font=theme.f(10.5),
                                      text_color=theme.TEXT_3)
        self._temp_lbl.pack(**pad)
        self.temp.configure(command=lambda v: self._temp_lbl.configure(
            text=f"current: {float(v):.2f}"))
        self._temp_lbl.configure(text=f"current: {self.temp.get():.2f}")

        # max steps
        ctk.CTkLabel(box, text="Max tool steps per turn (3–20)",
                     font=theme.f(11.5), text_color=theme.TEXT_2).pack(**pad, pady=(10, 2))
        self.steps = ctk.CTkEntry(box, width=90, corner_radius=8, fg_color=theme.EVENT_BG,
                                  border_color=theme.BORDER, font=theme.f(11.5),
                                  text_color=theme.TEXT)
        self.steps.pack(**pad, pady=(0, 10))
        self.steps.insert(0, str(int(self.cfg.get("agent.max_tool_steps", 8))))

        # switches
        self.auto_title = self._switch(box, "Generate conversation titles automatically",
                                       self.cfg.get("agent.auto_title", True))
        self.inject = self._switch(box, "Inject relevant memories into turns",
                                   self.cfg.get("agent.inject_memories", True))

        # font scale
        ctk.CTkLabel(box, text="Text size", font=theme.f(11.5),
                     text_color=theme.TEXT_2).pack(**pad, pady=(10, 2))
        self.fontscale = ctk.CTkSlider(box, from_=0.85, to=1.5, number_of_steps=13,
                                       button_color=theme.ACCENT,
                                       progress_color=theme.ACCENT)
        self.fontscale.set(float(self.cfg.get("ui.font_scale", 1.0)))
        self.fontscale.pack(fill="x", padx=20, pady=(0, 4))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 18))
        ctk.CTkButton(btns, text="Cancel", width=104, height=34, corner_radius=8,
                      fg_color="transparent", border_width=1, border_color=theme.BORDER,
                      hover_color=theme.CHAT_BG, text_color=theme.TEXT_2,
                      font=theme.f(11.5), command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text="Save", width=104, height=34, corner_radius=8,
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_LIGHT,
                      text_color="#FFFFFF", font=theme.f(11.5, "bold"),
                      command=self._save).pack(side="right")

        _show(self, master)

    def _switch(self, master, label, value) -> ctk.CTkSwitch:
        sw = ctk.CTkSwitch(master, text=label, progress_color=theme.ACCENT,
                           font=theme.f(11.5), text_color=theme.TEXT_2)
        if value:
            sw.select()
        sw.pack(fill="x", padx=20, pady=(8, 2))
        return sw

    def _save(self):
        self.result = {
            "temperature": round(float(self.temp.get()), 2),
            "max_tool_steps": self._int(self.steps.get(), 3, 20, 8),
            "auto_title": bool(self.auto_title.get()),
            "inject_memories": bool(self.inject.get()),
            "font_scale": round(float(self.fontscale.get()), 2),
        }
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    @staticmethod
    def _int(value: str, lo: int, hi: int, default: int) -> int:
        try:
            return max(lo, min(hi, int(value.strip())))
        except Exception:
            return default
