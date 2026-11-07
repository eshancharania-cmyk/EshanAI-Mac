"""Chat message area: user bubbles, streaming assistant messages, tool chips."""
from __future__ import annotations

import time
import tkinter as tk

import customtkinter as ctk

from . import theme
from .markdown import parse
from .welcome import Welcome

MAX_STREAM_CHARS = 80000       # hard cap for one streaming textbox


class ChatArea(ctk.CTkScrollableFrame):
    """Scrollable list of chat rows with ChatGPT-like layout."""

    def __init__(self, master, font_scale: float = 1.0, suggest=None, **kw):
        super().__init__(master, fg_color=theme.CHAT_BG, corner_radius=0, scrollbar_button_color=theme.BORDER,
                         scrollbar_button_hover_color=theme.TEXT_3, **kw)
        self._scale = font_scale
        self._suggest = suggest
        self._rows = []                       # widget rows in order
        self._row_kinds = []                  # parallel: kind per row (placement)
        self._wrappable = []                  # labels needing wraplength updates
        self._last_wheel = 0.0
        self._empty_hint = None
        self._stream_box = None               # current streaming assistant textbox
        self._assistant_boxes = []            # assistant text blocks (height-fitted)
        self._pending_chip = None             # (frame, dot, label) awaiting result
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self._build_empty_hint()

    # ---------------- infrastructure ----------------
    def _wscale(self) -> float:
        """Widget scale (real DPI px per logical px) for plain-tk px sizes."""
        try:
            s = self.winfo_fpixels("1i") / 96.0
            return s if s and s > 0 else 1.0
        except Exception:
            return 1.0

    def _chip_wrap(self) -> int:
        """Wraplength in REAL px for the plain-tk chip label."""
        ws = self._wscale()
        return max(240, int(min(self._wrap_width(), 760 * ws) - 160 * ws))

    def _user_wrap(self) -> int:
        """Wraplength in REAL px for the plain-tk user message label."""
        ws = self._wscale()
        return max(240, int(min(self._wrap_width(), 720 * ws) - 60 * ws))

    def _on_wheel(self, event):
        self._last_wheel = time.time()

    def _fs(self, size: float) -> float:
        return max(9.0, size * self._scale)

    def _build_empty_hint(self):
        """Welcome panel for an empty conversation (friendly greeting +
        clickable starters). Built but NOT gridded — show_empty_hint grids it."""
        self._empty_hint = Welcome(self, on_send=self._suggest,
                                   font_scale=self._scale)

    def _forget_empty_hint(self):
        if self._empty_hint is not None:
            try:
                self._empty_hint.destroy()
            except Exception:
                pass
            self._empty_hint = None

    def show_empty_hint(self, text: str = ""):
        if self._empty_hint is None:
            self._build_empty_hint()
        t = self._empty_hint
        # placed directly (NOT registered in _rows) — it is transient state,
        # destroyed on the first message that arrives
        t.grid(row=self._next_row(), column=0, columnspan=2, sticky="nsew",
               padx=(self._inset(), self._inset()), pady=(30, 20))

    def _next_row(self) -> int:
        return len(self._rows)

    # ------------------------------------------------ ChatGPT centered column
    GPT_COL = 768                     # max content column width (LOGICAL px)

    def _available(self) -> int:
        return max(360, self.winfo_width() or 900)

    def _col_real(self, avail: int | None = None) -> int:
        """Content column width in PHYSICAL (tk device) px. Tk on a DPI-aware
        build reports winfo sizes in device px, so the logical cap must be
        scaled with the live DPI factor — mixing the two clipped user pills
        (wraplength computed real, cell computed logical)."""
        avail = avail or self._available()
        ws = self._wscale()
        return max(240, int(min(avail - 36 * ws, self.GPT_COL * ws)))

    def _inset(self, avail: int | None = None) -> int:
        """Horizontal distance from window edge to the centered column (physical px)."""
        avail = avail or self._available()
        return max((avail - self._col_real(avail)) // 2, 20)

    def _wrap_width(self) -> int:
        """Text wrap width inside the column (physical px)."""
        avail = self._available()
        ws = self._wscale()
        return max(320, int(min(avail - 90 * ws, (self.GPT_COL - 60) * ws)))

    def _grid_new(self, widget, kind: str, pady=0, sticky=None):
        """First-time placement of a row; later reflows reuse kind insets."""
        i = self._next_row()
        self._rows.append(widget)
        self._row_kinds.append(kind)
        sticky = sticky or self._sticky_for(kind)
        widget.grid(row=i, column=0, columnspan=2, sticky=sticky,
                    padx=(self._inset(), self._inset()), pady=pady)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        return i

    def _sticky_for(self, kind: str) -> str:
        return {"user": "e", "assistant": "we", "chip": "w",
                "separator": "ew", "status": "ew", "hint": "nsew"}.get(kind, "we")

    def on_resize(self):
        """Re-grid all rows with fresh insets, then refresh wraplengths."""
        avail = self._available()
        inset = self._inset(avail)
        hint = self._empty_hint
        if hint is not None and hint.winfo_manager():
            try:
                hint.grid_configure(padx=(inset, inset))
            except Exception:
                pass
        for i, (w, kind) in enumerate(zip(list(self._rows), list(self._row_kinds))):
            try:
                if kind == "separator":          # full-area hairline, 3 cm gaps
                    gap = self._sep_gap(avail)
                    w.grid_configure(padx=(gap, gap), sticky="ew")
                else:
                    w.grid_configure(padx=(inset, inset), sticky=self._sticky_for(kind))
            except Exception:
                pass
        wl = self._wrap_width()
        ws = self._wscale()
        for row in self._wrappable:
            lbl, pad = row[0], row[1]
            real = row[2] if len(row) > 2 else False
            try:
                if real:
                    # pad is logical margin (60 user / 160 chip) — convert
                    px = max(220, min(wl, int(720 * ws)) - int(pad * ws))
                else:
                    px = max(220, min(wl, 1200))      # status line: column width
                lbl.configure(wraplength=int(px))
            except Exception:
                pass
        for box in list(self._assistant_boxes):
            try:
                box.refit_width(self._wrap_width())
            except Exception:
                pass

    def _after_add(self, stick: bool = True):
        self.update_idletasks()
        if stick and (time.time() - self._last_wheel > 1.2):
            try:
                self._parent_canvas.yview_moveto(1.0)
            except Exception:
                try:
                    self.yview_moveto(1.0)
                except Exception:
                    pass

    def clear(self):
        for r in self._rows:
            try:
                r.destroy()
            except Exception:
                pass
        self._rows.clear()
        self._row_kinds.clear()
        self._wrappable.clear()
        self._assistant_boxes.clear()
        self._stream_box = None
        self._pending_chip = None
        if self._empty_hint is None:
            self._build_empty_hint()

    # ---------------- messages ----------------
    def _sep_gap(self, avail: int | None = None) -> int:
        """Separator side gap = 3 cm (user spec), in tk device px — winfo_fpixels
        is DPI-aware, so multiplying by 3 cm in inches is exact on screen."""
        avail = avail or self._available()
        try:
            gap = self.winfo_fpixels("1i") * 1.18110      # 3 cm in inches
        except Exception:
            gap = 113
        return max(16, int(min(gap, (avail - 120) // 2)))

    def add_separator(self):
        """Thin, subtly darker hairline that closes a turn: 'my message,
        return message, then the line'. Runs across the chat area with a
        3 cm gap on either side."""
        line = tk.Frame(self, height=2, bg=theme.SEPARATOR,
                        bd=0, highlightthickness=0)
        self._grid_new(line, "separator", pady=(3, 5))
        self._after_add(stick=False)

    def add_user_message(self, text: str):
        self._forget_empty_hint()
        if self._rows:                    # close the previous turn with the hairline
            self.add_separator()
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        self._grid_new(wrap, "user", pady=(8, 2))
        bubble = ctk.CTkFrame(wrap, fg_color=theme.BUBBLE_USER, corner_radius=theme.BUBBLE_RADIUS)
        bubble.pack(anchor="e")
        # PLAIN tk label inside the CTk pill: CTkLabel's canvas geometry
        # lags/oscilates with long text (left+right center-clip; see chip case).
        lbl = tk.Label(bubble, text=text or " ", justify="left", anchor="w",
                       bg=theme.BUBBLE_USER, fg=theme.TEXT,
                       font=(theme.FONT_FAMILY, int(round(self._fs(12)))),
                       wraplength=self._user_wrap())
        lbl.pack(padx=16, pady=10, anchor="w")
        self._wrappable.append((lbl, 60, True))
        self._after_add()

    def begin_assistant_message(self) -> None:
        """Start a fresh assistant block — ChatGPT style: PLAIN TEXT on the
        page background (no bubble, fixed full-column width so nothing
        oscillates), growing TALLER live as the reply streams."""
        self._forget_empty_hint()
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        self._grid_new(wrap, "assistant", pady=(10, 2))
        box = _MarkdownStream(wrap, self._fs(12), self._wrappable)
        box.frame.pack(anchor="w", fill="x")
        self._assistant_boxes.append(box)
        self._pending_chip = None
        self._stream_box = box
        self._after_add()

    def append_stream(self, text: str):
        if self._stream_box is not None:
            self._stream_box.append(text)
            self._after_add()

    def finalize_assistant(self, fallback_text: str = ""):
        if self._stream_box is not None:
            self._stream_box.render_markdown(fallback_text)
            self._stream_box = None
            self._after_add()

    def add_tool_chip(self, tool: str, args_preview: str, ok: bool | None, result_text: str = ""):
        """One line per action. ok=None -> pending amber dot; result updates in place
        via resolve_pending_chip, or can be passed directly (history rendering)."""
        dot_color = {True: theme.OK, False: theme.ERR, None: theme.WARN}[ok]
        frame = ctk.CTkFrame(self, fg_color=theme.EVENT_BG, corner_radius=10,
                             border_width=0)
        self._grid_new(frame, "chip", pady=(2, 1))
        dot = tk.Canvas(frame, width=8, height=8, highlightthickness=0, bg=theme.EVENT_BG)
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")
        dot.pack(side="left", padx=(12, 7), pady=5)
        if ok is None:
            label = f"{tool}  {args_preview}".strip()
        else:
            label = _chip_label(tool, ok, result_text)
        # PLAIN tk label: auto-sizes its own width exactly (the CTk label's
        # canvas geometry lags/oscilates and center-clips mono text).
        lbl = tk.Label(frame, text=label, bg=theme.EVENT_BG, fg=theme.TEXT_2,
                       justify="left", anchor="w",
                       font=(theme.MONO_FAMILY, int(round(self._fs(10.5)))),
                       wraplength=self._chip_wrap())
        lbl.pack(side="left", pady=4, padx=(0, 12))
        self._wrappable.append((lbl, 40 + 120, True))
        self._after_add()
        if ok is None:
            self._pending_chip = (frame, dot, lbl, tool)

    def resolve_pending_chip(self, ok: bool, result_text: str = "") -> bool:
        """Turn the amber pending dot of the last tool chip green/red in place."""
        chip = self._pending_chip
        if chip is None:
            return False
        self._pending_chip = None
        frame, dot, lbl, tool = chip
        try:
            dot.itemconfig(1, fill=(theme.OK if ok else theme.ERR))
            lbl.configure(text=_chip_label(tool, ok, result_text))
            return True
        except Exception:
            return False

    def add_status_chip(self, text: str, kind: str = "info"):
        color = {"info": theme.TEXT_3, "ok": theme.OK, "warn": theme.WARN, "err": theme.ERR}[kind]
        frame = ctk.CTkFrame(self, fg_color="transparent")
        self._grid_new(frame, "status", pady=(6, 2))
        lbl = ctk.CTkLabel(frame, text=text, font=theme.f(self._fs(10.5)), text_color=color,
                           justify="center")
        lbl.pack(anchor="center")
        self._wrappable.append((lbl, 900, False))
        self._after_add()


def _chip_label(tool: str, ok: bool, result_text: str = "") -> str:
    summary = " ; ".join((result_text or "").strip().splitlines()[:2])[:150]
    mark = "✓" if ok else "⚠"
    return f"{tool}  {mark} {summary}" if summary else f"{tool}  {mark}"


class _MarkdownStream:
    """Streaming assistant text — ChatGPT style: full-column-width text on
    the page background (NO bubble, NO width fitting), on completion re-renders
    as markdown. Only the HEIGHT is fitted (cheap, stable, no oscillation)."""

    def __init__(self, parent, base_size: float, wrappable_list):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.base_size = max(9, int(round(base_size)))   # SAME size as user text
        self.tb = ctk.CTkTextbox(
            self.frame, fg_color=theme.CHAT_BG, corner_radius=0,
            border_width=0, wrap="word", font=(theme.FONT_FAMILY, self.base_size),
            text_color=theme.TEXT, height=20,
            scrollbar_button_color=theme.CHAT_BG,          # scrollbar invisible
            scrollbar_button_hover_color=theme.CHAT_BG)
        self.tb.pack(anchor="w", fill="x", padx=12, pady=(10, 6))
        # CTk negativizes tuple font sizes (-N = PIXELS on a DPI-aware build,
        # ~2/3 of the points size the user pill renders with). Set the REAL
        # positive (points) font directly on the inner tk.Text so assistant
        # text is exactly the user text's size.
        try:
            self.tb._textbox.configure(font=(theme.FONT_FAMILY, self.base_size))
        except Exception:
            pass
        self._raw: list[str] = []
        self._chars = 0
        import tkinter.font as _tkfont
        try:
            self._lspace = _tkfont.Font(
                font=self.tb._textbox.cget("font")).metrics("linespace")
        except Exception:
            self._lspace = 17
        self._configure_tags()
        self._wrappable_list = wrappable_list
        self._last_fit = 0.0
        self._MAX_H = 19000                   # probe/height ceiling for enormous messages

    # ------------------------------------------------------------ height fit
    def _content_px(self) -> int:
        """Total pixel height of the rendered text, independent of widget
        visibility. Tk's dlineinfo/count only describe lines inside the
        widget's current height, so we probe tall first, then read the box
        of the LAST display line (its bottom == full content height)."""
        inner = self.tb._textbox
        probe = 2400                            # ~96 lines at 25px
        for _ in range(4):                      # covers up to ~19,000px
            try:
                self.tb.configure(height=probe)
            except Exception:
                return 400
            try:
                inner.update_idletasks()
            except Exception:
                pass
            d = inner.dlineinfo("end-1c")
            if d is not None:
                d1 = inner.dlineinfo("1.0")
                if d1 is not None and int(d1[3]) != self._lspace:
                    self._lspace = int(d1[3])   # real scaled linespace
                return int(d[1] + d[3])
            probe *= 2
        return probe                            # enormous: show everything

    def _fit_height(self, force: bool = False):
        """Resize the textbox to its content so the message grows live, like
        ChatGPT's. No assumed math: the content height is measured EXACTLY
        with Tk's count(..., "ypixels") (includes wraps + markdown block
        spacing — dlineinfo/displayline estimates under-measure and clip the
        last line), then the configured height is corrected using the
        widget's OWN observed configured->real-px ratio. CTk's scaling is
        machine-dependent, so it is measured live and iterated — never
        estimated from monitor DPI."""
        now = time.monotonic()
        if not force and (now - self._last_fit) < 0.05:
            return              # throttled — the next streaming chunk refits again
        try:
            inner = self.tb._textbox
            px = None
            try:
                got = inner.count("1.0", "end", "ypixels")
                if isinstance(got, tuple):
                    got = got[0] if got else None
                if got:
                    px = int(got)
            except Exception:
                px = None
            if not px:
                px = self._content_px()          # degenerate-measure fallback
            px = min(max(px + 8, 30), self._MAX_H)   # +8 safety below the last line
            # Shape via the widget's observed ratio, not any assumed scale.
            for _ in range(4):
                try:
                    inner.update_idletasks()
                    real = float(inner.winfo_height())
                    cfg = float(self.tb.cget("height"))
                except Exception:
                    break
                if real <= 0 or cfg <= 0:
                    break
                if real >= px:
                    break                        # all content visible — done
                if real < 10:
                    # not laid out yet (no geometry pass): a neutral guess
                    # never clips; the next call/resize corrects it
                    cfg = int(px)
                else:
                    map_k = max(0.4, min(real / cfg, 4.0))   # observed ratio
                    cfg = int(round(px / map_k))
                try:
                    self.tb.configure(height=max(30, int(min(cfg, self._MAX_H))))
                except Exception:
                    break
            self._last_fit = now
        except Exception:
            pass

    # ---------------------------------------------------------- resize hooks
    def refit_width(self, max_width: int | None = None) -> None:
        """Window resized: the text WIDTH always follows the grid column
        (fill="x" — no width fitting at all), so only the height needs a
        refit. max_width is accepted but ignored (call site unchanged)."""
        try:
            self._fit_height(force=True)
        except Exception:
            pass

    def _configure_tags(self):
        """Markdown tag styles. Configured on the INNER tk.Text directly:
        CTkTextbox.tag_config FORBIDS 'font' (raises AttributeError — the old
        try/except silently dropped ALL markdown fonts: no bold/italics/
        headings ever rendered), while the inner tk.Text accepts fonts with
        positive point sizes (identical to the user pill's rendering)."""
        s = self.base_size
        conf = {
            "": {"foreground": theme.TEXT},
            "b": {"font": (theme.FONT_FAMILY, s, "bold")},
            "i": {"font": (theme.FONT_FAMILY, s, "italic")},
            "bi": {"font": (theme.FONT_FAMILY, s, "bold italic")},
            "code": {"font": (theme.MONO_FAMILY, s - 1), "foreground": theme.ACCENT_LIGHT},
            "codeblock": {"font": (theme.MONO_FAMILY, s - 1), "foreground": theme.TEXT_2,
                          "background": theme.CODE_BG, "spacing1": 3, "spacing3": 3},
            "link": {"foreground": theme.ACCENT_LIGHT, "underline": True},
            "bullet": {"foreground": theme.ACCENT_LIGHT},
            "quote": {"foreground": theme.TEXT_2, "font": (theme.FONT_FAMILY, s, "italic")},
            "heading1": {"font": (theme.FONT_FAMILY, s + 4, "bold"), "spacing1": 8, "spacing3": 3},
            "heading2": {"font": (theme.FONT_FAMILY, s + 3, "bold"), "spacing1": 6, "spacing3": 2},
            "heading3": {"font": (theme.FONT_FAMILY, s + 1, "bold"), "spacing1": 5, "spacing3": 2},
            "rule": {"foreground": theme.BORDER, "spacing1": 6, "spacing3": 6},
        }
        try:
            inner = self.tb._textbox
        except Exception:
            inner = None
        for name, attrs in conf.items():
            try:
                if inner is not None:
                    inner.tag_config(name, **attrs)     # plain tk: fonts allowed
                else:
                    self.tb.tag_config(
                        name, **{k: v for k, v in attrs.items() if k != "font"})
            except Exception:
                pass

    def append(self, text: str):
        self._raw.append(text)
        # streaming: plain text, always end at the bottom
        self.tb.configure(state="normal")
        self.tb.insert("end", text)
        self._chars += len(text)
        lines = int(self.tb.index("end-1c").split(".")[0])
        if self._chars > MAX_STREAM_CHARS:
            self.tb.delete("1.0", f"1.0+{self._chars - MAX_STREAM_CHARS}c")   # drop oldest text
            self._chars = MAX_STREAM_CHARS
        elif lines > 400:
            self.tb.delete("1.0", f"{lines - 400}.0")
        self.tb.see("end")
        self.tb.configure(state="disabled")
        self._fit_height()

    def render_markdown(self, fallback_text: str = ""):
        """Replace streamed plain text with final markdown rendering."""
        full = "".join(self._raw) or fallback_text
        self._raw.clear()
        self.tb.configure(state="normal")
        self.tb.delete("1.0", "end")
        blocks = parse(full)
        first = True
        prev_is_list = False
        for blk in blocks:
            if not first:
                is_list = blk.style in ("bullet", "number")
                self.tb.insert("end", "\n" if (prev_is_list and is_list) else "\n\n")
                prev_is_list = is_list
            else:
                first = False
                prev_is_list = blk.style in ("bullet", "number")
            style = blk.style
            if style == "rule":
                self.tb.insert("end", "─" * 46, "rule")
                continue
            if style == "code":
                self.tb.insert("end", "\n" + blk.segments[0][0] + "\n", "codeblock")
                continue
            # marker styles live on the segments themselves (the `bullet` tag
            # would otherwise tint the WHOLE line); only real block styles
            # (headings, quote) apply at block level.
            tag = "" if style in ("text", "bullet", "number") else style
            for seg_text, seg_tag in blk.segments:
                tags = []
                if tag:
                    tags.append(tag)
                if seg_tag:
                    tags.append(seg_tag)
                self.tb.insert("end", seg_text, tuple(tags) if tags else None)
        if not blocks:
            self.tb.insert("end", full.strip() or " ")
        self.tb.tag_config("sel", background=theme.ACCENT)
        self.tb.see("1.0")
        self.tb.configure(state="disabled")
        self._fit_height(force=True)
