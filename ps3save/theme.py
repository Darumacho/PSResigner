"""Light/dark theme for the tkinter GUI.

Built on ttk's "clam" theme -- the only built-in ttk theme that is both
cross-platform (unlike "vista"/"winnative", Windows-only) and fully
re-colorable via Style.configure (unlike "aqua" on macOS, which mostly
ignores color overrides). Plain tk widgets (Text, Listbox) aren't covered
by ttk styles at all, so they're colored directly via style_text_widget()/
style_listbox().
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

LIGHT = {
    "bg": "#f0f0f0",
    "fg": "#000000",
    "entry_bg": "#ffffff",
    "entry_fg": "#000000",
    "select_bg": "#3366cc",
    "select_fg": "#ffffff",
    "border": "#a0a0a0",
    "trough": "#d9d9d9",
    "accent": "#0070cc",
    "accent_active": "#0059a3",
    "accent_fg": "#ffffff",
}

DARK = {
    "bg": "#2b2b2b",
    "fg": "#e0e0e0",
    "entry_bg": "#3c3f41",
    "entry_fg": "#e0e0e0",
    "select_bg": "#4a6fa5",
    "select_fg": "#ffffff",
    "border": "#555555",
    "trough": "#1e1e1e",
    "accent": "#3b82f6",
    "accent_active": "#5b94f7",
    "accent_fg": "#ffffff",
}


def palette(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def apply_ttk_style(root: tk.Tk, dark: bool) -> None:
    c = palette(dark)
    style = ttk.Style(root)
    style.theme_use("clam")

    root.configure(background=c["bg"])

    style.configure(".", background=c["bg"], foreground=c["fg"],
                     fieldbackground=c["entry_bg"], borderwidth=1)
    for widget in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label", "TCheckbutton"):
        style.configure(widget, background=c["bg"], foreground=c["fg"])
    style.map("TCheckbutton", background=[("active", c["bg"])], foreground=[("active", c["fg"])])

    style.configure("TButton", background=c["entry_bg"], foreground=c["fg"], bordercolor=c["border"])
    style.map(
        "TButton",
        background=[("active", c["select_bg"]), ("pressed", c["select_bg"])],
        foreground=[("active", c["select_fg"]), ("pressed", c["select_fg"])],
    )

    style.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                     bordercolor=c["accent"])
    style.map(
        "Accent.TButton",
        background=[("active", c["accent_active"]), ("pressed", c["accent_active"])],
        foreground=[("active", c["accent_fg"]), ("pressed", c["accent_fg"])],
    )

    style.configure("TEntry", fieldbackground=c["entry_bg"], foreground=c["entry_fg"],
                     bordercolor=c["border"], insertcolor=c["fg"])
    style.map("TEntry", fieldbackground=[("disabled", c["bg"])])

    style.configure("Treeview", background=c["entry_bg"], fieldbackground=c["entry_bg"],
                     foreground=c["entry_fg"], bordercolor=c["border"])
    style.map("Treeview", background=[("selected", c["select_bg"])],
              foreground=[("selected", c["select_fg"])])
    style.configure("Treeview.Heading", background=c["bg"], foreground=c["fg"])
    style.map("Treeview.Heading", background=[("active", c["bg"])])

    style.configure("TScrollbar", background=c["bg"], troughcolor=c["trough"],
                     bordercolor=c["border"], arrowcolor=c["fg"])

    style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
    style.configure("TNotebook.Tab", background=c["entry_bg"], foreground=c["fg"],
                     bordercolor=c["border"])
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["select_bg"])],
        foreground=[("selected", c["select_fg"])],
    )


def style_text_widget(widget: tk.Text, dark: bool) -> None:
    c = palette(dark)
    widget.configure(
        background=c["entry_bg"], foreground=c["entry_fg"],
        insertbackground=c["fg"],
        selectbackground=c["select_bg"], selectforeground=c["select_fg"],
    )


def style_listbox(widget: tk.Listbox, dark: bool) -> None:
    c = palette(dark)
    widget.configure(
        background=c["entry_bg"], foreground=c["entry_fg"],
        selectbackground=c["select_bg"], selectforeground=c["select_fg"],
    )
