"""App-wide visual theme for Trader's Little Jedi.

Design sources:
  ConcertTagger (SwiftUI/macOS) — "Dead-inspired" color palette, dark
    purple background, gradient accent system (FunColors.swift)
  HighGrabber (Python/Rich CLI) — status indicator pattern: ✓ → ! ✗,
    colored log lines, information-dense output style

Apply to any tk.Tk / tk.Toplevel:
    import audiohelper.theme as theme
    theme.apply(root)
    theme.style_log(text_widget)
"""

import sys
import tkinter as tk
from tkinter import ttk

# ── Color palette (from ConcertTagger/FunColors.swift) ───────────────────────

# Dead-inspired accent colors
DEAD_RED    = "#E63333"   # rgb(0.9, 0.2, 0.2)
DEAD_BLUE   = "#3366E6"   # rgb(0.2, 0.4, 0.9)
DEAD_YELLOW = "#FFD933"   # rgb(1.0, 0.85, 0.2)
DEAD_GREEN  = "#33B366"   # rgb(0.2, 0.7, 0.4)
DEAD_PURPLE = "#9933CC"   # rgb(0.6, 0.2, 0.8)
DEAD_ORANGE = "#FF8033"   # rgb(1.0, 0.5, 0.2)
DEAD_PINK   = "#FF6699"   # rgb(1.0, 0.4, 0.7)

# Background ramp (from ConcertTagger backgroundGradient)
BG_WINDOW  = "#0F0B1A"   # deepest — window chrome
BG_DEEP    = "#14102A"   # slightly lighter — main content bg
BG_PANEL   = "#1C1632"   # panels, frames, label frames
BG_WIDGET  = "#251E3E"   # entry fields, listboxes, treeviews
BG_SELECT  = "#3D2B7A"   # selected item background
BG_HOVER   = "#2E2550"   # hovered item background

# Text
FG_PRIMARY   = "#E8E8F5"   # near-white
FG_SECONDARY = "#9999C0"   # muted purple-gray
FG_DIM       = "#555588"   # very muted — timestamps, metadata
FG_DISABLED  = "#3D3A5C"

# Borders & separators
BORDER    = "#302860"
SEPARATOR = "#221C44"

# Accent hierarchy — maps to ConcertTagger gradient midpoints
ACCENT_PRIMARY = "#7744DD"   # funGradient1 (Purple→Blue→Pink) dominant
ACCENT_ACTIVE  = "#8855EE"
ACCENT_ACTION  = "#DD5500"   # funGradient2 (Orange→Yellow→Red)
ACCENT_DANGER  = "#CC2222"   # clear destructive red
ACCENT_SUCCESS = "#33B366"   # DEAD_GREEN — success / OK
ACCENT_INFO    = "#4ECCFF"   # bright cyan — matches HighGrabber [cyan]
ACCENT_WARN    = "#FFD933"   # DEAD_YELLOW — warning

# HighGrabber log status colors
LOG_OK   = DEAD_GREEN    # ✓ success
LOG_ERR  = DEAD_RED      # ✗ error / FAIL
LOG_WARN = DEAD_YELLOW   # ! warning
LOG_INFO = ACCENT_INFO   # → action / info
LOG_DIM  = FG_DIM        # metadata / path
LOG_BOLD = FG_PRIMARY    # headline / summary


# ── ttk theme application ─────────────────────────────────────────────────────

