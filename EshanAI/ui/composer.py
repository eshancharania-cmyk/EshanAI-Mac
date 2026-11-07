"""Message composer — ChatGPT-style floating input pill:

  [ rounded box, centered to the message column, autogrows        ]
  [ typed text...                                    (send ↑) ]  ← button inside

Enter to send, Shift+Enter newline. Below the pill: a centered footnote
(state text replaces it while a turn runs).
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import theme

PLACEHOLDER = "Ask anything…"

MAX_COL = 768            # same column width as the chat messages
MAX_LINES = 9            # autogrow cap


class Composer(ctk.CTkFrame):
    def __init__(self, master, on_send, on_resize_hint=None, font_scale: float = 1.0,
                 on_mic=None, **kw):
        # explicit page color: "transparent" resolved BLACK for the empty
        # region under the pill on this CTk build (seen as a black band at
        # the window's bottom edge) — never trust CTk transparency here
        super().__init__(master, fg_color=theme.CHAT_BG, **kw)
        self.on_send = on_send
        self.on_mic = on_mic
        self.busy = False
        self._scale = font_scale
        self._hint = on_resize_hint

        self._pill = ctk.CTkFrame(
            self, fg_color=theme.COMPOSER_BG, corner_radius=24,
            border_width=1, border_color=theme.BORDER)
        self._pill.grid(row=0, column=0, sticky="ew", padx=(0, 0), pady=(6, 2))
        self.grid_columnconfigure(0, weight=1)

        self.input = ctk.CTkTextbox(
            self._pill, fg_color=theme.COMPOSER_BG, border_width=0, corner_radius=0,
            font=(theme.FONT_FAMILY, self._fs(12.5)), text_color=theme.TEXT,
            wrap="word", height=26, activate_scrollbars=False,
            scrollbar_button_color=theme.COMPOSER_BG,
            scrollbar_button_hover_color=theme.COMPOSER_BG)
        self.input.grid(row=0, column=0, sticky="ew", padx=(16, 4), pady=(10, 6))
        self._pill.grid_columnconfigure(0, weight=1)
        # same story as the chat: CTk negativizes tuple fonts (pixel size) —
        # set the real points font on the inner tk.Text directly
        try:
            self.input._textbox.configure(
                font=(theme.FONT_FAMILY, int(round(self._fs(12.5)))))
        except Exception:
            pass

        self.input.bind("<Return>", self._on_return)
        self.input.bind("<KeyRelease>", self._autogrow)
        self.input.bind("<<Paste>>", self._after_idle_autogrow)
        self.input.bind("<<Cut>>", self._after_idle_autogrow)
        # the CHAT pane binds <MouseWheel> on ALL widgets; when the composer is
        # scrollable we must intercept the wheel HERE (and return "break") so
        # typing/pasting more than the 9-line cap still reaches the text.
        self.input.bind("<MouseWheel>", self._wheel)
        self._pill.bind("<MouseWheel>", self._wheel)
        self.input.bind("<FocusIn>", self._focus_in)
        self.input.bind("<FocusOut>", self._focus_out)
        self.input.insert("1.0", PLACEHOLDER)
        self.input._textbox.tag_config("ph", foreground=theme.TEXT_3)
        self.input.tag_add("ph", "1.0", "end-1c")
        self._has_placeholder = True

        self.send_btn = ctk.CTkButton(
            self._pill, text="➤", width=30, height=30, corner_radius=999,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_LIGHT,
            font=(theme.FONT_FAMILY, self._fs(12.5), "bold"),
            command=self._do_send, text_color="#FFFFFF")
        self.send_btn.grid(row=0, column=2, padx=(4, 10), pady=(8, 4), sticky="e")

        # mic bubble — same side as send (user voice-design spec)
        self.mic_btn = ctk.CTkButton(
            self._pill, text="🎤", width=30, height=30, corner_radius=999,
            fg_color="#22242A", hover_color="#2A2D34", border_width=1,
            border_color=theme.BORDER,
            font=(theme.FONT_FAMILY, self._fs(11)),
            command=self._mic_pressed, text_color=theme.TEXT_2)
        self.mic_btn.grid(row=0, column=1, padx=(4, 2), pady=(8, 4), sticky="e")
        self._scrolled = False                   # composer view is scrolled back

        # PLAIN tk label (like the chip/user labels): CTk transparent labels
        # paint a BLACK canvas bg on this build — the footnote once sat on an
        # invisible black band; explicit colors are deterministic.
        self.note = tk.Label(self, text="", height=1, justify="center",
                             font=(theme.FONT_FAMILY, int(round(self._fs(10)))),
                             bg=theme.CHAT_BG, fg=theme.TEXT_3,
                             bd=0, highlightthickness=0)
        self.note.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        self.set_note("")                       # shows the default footnote
        self._update_send()                     # dim until the user types

    # ---------------- layout ----------------
    def _fs(self, size: float) -> float:
        return max(9.0, size * self._scale)

    def on_resize(self, width: float | None = None):
        """Center the pill to the same column as the chat (called on window resize)."""
        try:
            if width is None:
                width = self.master.winfo_width() or self.winfo_width()
            ws = self._wscale()
            avail = width - 6 * ws
            col = min(avail - 36 * ws, MAX_COL * ws)
            inset = max((avail - int(col)) // 2, 20)
            self._pill.grid_configure(padx=(inset, inset))
            try:
                self.after(30, self._autogrow)     # width change -> line count change
            except Exception:
                pass
        except Exception:
            pass

    # ---------------- helpers ----------------
    def _linespace_real(self) -> float:
        """Real (device px) rendered line height of the input font."""
        try:
            import tkinter.font as _font
            return float(_font.Font(
                font=self.input._textbox.cget("font")).metrics("linespace"))
        except Exception:
            return 24.0

    def _wscale(self) -> float:
        """Live DPI factor (physical px per logical px) — tk reports device px."""
        try:
            s = self.winfo_fpixels("1i") / 96.0
            return s if s and s > 0 else 1.0
        except Exception:
            return 1.0

    def _init_lspace(self) -> None:
        """Logical px per text line — derived from the REAL rendered line
        height, not a constant (CTkTextbox height is configured in logical
        px and scales to real at ~1.5x on this display)."""
        self._lspace = max(8, int(round(self._linespace_real() / max(1.0, self._wscale()))))

    def _display_lines(self) -> int:
        """SHOWN text lines (tk count = newline crossings, so +1)."""
        inner = self.input._textbox
        try:
            n = inner.tk.call(inner._w, "count", "-displaylines", "1.0", "end-1c")
            return max(1, int(n) + 1)
        except Exception:
            return max(1, int(self.input.index("end-1c").split(".")[0] or 1))

    def _autogrow(self, event=None):
        self._update_send()
        if getattr(self, "_has_placeholder", False):
            return
        self._init_lspace()
        shown = self._display_lines()
        cap = MAX_LINES * self._lspace          # logical height of 9 shown lines
        self.input.configure(height=max(26, min(cap, 9 + shown * self._lspace)))
        inner = self.input._textbox
        if shown > MAX_LINES:
            inner.see("insert")                    # keep the caret/paste end visible
            self._scrolled = True
        else:
            inner.yview_moveto(0.0)
            self._scrolled = False

    def _update_send(self):
        """Aliveness cue: the arrow lights up (accent) when you typed
        something and goes quiet (dim) on empty/placeholder/busy."""
        try:
            has = bool(self.input.get("1.0", "end-1c").strip()) \
                and not getattr(self, "_has_placeholder", False)
            on = has and not self.busy
            self.send_btn.configure(
                fg_color=theme.ACCENT if on else "#22242A",
                hover_color=theme.ACCENT_LIGHT if on else "#2A2D34",
                text_color="#FFFFFF" if on else theme.TEXT_3)
        except Exception:
            pass

    def _after_idle_autogrow(self, event=None):
        """Paste/cut changes lines without a KeyRelease — recompute right after."""
        try:
            self.after(25, self._autogrow)
        except Exception:
            pass

    def _wheel(self, event):
        """Scroll the COMPOSER when hovering it and its text overflows the
        cap; otherwise let the event fall through to the chat pane."""
        if getattr(self, "_has_placeholder", False):
            return None
        overflowing = self._display_lines() > MAX_LINES
        if not overflowing and not getattr(self, "_scrolled", False):
            return None
        try:
            notches = event.delta / 120.0 if abs(event.delta) >= 120 else event.delta / 12.0
            lines = -int(round(notches))           # wheel-up -> view earlier text
            self.input._textbox.yview_scroll(lines * 10, "units")  # 10 units = 1 line
            self._scrolled = True
        except Exception:
            return None
        return "break"

    def _mic_pressed(self):
        if self.on_mic is not None:
            try:
                self.on_mic()
            except Exception:
                pass

    def set_mic_state(self, state: str):
        """normal | open (voice panel up) | recording | off (busy)."""
        try:
            m = self.mic_btn
            if state == "recording":
                m.configure(fg_color="#B94C48", hover_color="#C95854",
                            text_color="#FFFFFF", border_color="#B94C48")
            elif state == "open":
                m.configure(fg_color=theme.ACCENT, hover_color=theme.ACCENT_LIGHT,
                            text_color="#FFFFFF", border_color=theme.ACCENT)
            elif state == "off":
                m.configure(fg_color="#191B1E", hover_color="#191B1E",
                            text_color=theme.TEXT_3, border_color=theme.BORDER)
            else:
                m.configure(fg_color="#22242A", hover_color="#2A2D34",
                            text_color=theme.TEXT_2, border_color=theme.BORDER)
        except Exception:
            pass

    def _focus_in(self, event=None):
        if getattr(self, "_has_placeholder", False):
            self.input.delete("1.0", "end")
            self._has_placeholder = False
        self._update_send()

    def _focus_out(self, event=None):
        try:
            txt = self.input.get("1.0", "end-1c")
        except Exception:
            return
        if not txt.strip():
            self.input.delete("1.0", "end")
            self.input.insert("1.0", PLACEHOLDER)
            self.input.tag_add("ph", "1.0", "end-1c")
            self._has_placeholder = True
        self._update_send()

    def _clear_placeholder(self):
        if getattr(self, "_has_placeholder", False):
            self.input.delete("1.0", "end")
            self._has_placeholder = False

    def _on_return(self, event):
        if event.state & 0x0001:      # shift held -> newline
            return None
        self._do_send()
        return "break"

    def _do_send(self):
        if self.busy:
            return
        self._clear_placeholder()
        text = self.input.get("1.0", "end-1c")
        text = text.replace(PLACEHOLDER, "").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._autogrow()
        self.on_send(text)

    def set_note(self, status: str):
        """Footnote under the pill: state while busy, help line otherwise."""
        if status:
            self.note.configure(text=status)
        else:
            self.note.configure(text="Enter to send · Shift+Enter for a new line "
                                     "· runs fully on this PC")

    def set_busy(self, busy: bool, status: str = ""):
        self.busy = busy
        self.send_btn.configure(state="disabled" if busy else "normal")
        self._update_send()

    def focus_input(self):
        self.input.focus_set()

    def clear_text(self):
        self.input.delete("1.0", "end")
        self._has_placeholder = False
        self._autogrow()
