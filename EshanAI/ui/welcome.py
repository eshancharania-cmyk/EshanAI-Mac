"""Welcome (empty-chat) panel — the app's first impression.

Shown whenever a conversation has no messages: a centered greeting with the
time of day, a one-line "what it can do" note, and clickable starter pills
that send their prompt with one click (ChatGPT-style empty state).
"""
from __future__ import annotations

import datetime
import tkinter as tk

import customtkinter as ctk

from . import theme


# (button label, prompt sent on click) — showcases the three core abilities:
# local knowledge + tools, PC control, and persistent memory.
STARTERS: list[tuple[str, str]] = [
    ("Tell me about this PC", "What are this PC's specs?"),
    ("Open the calculator", "Open the calculator app"),
    ("Help me draft an email", "Help me draft a short, polite email"),
    ("I like short answers", "Remember this: I like short answers"),
]

HOVER_BG = "#23262D"          # chips' hover surface (slightly lighter than EVENT_BG)


def _greeting(now: datetime.datetime | None = None) -> str:
    h = (now or datetime.datetime.now()).hour
    if 5 <= h < 12:
        return "Good morning 👋"
    if 12 <= h < 18:
        return "Good afternoon 👋"
    return "Good evening 👋"


class Welcome(ctk.CTkFrame):
    def __init__(self, master, on_send=None, font_scale: float = 1.0, **kw):
        # explicit page color: CTk "transparent" has painted black on this build
        super().__init__(master, fg_color=theme.CHAT_BG, corner_radius=0, **kw)
        self._scale = max(0.5, float(font_scale))
        self._on_send = on_send

        s = self._fs
        body = tk.Frame(self, bg=theme.CHAT_BG, bd=0, highlightthickness=0)
        body.pack(expand=True)

        tk.Label(body, text="✦", font=theme.f(s(17)),
                 bg=theme.CHAT_BG, fg=theme.ACCENT_LIGHT,
                 bd=0, highlightthickness=0).pack(pady=(18, 0))

        tk.Label(body, text=_greeting(), justify="center",
                 font=theme.f(s(21), "bold"),
                 bg=theme.CHAT_BG, fg=theme.TEXT,
                 bd=0, highlightthickness=0).pack(pady=(8, 3))

        tk.Label(body, text="What can I help you with today?", justify="center",
                 font=theme.f(s(12)),
                 bg=theme.CHAT_BG, fg=theme.TEXT_2,
                 bd=0, highlightthickness=0).pack(pady=(0, 2))

        tk.Label(body, text="Runs fully on this PC — nothing leaves it.",
                 justify="center", font=theme.f(s(10)),
                 bg=theme.CHAT_BG, fg=theme.TEXT_3,
                 bd=0, highlightthickness=0).pack(pady=(0, 20))

        if self._on_send is not None:
            chips = tk.Frame(body, bg=theme.CHAT_BG, bd=0, highlightthickness=0)
            chips.pack(pady=(0, 14))
            chips.grid_columnconfigure(0, uniform="w")
            chips.grid_columnconfigure(1, uniform="w")
            for i, (label, prompt) in enumerate(STARTERS):
                btn = ctk.CTkButton(
                    chips, text=label, height=34, corner_radius=17,
                    fg_color=theme.EVENT_BG, hover_color=HOVER_BG,
                    border_width=1, border_color=theme.BORDER,
                    text_color=theme.TEXT_2, anchor="center",
                    font=theme.f(s(11)),
                    command=lambda p=prompt: self._send(p))
                btn.grid(row=i // 2, column=i % 2, padx=6, pady=6,
                         ipadx=10, sticky="nsew")

            tk.Label(body, text="Click a suggestion, or just type below ↓",
                     justify="center", font=theme.f(s(9)),
                     bg=theme.CHAT_BG, fg=theme.TEXT_3,
                     bd=0, highlightthickness=0).pack()

    def _fs(self, size: float) -> int:
        return max(8, int(round(size * self._scale)))

    def _send(self, prompt: str):
        if self._on_send is None:
            return
        try:
            self._on_send(prompt)
        except Exception:
            pass