def apply(root: tk.Misc) -> None:
    """Apply the Dead-inspired dark theme to *root* and all descendants.

    Call once on the tk.Tk root after creating it.  All child windows
    (Toplevel) inherit the style automatically.
    """
    if isinstance(root, tk.Tk):
        root.configure(bg=BG_DEEP)

    style = ttk.Style(root)
    # clam is the most CSS-like built-in base — exposes every element
    style.theme_use("clam")

    fnt_ui   = _font(10)
    fnt_sm   = _font(9)
    fnt_bold = _font(10, bold=True)

    # ── Global defaults ───────────────────────────────────────────────────────
    style.configure(".",
        background=BG_PANEL,
        foreground=FG_PRIMARY,
        fieldbackground=BG_WIDGET,
        selectbackground=BG_SELECT,
        selectforeground=FG_PRIMARY,
        bordercolor=BORDER,
        darkcolor=BG_WINDOW,
        lightcolor=BG_PANEL,
        troughcolor=BG_DEEP,
        highlightcolor=BORDER,
        highlightbackground=BG_PANEL,
        relief="flat",
        font=fnt_ui,
    )

    # ── Frame / LabelFrame ────────────────────────────────────────────────────
    style.configure("TFrame", background=BG_PANEL, relief="flat")
    style.configure("TLabelframe",
        background=BG_PANEL, bordercolor=SEPARATOR, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label",
        background=BG_PANEL, foreground=FG_SECONDARY, font=_font(9, bold=True))

    # ── Label ─────────────────────────────────────────────────────────────────
    style.configure("TLabel", background=BG_PANEL, foreground=FG_PRIMARY)
    style.configure("Secondary.TLabel", foreground=FG_SECONDARY)
    style.configure("Dim.TLabel", foreground=FG_DIM, font=fnt_sm)
    style.configure("Bold.TLabel", font=fnt_bold)

    # ── Buttons ───────────────────────────────────────────────────────────────
    # Default (purple primary — funGradient1)
    style.configure("TButton",
        background=ACCENT_PRIMARY, foreground=FG_PRIMARY,
        borderwidth=0, relief="flat", padding=(10, 5), font=fnt_ui)
    style.map("TButton",
        background=[("active", ACCENT_ACTIVE), ("disabled", BG_PANEL)],
        foreground=[("disabled", FG_DISABLED)])

    # Toolbar buttons (ghost / subtle)
    style.configure("Ghost.TButton",
        background=BG_PANEL, foreground=FG_SECONDARY,
        borderwidth=1, relief="solid", padding=(8, 4),
        bordercolor=SEPARATOR)
    style.map("Ghost.TButton",
        background=[("active", BG_HOVER)],
        foreground=[("active", FG_PRIMARY)],
        bordercolor=[("active", BORDER)])

    # Action (orange — funGradient2)
    style.configure("Action.TButton",
        background=ACCENT_ACTION, foreground=FG_PRIMARY,
        borderwidth=0, relief="flat", padding=(10, 5))
    style.map("Action.TButton",
        background=[("active", "#EE6600"), ("disabled", BG_PANEL)],
        foreground=[("disabled", FG_DISABLED)])

    # Danger (red)
    style.configure("Danger.TButton",
        background=ACCENT_DANGER, foreground=FG_PRIMARY,
        borderwidth=0, relief="flat", padding=(10, 5))
    style.map("Danger.TButton",
        background=[("active", "#DD1111"), ("disabled", BG_PANEL)],
        foreground=[("disabled", FG_DISABLED)])

    # Success (green)
    style.configure("Success.TButton",
        background=ACCENT_SUCCESS, foreground=FG_PRIMARY,
        borderwidth=0, relief="flat", padding=(10, 5))
    style.map("Success.TButton",
        background=[("active", "#44CC77"), ("disabled", BG_PANEL)],
        foreground=[("disabled", FG_DISABLED)])

    # ── Entry ─────────────────────────────────────────────────────────────────
    style.configure("TEntry",
        fieldbackground=BG_WIDGET, foreground=FG_PRIMARY,
        bordercolor=BORDER, insertcolor=FG_PRIMARY,
        padding=(6, 4), selectbackground=BG_SELECT)
    style.map("TEntry",
        bordercolor=[("focus", ACCENT_PRIMARY)],
        fieldbackground=[("disabled", BG_PANEL)])

    # ── Combobox ──────────────────────────────────────────────────────────────
    style.configure("TCombobox",
        fieldbackground=BG_WIDGET, foreground=FG_PRIMARY,
        background=BG_WIDGET, arrowcolor=FG_SECONDARY, bordercolor=BORDER)
    style.map("TCombobox",
        fieldbackground=[("readonly", BG_WIDGET)],
        foreground=[("readonly", FG_PRIMARY)],
        bordercolor=[("focus", ACCENT_PRIMARY)])

    # ── Treeview ──────────────────────────────────────────────────────────────
    style.configure("Treeview",
        background=BG_WIDGET, foreground=FG_PRIMARY,
        fieldbackground=BG_WIDGET, bordercolor=BORDER, rowheight=26)
    style.configure("Treeview.Heading",
        background=BG_PANEL, foreground=FG_SECONDARY,
        borderwidth=0, relief="flat", font=_font(9, bold=True))
    style.map("Treeview",
        background=[("selected", BG_SELECT)],
        foreground=[("selected", FG_PRIMARY)])
    style.map("Treeview.Heading",
        background=[("active", BG_HOVER)],
        foreground=[("active", FG_PRIMARY)])

    # ── Notebook ──────────────────────────────────────────────────────────────
    style.configure("TNotebook",
        background=BG_DEEP, borderwidth=0, tabmargins=0)
    style.configure("TNotebook.Tab",
        background=BG_PANEL, foreground=FG_SECONDARY,
        padding=(12, 6), borderwidth=0)
    style.map("TNotebook.Tab",
        background=[("selected", BG_WIDGET)],
        foreground=[("selected", FG_PRIMARY)])

    # ── Scrollbar ─────────────────────────────────────────────────────────────
    style.configure("TScrollbar",
        background=BORDER, troughcolor=BG_DEEP,
        borderwidth=0, relief="flat", arrowsize=12, arrowcolor=FG_DIM)
    style.map("TScrollbar",
        background=[("active", ACCENT_PRIMARY)])

    # ── Progressbar ───────────────────────────────────────────────────────────
    style.configure("TProgressbar",
        troughcolor=BG_DEEP, background=ACCENT_PRIMARY,
        borderwidth=0, thickness=6)

    # ── Separator ─────────────────────────────────────────────────────────────
    style.configure("TSeparator", background=SEPARATOR)

    # ── Checkbutton / Radiobutton ─────────────────────────────────────────────
    style.configure("TCheckbutton",
        background=BG_PANEL, foreground=FG_PRIMARY,
        indicatorcolor=BG_WIDGET)
    style.map("TCheckbutton",
        indicatorcolor=[("selected", ACCENT_PRIMARY)],
        foreground=[("disabled", FG_DISABLED)],
        background=[("active", BG_PANEL)])
    style.configure("TRadiobutton",
        background=BG_PANEL, foreground=FG_PRIMARY)
    style.map("TRadiobutton",
        indicatorcolor=[("selected", ACCENT_PRIMARY)],
        background=[("active", BG_PANEL)])

    # ── PanedWindow / Sash ────────────────────────────────────────────────────
    style.configure("TPanedwindow", background=BG_DEEP)
    style.configure("Sash", sashthickness=5, sashrelief="flat",
                    background=SEPARATOR)

    # ── Status bar label ──────────────────────────────────────────────────────
    style.configure("Status.TLabel",
        background=BG_DEEP, foreground=FG_SECONDARY,
        font=fnt_sm, relief="sunken", padding=(6, 2))


