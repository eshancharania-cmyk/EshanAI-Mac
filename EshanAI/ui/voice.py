"""Voice mode panel — the companion face (v2: eye-only reference design).

Design (user spec 5 Nov 2026 + reference images):
  * a mic button (in a bubble, beside the send arrow) opens this panel
  * the panel FILLS THE WHOLE CHAT PANE with its dark grey card — the
    eyes draw directly on the chat, nothing small and floating
  * EYES ONLY — no mouth. Each eye is an ORGANIC per-corner rounded
    shape (not a plain rectangle) sitting on a REAL soft glow — a
    pre-blurred halo image fading into the card (PIL Gaussian, cached),
    small and friendly, not a big bloom:
      neutral  pleasant blue, straight, no tilt          (ref 1)
      sad      dimmer "sadder" blue, drooping outward, big
               scoop bottoms  (ref 2)
      happy    pleasant yellowish, wide squircle, bright glow  (ref 3)
      angry    plain RED, tilted inward-down, tighter corners
  * the face wears the variant matching the TOPIC'S mood (local lexicon)
  * push-to-talk while HOLDING SPACE; "listening" pops at the bottom in
    a slightly lighter shade; eyes gently pulse while hearing you
  * blinking is PHASED: a smooth close-open envelope (open → easing
    shut → closed beat → easing open → open), never a snap between two
    squircles; new blinks land 5–15 s apart, randomly
  * exit is NOT a ✕ button: the top of the card says "press CTRL+S to
    exit" and CTRL+S closes voice mode (ESC kept as a quiet fallback)
  * pop-in bounce on open, thinking drift, per-variant idle
    "looking around" choreographies

Everything is drawn on one tkinter Canvas plus cached glow images — no
webview, fully offline.
"""
from __future__ import annotations

import math
import random
import time
import tkinter as tk

from . import theme

# ------------------------------------------------- palette (design spec)
BUBBLE_BG = "#1B1D21"        # dark grey face card (fills the chat pane)
BUBBLE_BORDER = "#2C2F35"
EYE_BLUE = "#4C83C3"         # pleasant blue — the default face (ref 1)
EYE_YELLOW = "#E7C34F"       # pleasant yellowish — happy (ref 3)
EYE_SAD = "#3F63A0"          # a sadder, dimmer shade of blue
EYE_RED = "#E0483E"          # plain red for angry — NOT dark red
LISTEN_INK = "#3A3E46"       # "slightly lighter than the background"
TOP_INK = "#4A4E57"          # the CTRL+S exit line
HINT_INK = "#4A4E57"

# per-mood eye recipe: width, height, tilt(deg), corners, colour
# corners = (top_outer, top_inner, bottom_inner, bottom_outer) as FRACTIONS
# of eye height — unequal corners give the organic shapes from the refs:
# NOT uniform rounded rectangles. tilt: +deg = the eye's RIGHT end tips
# DOWN (canvas y runs downward); sad = outer end down (droop away),
# angry = inner end down (furrow in).
EYEBALL = {
    "neutral": (100, 140, 0.0, (0.50, 0.34, 0.30, 0.34), EYE_BLUE),
    "happy":   (132, 118, 0.0, (0.34, 0.34, 0.34, 0.34), EYE_YELLOW),
    "sad":     (96, 126, 13.0, (0.26, 0.20, 0.46, 0.52), EYE_SAD),
    "angry":   (118, 104, -11.0, (0.22, 0.22, 0.24, 0.22), EYE_RED),
}

GLOW_BLUR = 0.18             # halo feather = fraction of the eye height
GLOW_PUFF = 0.38             # halo extent = fraction of the eye height
BLINK_DUR = 0.26             # full phased blink: close-open envelope
POP_TIME = 0.42
CLOSE_TIME = 0.22


