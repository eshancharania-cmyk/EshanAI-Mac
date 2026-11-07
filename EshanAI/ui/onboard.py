"""First-run onboarding card for EshanAI.

Covers the whole chat pane (like the voice face) and walks a brand-new
user through, mostly autonomously:

  1. NAME  — "what should I call you?" saved as the FIRST memory and
             into settings (user.name), used until they change it
             (via the MEM button).
  2. SETUP — automatic, one background thread: Ollama engine,
             brain model download (~2.6 GB) with live %, offline
             whisper voice (~75 MB). Live checklist + progress bars.
  3. GUIDE — where everything is and how to use it.

Skip is always available; the app stays fully usable regardless.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from core import config
from core.setup_flow import SetupFlow
from ui import theme

GUIDE_ROWS = [
    ("🎤", "Voice mode",
     "Click the small mic bubble beside the send arrow. Hold SPACE "
     "while you talk, release to send; EshanAI answers OUT LOUD. "
     "Hold SPACE again to interrupt. CTRL+S closes the face."),
    ("💬", "Chatting",
     "Type in the bottom box and press Enter or the arrow. EshanAI "
     "thinks locally, remembers the facts you let it save, and keeps "
     "every conversation in the left sidebar."),
    ("🧠", "Memory (MEM)",
     "The MEM button at the bottom-left shows what EshanAI remembers — "
     "including your name. Edit or delete anything you want there; it "
     "uses your memories automatically while replying."),
    ("🎚️", "Settings",
     "The Settings button (bottom-left) changes the text size (Ctrl+wheel "
     "also zooms the chat) and other personal touches."),
    ("🤖", "Booting fresh",
     "If Ollama ever fails to wake up, the top-right chip turns red — "
     "just reopen EshanAI and this setup runs again to fix it."),
]


class OnboardingCard(ctk.CTkFrame):
    """Covers the right pane for first-run setup."""

    def __init__(self, master, app, on_finish):
        super().__init__(master, fg_color="#1B1D21", corner_radius=0)
        self._app = app
        self._on_finish = on_finish
        self._name = ""
        self._flow = None
        try:
            self._fs = float(app._font_scale)
        except Exception:
            self._fs = 1.35
        self._build_name_phase()

    # ---------------- helpers ----------------
    def _f(self, size, weight="normal"):
        return theme.f(size * self._fs / 1.2, weight)

    def _title(self, text):
        return ctk.CTkLabel(self, text=text, font=self._f(24, "bold"),
                            text_color=theme.TEXT, wraplength=560,
                            justify="left")

    def _sub(self, text, color=None):
        return ctk.CTkLabel(
            self, text=text,
            font=self._f(12.5), text_color=color or theme.TEXT_2,
            wraplength=560, justify="left")

    def _clear(self):
        for w in self.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def _finish(self):
        try:
            config.set("setup.done", True)
            config.save()
        except Exception:
            pass
        try:
            self._on_finish()
        except Exception:
            pass

    def _skip(self):
        """Skip everything — still usable, asks again on next launch."""
        self._finish()

    # ---------------- phase 1: name ----------------
    def _build_name_phase(self):
        self._clear()
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.place(relx=0.42, rely=0.46, anchor="center")

        self._title("👋  Welcome to EshanAI").pack(anchor="w")
        self._sub("Everything EshanAI does works on this computer — no "
                  "cloud, no accounts, nothing leaves your machine.").pack(
            anchor="w", pady=(10, 4))
        self._sub("First: what should I call you? This becomes my very "
                  "first memory, and I'll use it until you change it.",
                  theme.TEXT_3).pack(anchor="w", pady=(0, 18))

        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(anchor="w")
        self._name_entry = ctk.CTkEntry(
            row, width=270, height=44, font=self._f(14),
            placeholder_text="Your name…",
            fg_color="#20232A", border_color="#343943",
            text_color=theme.TEXT)
        self._name_entry.grid(row=0, column=0, padx=(0, 10))
        self._name_entry.bind("<Return>", lambda e: self._go_name())

        btn = ctk.CTkButton(row, text="Let's go  →", width=130, height=44,
                            font=self._f(13, "bold"),
                            command=self._go_name)
        btn.grid(row=0, column=1)

        self._name_status = self._sub("", theme.TEXT_3)
        self._name_status.pack(anchor="w", pady=(12, 0))

        skip = ctk.CTkLabel(self, text="skip for now »",
                            font=self._f(10.5), text_color=theme.TEXT_3,
                            cursor="hand2")
        skip.place(relx=0.96, rely=0.05, anchor="ne")
        skip.bind("<Button-1>", lambda e: self._skip())

        self.after(200, self._name_entry.focus_set)

    def _go_name(self):
        name = (self._name_entry.get() or "").strip() if hasattr(
            self, "_name_entry") else ""
        if not name:
            self._name_status.configure(
                text="Please tell me a name (or press Enter) — anything "
                     "you like.", text_color=theme.WARN)
            return
        name = name[:40]
        self._name = name
        try:
            self._app.apply_memory_note(f"My name is {name}.")
        except Exception:
            pass
        try:
            config.set("user.name", name)
            config.save()
        except Exception:
            pass
        self._build_setup_phase()

    # ---------------- phase 2: automatic setup ----------------
    def _build_setup_phase(self):
        self._clear()
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.place(relx=0.42, rely=0.46, anchor="center")

        self._title(f"Nice to meet you, {self._name}!").pack(anchor="w")
        self._sub("I'm setting everything up automatically — give me a "
                  "couple of minutes and don't worry about a thing. You "
                  "can watch the checklist below.").pack(
            anchor="w", pady=(8, 14))

        self._note = self._sub("", theme.TEXT_3)
        self._note.pack(anchor="w", pady=(0, 10))

        self._rows = {}
        for key in ("engine", "brain", "voice"):
            row = ctk.CTkFrame(wrap, fg_color="#20232A", corner_radius=10)
            row.pack(anchor="w", fill="x", pady=5, ipadx=8, ipady=8)
            head = ctk.CTkFrame(row, fg_color="transparent")
            head.pack(fill="x", padx=14, pady=(8, 0))
            title = ctk.CTkLabel(head, text="…", font=self._f(13, "bold"),
                                 text_color=theme.TEXT)
            title.pack(side="left")
            state = ctk.CTkLabel(head, text="waiting", font=self._f(11),
                                 text_color=theme.TEXT_3)
            state.pack(side="right")
            bar = ctk.CTkProgressBar(head, width=150, height=9,
                                     progress_color=theme.ACCENT,
                                     fg_color="#15171B")
            bar.set(0.0)
            self._rows[key] = {"title": title, "state": state, "bar": bar}

        skip = ctk.CTkLabel(self, text="skip the download stuff »",
                            font=self._f(10.5), text_color=theme.TEXT_3,
                            cursor="hand2")
        skip.place(relx=0.96, rely=0.05, anchor="ne")
        skip.bind("<Button-1>", lambda e: self._skip())

        self._flow = SetupFlow(self._emit_flow_event)
        import threading
        threading.Thread(target=self._flow.run, daemon=True).start()

    def _emit_flow_event(self, ev):
        "SetupFlow thread -> app event queue -> main thread."
        self._app._events.put({"type": "_setup", **ev})

    def on_flow_event(self, ev):
        "Touch widgets ONLY here (called from the UI dispatch)."
        key = ev.get("key")
        if key == "flow":
            self.after(500, self._build_guide_phase)
            return
        row = self._rows.get(key)
        if row is None:
            return
        which = ev.get("ev")
        if which == "start":
            row["title"].configure(text=ev.get("label", "…"))
            row["state"].configure(text="working…",
                                   text_color=theme.ACCENT)
        elif which == "progress":
            frac = ev.get("frac")
            if frac:
                try:
                    row["bar"].set(frac)
                except Exception:
                    pass
            row["state"].configure(text=ev.get("text", ""),
                                   text_color=theme.TEXT_2)
        elif which == "state":
            ok = ev.get("ok")
            color = theme.OK if ok else theme.WARN
            sign = "✓" if ok else "!"
            row["state"].configure(text=f"{sign} {ev.get('note', '')}",
                                   text_color=color)
            try:
                row["bar"].set(1.0 if ok else row["bar"].get())
                row["bar"].configure(mode="determinate")
            except Exception:
                pass

    # ---------------- phase 3: guide ----------------
    def _build_guide_phase(self):
        self._clear()
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.place(relx=0.42, rely=0.10, anchor="n")

        self._title(f"All set, {self._name or 'friend'}! ✨").pack()
        self._sub("Here's everything you need to know — it takes one "
                  "minute:").pack(anchor="w", pady=(8, 4))

        body = ctk.CTkScrollableFrame(self, width=640, height=330,
                                      fg_color="#191B1F",
                                      scrollbar_button_color="#2A2D34")
        body.place(relx=0.42, rely=0.16)
        for emoji, head_t, text in GUIDE_ROWS:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=(6, 2))
            ctk.CTkLabel(row, text=emoji, font=self._f(15),
                         text_color=theme.ACCENT).pack(
                side="left", padx=(2, 10), anchor="n")
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(inner, text=head_t, font=self._f(12.5, "bold"),
                         text_color=theme.TEXT, anchor="w").pack(
                fill="x", anchor="w")
            ctk.CTkLabel(inner, text=text, font=self._f(11),
                         text_color=theme.TEXT_2, anchor="w",
                         wraplength=520, justify="left").pack(
                fill="x", anchor="w")

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.place(relx=0.42, rely=0.87, anchor="center")
        ctk.CTkButton(foot, text="Start exploring  ✓", width=210,
                      height=44, font=self._f(13, "bold"),
                      command=self._finish).pack()

        self._sub("TIP: everything runs locally on this PC. EshanAI "
                  "works exactly the same even with no internet.",
                  theme.TEXT_3).place(relx=0.42, rely=0.955, anchor="center")