def style_log(widget: tk.Text) -> None:
    """Apply dark theme + HighGrabber-style colored tags to a tk.Text log widget."""
    widget.configure(
        bg=BG_DEEP,
        fg=FG_PRIMARY,
        insertbackground=FG_PRIMARY,
        selectbackground=BG_SELECT,
        selectforeground=FG_PRIMARY,
        relief="flat",
        borderwidth=0,
        padx=8,
        pady=6,
        font=("Consolas", 9) if _is_win() else ("Menlo", 11),
    )
    # HighGrabber status tags
    widget.tag_configure("ok",   foreground=LOG_OK)
    widget.tag_configure("err",  foreground=LOG_ERR)
    widget.tag_configure("warn", foreground=LOG_WARN)
    widget.tag_configure("info", foreground=LOG_INFO)
    widget.tag_configure("dim",  foreground=LOG_DIM)
    widget.tag_configure("bold", foreground=LOG_BOLD,
                         font=("Consolas", 9, "bold") if _is_win() else ("Menlo", 11, "bold"))


def style_canvas(widget: tk.Canvas) -> None:
    """Apply dark background to a tk.Canvas."""
    widget.configure(bg=BG_DEEP, highlightthickness=0)


def classify_log_line(line: str) -> str:
    """Return a tag name for *line* based on HighGrabber status patterns."""
    s = line.strip()
    if s.startswith(("✓", "[OK]", "OK ", "Done", "Tagged", "Extracted",
                      "Split and tagged", "Waveform ready", "installed")):
        return "ok"
    if s.startswith(("✗", "FAIL", "Error", "ERROR", "Cannot", "Could not",
                      "failed", "decode failed")):
        return "err"
    if s.startswith(("!", "[WARN]", "Warning", "Drag-and-drop unavailable",
                      "Missing", "Skipping")):
        return "warn"
    if s.startswith(("→", "[INFO]", "→ ", "Decoding", "Scanning", "Detecting",
                      "Loading", "Tagging", "Splitting")):
        return "info"
    if s.startswith(("  ", "\t", "path:", "tools/", "/")) or not s:
        return "dim"
    return ""   # default foreground


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_win() -> bool:
    return sys.platform == "win32"


def _font(size: int, bold: bool = False):
    if _is_win():
        base = "Segoe UI"
        # tkinter on Windows needs pt not px; sizes work directly
    elif sys.platform == "darwin":
        base = "SF Pro Text"
    else:
        base = "Ubuntu"
    weight = "bold" if bold else "normal"
    return (base, size, weight)
