"""EshanAI — entrypoint.

DPI awareness is enabled BEFORE customtkinter is imported anywhere so Tk
doesn't blur-scale on the user's 150% display.
"""
from __future__ import annotations

import sys


def _enable_dpi_awareness() -> None:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)        # per-monitor v2
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()         # system aware
            except Exception:
                pass
    except Exception:
        pass


def main() -> int:
    _enable_dpi_awareness()

    try:
        from ui.app import EshanAIApp
    except Exception as e:                                 # no window yet: bare message
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"EshanAI could not start:\n\n{e}", "EshanAI", 0x10)
        except Exception:
            print("EshanAI could not start:", e)
        return 1

    try:
        app = EshanAIApp()
    except Exception as e:                                 # partial UI built
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"EshanAI failed to initialize:\n\n{e}", "EshanAI", 0x10)
        except Exception:
            print("EshanAI failed to initialize:", e)
        return 1

    try:
        app.mainloop()
    finally:
        return 0


if __name__ == "__main__":
    sys.exit(main())
