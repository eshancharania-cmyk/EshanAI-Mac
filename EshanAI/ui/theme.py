"""EshanAI UI palette — grey-black surfaces with a minimal blue accent."""

# surfaces (grey-black, ChatGPT-dark inspired)
WINDOW_BG = "#111113"        # app window / darkest backdrop
SIDEBAR_BG = "#0C0C0E"       # left sidebar (darker than canvas, like ChatGPT)
CHAT_BG = "#111113"          # main chat panel (uniform canvas)
BUBBLE_USER = "#2A2D33"      # user message bubble (grey-blue, no border)
BUBBLE_ASSISTANT = "#1A1C20"  # assistant reply bubble (left edge, grows with text)
COMPOSER_BG = "#1A1C20"      # floating input pill
EVENT_BG = "#191B1F"         # tool/status chip
CODE_BG = "#15161A"          # inline/block code background

# borders
BORDER = "#26282C"
SEPARATOR = "#0C0C0E"        # subtle turn-separator hairline (slightly darker than CHAT_BG)

# message bubbles (user + assistant share the same geometry by design)
BUBBLE_RADIUS = 18

# text
TEXT = "#ECEEF1"             # primary
TEXT_2 = "#A7ABB4"           # secondary
TEXT_3 = "#767A82"           # muted / icons

# accent (blue kept minimal)
ACCENT = "#4C83C3"
ACCENT_LIGHT = "#78A9DF"

# semantic
OK = "#65B98A"
WARN = "#D9A65A"
ERR = "#D76E75"

# fonts (platform-appropriate, resizable via settings font_scale)
FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


def f(size: float, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)


def mono(size: float, weight: str = "normal") -> tuple:
    return (MONO_FAMILY, size, weight)