def _ease_out_back(t: float) -> float:
    "Standard overshooting ease — the pop bounce."
    c1, c3 = 1.70158, 2.70158
    t = max(0.0, min(1.0, t))
    return 1.0 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class VoicePanel(tk.Frame):
    "Full-pane overlay rendering the eye-face; owns the SPACE capture."

    def __init__(self, master, callbacks: dict, font_scale: float = 1.0):
        super().__init__(master, bg=BUBBLE_BG, bd=0, highlightthickness=0)
        self._cb = callbacks
        self._scale = max(0.5, float(font_scale))

        self._mood = "neutral"
        self._phase = "pop"                      # pop | open | closing | gone
        self._phase_t0 = time.monotonic()
        self._morph_until = 0.0
        self._face_state = "idle"                # idle | listening | thinking
        self._capture = False
        self._capture_t0 = 0.0
        self._speaking = False
        self._idle_t0 = time.monotonic()         # idle look-around grace timer
        self._k = 0.55                           # face scale, animated

        self._blink_t0 = None                      # phased blink start
        self._next_blink = time.monotonic() + random.uniform(5.0, 15.0)
        self._glow_master = {}                     # PIL halo per (mood, side)
        self._glow_photos = {}                     # scaled PhotoImage cache

        self.cv = tk.Canvas(self, bg=BUBBLE_BG, bd=0, highlightthickness=0)
        self.cv.place(in_=self, x=0, y=0, relwidth=1, relheight=1)

        # global bindings while the face is up
        self._space_down = False
        self.bind_all("<KeyPress-space>", self._space_press, add="+")
        self.bind_all("<KeyRelease-space>", self._space_release, add="+")
        self.bind_all("<Control-s>", self._ctrl_s, add="+")
        self.bind_all("<Escape>", self._esc_all, add="+")

        self._alive = True
        self._tick_job = self.after(33, self._tick)

    # ------------------------------------------------------------ lifecycle
    def is_open(self) -> bool:
        return self._alive

    def begin_close(self):
        "Release bindings, play the shrink, then the app destroys us."
        if self._phase in ("closing", "gone"):
            return
        if self._capture:
            self._capture = False
            self._face_state = "idle"
        try:
            self.unbind_all("<KeyPress-space>")
            self.unbind_all("<KeyRelease-space>")
            self.unbind_all("<Control-s>")
            self.unbind_all("<Escape>")
        except Exception:
            pass
        self._phase = "closing"
        self._phase_t0 = time.monotonic()

    def finished_closing(self) -> bool:
        return self._phase == "gone"

    def destroy(self):
        self._alive = False
        try:
            self.unbind_all("<KeyPress-space>")
            self.unbind_all("<KeyRelease-space>")
            self.unbind_all("<Control-s>")
            self.unbind_all("<Escape>")
        except Exception:
            pass
        tk.Frame.destroy(self)

    # ------------------------------------------------------------ input
    def _space_press(self, event):
        if not self._alive or self._phase != "open":
            return
        if not self._space_down:
            self._space_down = True
            self._cb["on_capture_start"]()

    def _space_release(self, event):
        if not self._alive:
            return
        if self._space_down:
            self._space_down = False
            self._cb["on_capture_end"]()

    def _ctrl_s(self, event):
        if self._alive and self._phase in ("pop", "open"):
            self._cb["on_close"]()
            return "break"
        return None

    def _esc_all(self, event):
        "Quiet fallback — not advertised, but still gets you out."
        if self._alive and self._phase in ("pop", "open"):
            self._cb["on_close"]()
            return "break"
        return None

    # ------------------------------------------------------------ states
    def set_mood(self, mood: str):
        if mood in ("neutral", "happy", "sad", "angry"):
            self._mood = mood
            self._morph_until = time.monotonic() + 0.19
            self._idle_t0 = time.monotonic() + 2.5   # regrace on new mood

    def set_capture(self, on: bool):
        "True while the mic is recording."
        self._capture = bool(on)
        if on:
            self._capture_t0 = time.monotonic()
            self._face_state = "listening"
        elif self._face_state == "listening":
            self._face_state = "idle"

    def set_thinking(self):
        self._face_state = "thinking"
        self._idle_t0 = time.monotonic() + 0.4

    def set_listening(self):
        self._face_state = "listening"
        self.set_capture(True)

    def set_idle(self):
        self._face_state = "idle"
        self._capture = False
        self._idle_t0 = time.monotonic()

    def set_speaking(self, speaking: bool):
        self._speaking = bool(speaking)

    # ------------------------------------------------------------ engine
    def _tick(self):
        if not self._alive:
            return
        try:
            self._render()
        except Exception:
            pass
        self._tick_job = self.after(33, self._tick)

    # -------- idle choreography (per variant, shared by NO two) ----------
    def _idle_offset(self, t: float):
        """'Looking around' after 2.5 s idle — a continuous choreography of
        the elapsed idle time; returns (dx, dy) in u-units."""
        m = self._mood
        if m == "neutral":
            x = 22 * math.sin(t / 1.9)              # slow curious pan
            y = 3 * math.sin(t / 2.7)
        elif m == "happy":
            x = 9 * math.sin(t / 1.1)               # light bouncing sway
            y = -5 * abs(math.sin(t * math.pi / 0.9))
        elif m == "sad":
            x = 6 * math.sin(t / 2.4)               # slow, tired drift down
            y = 8 + 4 * math.sin(t / 3.1)
        else:                                       # angry: fast darts
            x = 17 * math.sin(2 * math.pi * t * 1.35)
            y = 2 * math.sin(2 * math.pi * t * 2.7)
        return x, y

    # -------- painting ---------------------------------------------------
    def _render(self):
        cv = self.cv
        now = time.monotonic()
        W = max(240, cv.winfo_width())
        H = max(240, cv.winfo_height())
        u = min(W, H) / 500.0          # the face's unit of measure (px)

        # ---- face scale: pop bounce, morph dip, close shrink ----
        if self._phase == "pop":
            f = (now - self._phase_t0) / POP_TIME
            self._k = 0.55 + 0.45 * max(_ease_out_back(f), 0.0)
            if f >= 1.0:
                self._phase = "open"
                self._k = 1.0
        elif self._phase == "closing":
            f = (now - self._phase_t0) / CLOSE_TIME
            self._k = max(0.02, 1.0 - _ease_out(f))
            if f >= 1.0:
                self._phase = "gone"
                cv.delete("all")
                return
        elif self._phase == "open" and self._speaking:
            self._k = 1.0 + 0.014 * math.sin(2 * math.pi * now * 2.2)  # breathe
        else:
            self._k = 1.0

        k = self._k
        if self._phase == "open" and now < self._morph_until:
            f = (self._morph_until - now) / 0.19
            k *= 1.0 - 0.09 * f                   # dip when swapping mood

        cv.delete("all")
        # the card fills the ENTIRE chat pane (user: "fit the whole chat place")
        cv.create_rectangle(1, 1, W - 1, H - 1, fill=BUBBLE_BG,
                            outline=BUBBLE_BORDER, width=2)
        if self._phase == "closing":
            return

        cx = W / 2
        cy = H * 0.40                             # eyes sit a little high

        # ---- state offsets & dynamics ----
        dx = dy = 0.0
        pulse = 1.0
        if self._face_state == "thinking":
            dx, dy = -15 * u, -22 * u             # gaze drifts up-left: ponder
        elif self._face_state != "listening" and (
                self._phase == "open"
                and (now - self._idle_t0) > 2.5):
            t = now - self._idle_t0 - 2.5
            ix, iy = self._idle_offset(t)
            dx, dy = ix * u, iy * u
        if self._capture:                          # hearing you: soft pulse
            pulse = 1.0 + 0.045 * math.sin(2 * math.pi * now * 2.6)

        # ---- phased blink: smooth envelope, never a snap ----
        blink = 1.0
        if self._blink_t0 is not None:
            p = (now - self._blink_t0) / BLINK_DUR
            if p < 1.0:
                blink = max(0.05, 0.5 + 0.5 * math.cos(math.pi * p))
            else:
                self._blink_t0 = None
        elif now > self._next_blink and not self._capture:
            self._blink_t0 = now
            self._next_blink = now + random.uniform(5.0, 15.0)

        # ---- eyes (organic per-corner shapes on a real glow halo) ----
        ew_base, eh_base, rot, corners, colour = EYEBALL[self._mood]
        ew = ew_base * u * k * pulse
        eh = eh_base * u * k * pulse * blink
        if self._capture:                          # attentive: eyes open wide
            ew *= 1.06
        eye_dx = ew * 0.85 + 44 * u * 0.5          # spacing from centre
        th = math.radians(rot) if rot else 0.0
        for side in (-1, 1):
            ex = cx + dx * 0.6 + side * eye_dx
            ey = cy + dy + (8 * u if self._mood == "sad" else 0) * k
            if eh <= 4.0:
                continue
            photo = self._glow_for(ex, ey, ew, eh, rot, side)
            if photo is not None:                  # real gaussian halo first
                cv.create_image(ex, ey, image=photo)
            pts = self._eye_pts(ex, ey, ew, eh, corners, side)
            if th:
                pts = self._rot_pts(pts, ex, ey, th * side)
            cv.create_polygon(pts, smooth=True, fill=colour, outline="")

        # ---- top exit instruction (replaces the old ✕ button) ----
        cv.create_text(cx, 30 * u, text="press CTRL+S to exit",
                       fill=TOP_INK,
                       font=(theme.FONT_FAMILY,
                             max(10, int(round(13 * self._scale)))))

        # ---- "listening" word (pops up from below) ----
        if self._capture:
            a = _ease_out(min(1.0, (now - self._capture_t0) / 0.24))
            ly = H - (30 + (1 - a) * 18) * u
            cv.create_text(cx, ly, text="listening",
                           fill=LISTEN_INK,
                           font=(theme.FONT_FAMILY,
                                 max(9, int(round(13 * self._scale)))))

    # -------- helpers ----------------------------------------------------
    @staticmethod
    def _eye_pts(cx: float, cy: float, w: float, h: float,
                 corners, side: int):
        """Organic eye outline: rounded rect centred on (cx, cy) with
        PER-CORNER radii — `corners` = (r_top_outer, r_top_inner,
        r_bottom_inner, r_bottom_outer) as fractions of height — mirrored
        horizontally for the right eye (`side == 1`)."""
        r_to, r_ti, r_bi, r_bo = (max(0.0, min(0.52, c)) for c in corners)
        if side == 1:                       # right eye: outer ↔ inner swap
            rtl, rtr = r_ti, r_to
            rbr, rbl = r_bo, r_bi
        else:                               # left eye: outer is the left side
            rtl, rtr, rbr, rbl = r_to, r_ti, r_bi, r_bo
        x0, x1 = cx - w / 2, cx + w / 2
        y0, y1 = cy - h / 2, cy + h / 2

        def rr(v):
            return max(0.0, min(v, w / 2, h / 2))

        rtl, rtr, rbr, rbl = rr(rtl * h), rr(rtr * h), rr(rbr * h), rr(rbl * h)
        # clockwise from the top-left arc end
        return [x0 + rtl, y0,
                x1 - rtr, y0, x1, y0, x1, y0 + rtr,
                x1, y1 - rbr, x1, y1, x1 - rbr, y1,
                x0 + rbl, y1, x0, y1, x0, y1 - rbl,
                x0, y0 + rtl, x0, y0]

    # -------- real glow (pre-blurred halo, cached) -----------------------
    @staticmethod
    def _rgba(param):
        "hex #RRGGBB -> (r, g, b) tuple for PIL."
        return (int(param[1:3], 16), int(param[3:5], 16),
                int(param[5:7], 16))

    def _build_glow(self, mood, side, ew, eh, rot_deg):
        "One blurred halo blob per eye — flattened onto the card colour."
        try:
            from PIL import Image, ImageDraw, ImageFilter
        except Exception:
            return None
        ss = 2                                    # supersample for smoothness
        pad = int(min(ew, eh) * GLOW_PUFF + 10 + abs(rot_deg) * 0.5)
        W = int(ew + 2 * pad) * ss
        H = int(eh + 2 * pad) * ss
        colour = self._rgba(EYEBALL[mood][4])
        shape = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(shape)
        d.rounded_rectangle(
            [W / 2 - ew / 2 * ss, H / 2 - eh / 2 * ss,
             W / 2 + ew / 2 * ss, H / 2 + eh / 2 * ss],
            radius=int(min(ew, eh) * 0.45 * ss), fill=colour + (255,))
        blurred = shape.filter(
            ImageFilter.GaussianBlur(min(ew, eh) * GLOW_BLUR * ss))
        if rot_deg:
            # PIL rotates CCW in y-up space; canvas tilts CW/CCW in y-down
            blurred = blurred.rotate(
                -math.degrees(math.radians(rot_deg)) * side,
                resample=Image.BICUBIC, center=(W / 2, H / 2))
        base = Image.new("RGBA", (W, H), (0x1B, 0x1D, 0x21, 255))
        base.alpha_composite(blurred)
        return base.convert("RGB")

    def _glow_for(self, ex, ey, ew, eh, rot_deg, side):
        """Halo PhotoImage centred on the eye's middle: a per-(mood, side)
        master blob is built once, then LANCZOS-resized to the eye's
        current size (3-px buckets keep the cache tiny). None while
        popping (the halo appears with the settled open face)."""
        if self._phase != "open":
            return None
        key = (self._mood, side)
        pil = self._glow_master.get(key)
        if pil is None:
            pil = self._build_glow(self._mood, side, ew, eh, rot_deg)
            if pil is None:
                return None
            self._glow_master[key] = pil
        pad = min(ew, eh) * GLOW_PUFF + 10 + abs(rot_deg) * 0.5
        tgt_w = max(6, int(round(ew + 2 * pad)))
        tgt_h = max(6, int(round(eh + 2 * pad)))
        bucket_w = int(tgt_w / 3) * 3
        bucket_h = int(tgt_h / 3) * 3
        ph = self._glow_photos.get((self._mood, side, bucket_w, bucket_h))
        if ph is None:
            try:
                from PIL import Image, ImageTk
                scaled = pil.resize((bucket_w, bucket_h), Image.LANCZOS)
                # Pillow's ImageTk bridges PIL -> tk photo (plain
                # tk.PhotoImage(image=pil) raises and would kill the glow)
                ph = ImageTk.PhotoImage(image=scaled, master=self.cv)
            except Exception:
                return None
            self._glow_photos[(self._mood, side, bucket_w, bucket_h)] = ph
        return ph

    @staticmethod
    def _rot_pts(pts, cx: float, cy: float, theta: float):
        "Rotate a point-pair list around (cx, cy) by theta radians."
        c, s = math.cos(theta), math.sin(theta)
        out = []
        for i in range(0, len(pts), 2):
            x, y = pts[i] - cx, pts[i + 1] - cy
            out += [cx + x * c - y * s, cy + x * s + y * c]
        return out


# Callbacks the app provides (documented contract):
#   callbacks = {
#     "on_capture_start": () -> stop TTS, VoiceIO.listener.start()
#     "on_capture_end":   () -> VoiceIO.listener.stop()
#     "on_close":         () -> app.toggle_voice()
#   }
