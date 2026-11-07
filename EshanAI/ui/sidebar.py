"""Left sidebar: brand, new chat, search, conversation list, utilities."""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import theme


class Sidebar(ctk.CTkFrame):
    def __init__(self, master, callbacks: dict, font_scale: float = 1.0,
                 width: int = 300, **kw):
        """callbacks: on_new_chat, on_select(conv_id), on_rename(conv_id),
        on_delete(conv_id), on_search(query), on_settings, on_memory"""
        super().__init__(master, width=width, corner_radius=0,
                         fg_color=theme.SIDEBAR_BG, **kw)
        self.cb = callbacks
        self._scale = font_scale
        self._rows: dict[int, dict] = {}       # conv_id -> {frame, label}
        self._selected: int | None = None
        self._search_after = None
        self.grid_propagate(False)

        # ---------------- brand ----------------
        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(brand, text="◍  EshanAI", font=theme.f(self._fs(14.5), "bold"),
                     text_color=theme.TEXT).pack(anchor="w")

        # ---------------- new chat ----------------
        ctk.CTkButton(self, text="＋  New chat", height=36, corner_radius=10,
                      fg_color="#22242A", hover_color="#2B2E36",
                      font=theme.f(self._fs(12), "bold"), text_color=theme.TEXT,
                      border_width=0,
                      command=lambda: self.cb["on_new_chat"]()).pack(
            fill="x", padx=14, pady=(4, 8))

        # ---------------- search ----------------
        self.search = ctk.CTkEntry(self, placeholder_text="Search conversations…",
                                   height=30, corner_radius=8,
                                   fg_color="#15151A", border_color="#26282C",
                                   font=theme.f(self._fs(11)), text_color=theme.TEXT,
                                   placeholder_text_color=theme.TEXT_3)
        self.search.pack(fill="x", padx=14, pady=(0, 8))
        self.search.bind("<KeyRelease>", self._on_search_key)

        # ---------------- conversation list ----------------
        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.TEXT_3)
        self.list_frame.pack(fill="both", expand=True, padx=10, pady=(2, 6))

        # ---------------- bottom utilities ----------------
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(6, 8))
        ctk.CTkButton(bottom, text="⚙  Settings", height=28, corner_radius=8,
                      fg_color="transparent", border_width=0,
                      hover_color="#1B1D21", font=theme.f(self._fs(11.5)),
                      text_color=theme.TEXT_2,
                      command=lambda: self.cb["on_settings"]()).pack(
            side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(bottom, text="MEM", height=28, width=46, corner_radius=8,
                      fg_color="transparent", border_width=0,
                      hover_color="#1B1D21", font=theme.f(self._fs(11.5), "bold"),
                      text_color=theme.TEXT_2,
                      command=lambda: self.cb["on_memory"]()).pack(side="right")

        # ---------------- ollama status chip ----------------
        chip_row = ctk.CTkFrame(self, fg_color="transparent", height=22)
        chip_row.pack(fill="x", padx=18, pady=(0, 8))
        self._dot = tk.Canvas(chip_row, width=10, height=10, highlightthickness=0,
                              bg=theme.SIDEBAR_BG)
        self._dot_item = self._dot.create_oval(1, 1, 9, 9, fill=theme.TEXT_3, outline="")
        self._dot.pack(side="left", padx=(0, 8))
        self.chip = ctk.CTkLabel(chip_row, text="", font=theme.f(self._fs(10.5)),
                                 text_color=theme.TEXT_3, anchor="w")
        self.chip.pack(side="left", fill="x", expand=True)

    # ---------------------------------------------------------------- helpers
    def _fs(self, size: float) -> float:
        return max(9.0, size * self._scale)

    def set_chip(self, text: str, ok: bool | None = None):
        """ok=True green dot (ollama up), False red, None gray."""
        self.chip.configure(text=text)
        color = {True: theme.OK, False: theme.ERR, None: theme.TEXT_3}[ok]
        try:
            self._dot.itemconfig(self._dot_item, fill=color)
        except Exception:
            pass

    # ---------------------------------------------------------------- list
    def refresh(self, conversations: list[dict], selected: int | None = None):
        if selected is not None:
            self._selected = selected
        for r in self.list_frame.winfo_children():
            r.destroy()
        self._rows.clear()
        for conv in conversations:
            self._add_row(conv)
        if self._selected is not None and self._selected in self._rows:
            self._style_row(self._selected, True)

    def _add_row(self, conv: dict):
        cid = conv["id"]
        frame = ctk.CTkFrame(self.list_frame, fg_color="transparent", corner_radius=8)
        frame.pack(fill="x", pady=2)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=0)

        width = self.winfo_width()
        wrap = int(width - 70) if width > 100 else 200
        lbl = ctk.CTkLabel(frame, text=conv.get("title") or "New conversation",
                           anchor="w", justify="left", font=theme.f(self._fs(11.5)),
                           text_color=theme.TEXT if conv["id"] == self._selected
                           else theme.TEXT_2,
                           wraplength=wrap, height=30, cursor="hand2")
        lbl.grid(row=0, column=0, sticky="ew", padx=(10, 2), pady=5)

        menu_btn = ctk.CTkLabel(frame, text="⋯", width=22, font=theme.f(self._fs(13), "bold"),
                                text_color=theme.TEXT_3, cursor="hand2")
        menu_btn.grid(row=0, column=1, padx=(2, 8))

        for w in (frame, lbl):
            w.bind("<Button-1>", lambda e, c=cid: self._select(c))
            w.bind("<Enter>", lambda e, f=frame: self._hover(f, True))
            w.bind("<Leave>", lambda e, f=frame: self._hover(f, False))
        menu_btn.bind("<Button-1>", lambda e, c=cid, wb=menu_btn: self._menu(c, wb))
        menu_btn.bind("<Button-3>", lambda e, c=cid, wb=menu_btn: self._menu(c, wb))
        frame.bind("<Button-3>", lambda e, c=cid, wb=lbl: self._menu(c, wb))

        self._rows[cid] = {"frame": frame, "label": lbl, "menu_btn": menu_btn}

    def _hover(self, frame, on_off):
        if self._rows.get(self._selected, {}).get("frame") is frame:
            return
        try:
            frame.configure(fg_color="#1B1D21" if on_off else "transparent")
        except Exception:
            pass

    def _select(self, conv_id):
        self._selected = conv_id
        for cid, row in self._rows.items():
            self._style_row(cid, cid == conv_id)
        self.cb["on_select"](conv_id)

    def _style_row(self, conv_id, on_off):
        row = self._rows.get(conv_id)
        if not row:
            return
        try:
            row["frame"].configure(fg_color="#232529" if on_off else "transparent")
            row["label"].configure(text_color=theme.TEXT if on_off else theme.TEXT_2)
        except Exception:
            pass

    def update_title(self, conv_id, title):
        row = self._rows.get(conv_id)
        if row:
            try:
                row["label"].configure(text=title)
            except Exception:
                pass

    def selected(self) -> int | None:
        return self._selected

    # ---------------------------------------------------------------- menu
    def _menu(self, conv_id, widget):
        menu = tk.Menu(widget, tearoff=0, bg="#111B29", fg="#F1F5F9",
                       activebackground=theme.BORDER, activeforeground="#F1F5F9",
                       font=(theme.FONT_FAMILY, 10))
        menu.add_command(label="Rename", command=lambda: self.cb["on_rename"](conv_id))
        menu.add_separator()
        menu.add_command(label="Delete", command=lambda: self.cb["on_delete"](conv_id))
        try:
            menu.tk_popup(widget.winfo_rootx() + int(widget.winfo_width()),
                          widget.winfo_rooty() + widget.winfo_height() + 2)
        finally:
            menu.grab_release()

    # ---------------------------------------------------------------- search
    def _on_search_key(self, event=None):
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except Exception:
                pass
        self._search_after = self.after(250, self._run_search)

    def _run_search(self):
        self._search_after = None
        self.cb["on_search"](self.search.get().strip())

    def clear_search(self):
        try:
            self.search.delete(0, "end")
        except Exception:
            pass
