"""Minimal tkinter GUI for ps3save: browse a save folder, inspect it,
check its hashes and resign it to a new console_id/user_id.

Pure stdlib (tkinter) + pfd.py/sfo.py, plus tkinterdnd2 for native
drag-and-drop (tkinter alone has no cross-platform DnD support); runs
the same way on Windows and macOS either way.
"""

from __future__ import annotations

import math
import shutil
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

from . import games_conf, theme
from .cheats import (
    CheatEntry,
    CheatsError,
    UnsupportedCodeError,
    apply_sw_codes,
    extract_product_code,
    list_cheats_for_save,
)
from .history import HistoryEntry, append_history, clear_history, load_history
from .pfd import PfdConfig, PfdError, PfdFile
from .profiles import Profile, ProfileError, load_profiles, save_profiles
from .region import region_label
from .settings import load_settings, save_settings
from .sfo import SfoError, SfoFile
from .usb_scan import find_ps3_save_folders

HASH_NAMES = {0: "FILE", 1: "FILE_CID", 2: "FILE_DHK_CID2", 3: "FILE_AID_UID"}


def _round_window_corners(window: tk.Tk) -> None:
    """Windows 11 renders DWM-composited windows with rounded corners by
    default, but a bare Tk toplevel doesn't opt into that -- ask for it
    explicitly via DWMWA_WINDOW_CORNER_PREFERENCE, and give the thin system
    border DWM draws around the window a white DWMWA_BORDER_COLOR (default
    is an unobtrusive gray that barely reads as a border at all against a
    busy background image). No-op on Mac/Linux (already rounded/themed by
    the OS) and silently ignored on Windows versions that don't know these
    attributes (10 and earlier)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        GA_ROOT = 2
        hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), GA_ROOT)

        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        corner = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner)
        )

        DWMWA_BORDER_COLOR = 34
        white = ctypes.c_int(0x00FFFFFF)  # COLORREF: 0x00BBGGRR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR, ctypes.byref(white), ctypes.sizeof(white)
        )
    except (AttributeError, OSError):
        pass


def _resource_path(*parts: str) -> Path:
    """Resolve a bundled resource both running from source (project root is
    two levels up from this file) and frozen by PyInstaller (extracted
    under sys._MEIPASS -- see the `datas` entry in PSResigner.spec)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def _load_icon(name: str, target_size: int = 16) -> tk.PhotoImage | None:
    """Load a button icon from resources/ and rescale it to target_size x
    target_size. tkinter's PhotoImage only scales by integer factors
    (zoom = multiply, subsample = divide), so an arbitrary ratio like
    64 -> 48 (3/4) is done as zoom(3) then subsample(4)."""
    path = _resource_path("resources", name)
    if not path.exists():
        return None
    try:
        img = tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None
    original = img.width()
    if original and original != target_size:
        g = math.gcd(target_size, original)
        zoom_factor, sub_factor = target_size // g, original // g
        if zoom_factor > 1:
            img = img.zoom(zoom_factor, zoom_factor)
        if sub_factor > 1:
            img = img.subsample(sub_factor, sub_factor)
    return img


def _parse_user_id(value: str) -> bytes:
    value = value.strip()
    if value.isdigit():
        return PfdConfig.user_id_from_int(int(value))
    raw = value.encode("ascii")
    if len(raw) != 8:
        raise ValueError("User ID must be an integer or an 8-character string")
    return raw


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=10)
        self.master = master
        master.title("PSResigner")
        # macOS: wider/shorter default (952x702 on screen incl. the 32pt
        # title bar) -- Aqua widgets and fonts run larger than on Windows.
        master.geometry("952x670" if sys.platform == "darwin" else "780x720")
        _round_window_corners(master)

        icon_path = _resource_path("resources", "app_logo.png")
        if icon_path.exists():
            try:
                self._app_icon = tk.PhotoImage(file=str(icon_path))
                master.iconphoto(True, self._app_icon)
            except tk.TclError:
                pass
        self._build_background()
        master.columnconfigure(0, weight=1)

        self.save_dir: Path | None = None
        self.pf: PfdFile | None = None
        self._icons = {
            name: icon
            for name in ("open-folder.png", "profiles.png")
            if (icon := _load_icon(name)) is not None
        }
        self._settings = load_settings()
        self.dark_var = tk.BooleanVar(value=bool(self._settings.get("dark_mode", False)))
        self._recent_folders: list[str] = list(self._settings.get("recent_folders", []))

        self._build_folder_row()
        self._build_info_panel()
        self._build_notebook()
        self._build_log()
        self._setup_drag_drop()

        self.apply_theme()

    # ---- background image -----------------------------------------------

    def _build_background(self) -> None:
        """A discreet background image behind the window -- visible only in
        the margin left around the main frame (ttk panels are opaque, so
        it wouldn't otherwise show through). Needs Pillow (the only image
        format we have is JPEG, which plain tkinter can't load); silently
        skipped if Pillow isn't installed."""
        if Image is None:
            return
        path = _resource_path("resources", "BackgroundMain.jpg")
        if not path.exists():
            return
        try:
            self._bg_source = Image.open(path)
        except OSError:
            return

        self._bg_label = tk.Label(self.master, borderwidth=0)
        self._bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._bg_label.lower()
        self.master.bind("<Configure>", self._resize_background)
        self._resize_background()

    def _resize_background(self, event=None) -> None:
        if Image is None or not hasattr(self, "_bg_source"):
            return
        # A <Configure> binding on the toplevel also fires for every child
        # widget -- only the window's own events matter here. Take the size
        # from the event rather than update_idletasks() + winfo_*: forcing
        # an idle update from inside <Configure> while the window first maps
        # makes Tk 9 on macOS drop the requested geometry (window came up
        # at Tk's default 200x200).
        if event is not None and event.widget is not self.master:
            return
        if event is not None:
            w, h = event.width, event.height
        else:
            w, h = self.master.winfo_width(), self.master.winfo_height()
        w, h = max(w, 1), max(h, 1)
        if getattr(self, "_bg_size", None) == (w, h):
            return
        self._bg_size = (w, h)
        resized = self._bg_source.resize((w, h), Image.LANCZOS)
        self._bg_image = ImageTk.PhotoImage(resized)
        self._bg_label.configure(image=self._bg_image)

    # ---- theme ---------------------------------------------------------

    def apply_theme(self) -> None:
        dark = self.dark_var.get()
        theme.apply_ttk_style(self.master, dark)
        theme.style_text_widget(self.log_text, dark)
        self._content_border.configure(background=theme.palette(dark)["bg"])

    def on_toggle_theme(self) -> None:
        self.apply_theme()
        self._settings["dark_mode"] = self.dark_var.get()
        save_settings(self._settings)

    def _remember_console_id(self, console_id_hex: str) -> None:
        self._settings["last_console_id"] = console_id_hex
        save_settings(self._settings)

    def _remember_recent_folder(self, path: Path) -> None:
        entry = str(path)
        recents = [p for p in self._recent_folders if p != entry]
        recents.insert(0, entry)
        self._recent_folders = recents[:8]
        self._settings["recent_folders"] = self._recent_folders
        save_settings(self._settings)
        self.folder_combo.configure(values=self._recent_folders)

    # ---- layout ---------------------------------------------------

    def _build_folder_row(self) -> None:
        row = ttk.Frame(self.master)
        row.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 7))
        row.columnconfigure(1, weight=1)

        ttk.Label(row, text="Save folder:").grid(row=0, column=0, sticky="w")
        self.folder_var = tk.StringVar()
        self.folder_combo = ttk.Combobox(row, textvariable=self.folder_var, values=self._recent_folders)
        self.folder_combo.grid(row=0, column=1, sticky="ew", padx=6)
        self.folder_combo.bind("<<ComboboxSelected>>", self._on_recent_folder_selected)
        ttk.Button(
            row, text="Browse...", image=self._icons.get("open-folder.png"), compound="left",
            command=self.on_browse,
        ).grid(row=0, column=2)
        ttk.Button(
            row, text="Scan USB...", command=self.on_scan_usb,
        ).grid(row=0, column=3, padx=(6, 0))
        ttk.Checkbutton(
            row, text="Dark mode", variable=self.dark_var, command=self.on_toggle_theme
        ).grid(row=0, column=4, padx=(12, 0))

    def _build_info_panel(self) -> None:
        box = ttk.Frame(self.master)
        box.grid(row=1, column=0, sticky="ew", padx=14, pady=(7, 7))
        box.columnconfigure(1, weight=2, minsize=220)

        self.info_labels: dict[str, tk.StringVar] = {}
        fields = ["Title", "Sub-title", "PFD version", "Trophy set file", "Account ID",
                   "Product code", "Hash profile"]
        for i, field in enumerate(fields):
            ttk.Label(box, text=f"{field}:").grid(row=i, column=0, sticky="w", padx=(4, 8))
            var = tk.StringVar(value="-")
            ttk.Label(box, textvariable=var).grid(row=i, column=1, sticky="w")
            self.info_labels[field] = var

        columns = ("name", "size")
        self.file_tree = ttk.Treeview(box, columns=columns, show="headings", height=len(fields))
        self.file_tree.heading("name", text="File")
        self.file_tree.heading("size", text="Size (bytes)")
        self.file_tree.column("name", width=110)
        self.file_tree.column("size", width=110, anchor="e")
        self.file_tree.grid(row=0, column=2, rowspan=len(fields), sticky="nsew", padx=(12, 4))
        box.columnconfigure(2, weight=1)

        self.icon_image: tk.PhotoImage | None = None
        self.icon_label = ttk.Label(box)
        self.icon_label.grid(row=0, column=3, rowspan=len(fields), padx=(4, 4))

    def _build_notebook(self) -> None:
        """Hand-rolled tab switcher instead of ttk.Notebook: a Notebook is
        one single rectangle sized to fit its widest tab's content, so the
        tab strip's row is forced to that same width too -- the empty space
        beside the tab labels is opaque (notebook background), not a gap,
        and no style tweak can make it show the background image through.
        Two plain buttons + two separately-gridded frames (only one shown
        at a time, via grid()/grid_remove()) don't have that problem: the
        button row is only as wide as the two buttons.

        The content frames sit inside a plain tk.Frame with a white
        highlight ring -- a "page" look, like a Notebook's content pane --
        but the ring wraps only the content, not the tab row above it, so
        it can never reintroduce the dead space the Notebook replacement
        was built to get rid of."""
        tab_bar = ttk.Frame(self.master)
        tab_bar.grid(row=2, column=0, sticky="w", padx=14, pady=(7, 0))

        content_border = tk.Frame(self.master, highlightthickness=1,
                                   highlightbackground="white", highlightcolor="white", bd=0)
        content_border.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 7))
        content_border.columnconfigure(0, weight=1)
        self._content_border = content_border

        self._tab_frames: dict[str, ttk.Frame] = {}
        self._tab_buttons: dict[str, ttk.Button] = {}

        for key, label in (("resign", "Resign"), ("cheats", "Cheats")):
            btn = ttk.Button(tab_bar, text=label, command=lambda k=key: self._select_tab(k))
            btn.pack(side="left", padx=(0, 2))
            self._tab_buttons[key] = btn

            frame = ttk.Frame(content_border, padding=8)
            frame.grid(row=0, column=0, sticky="ew")
            self._tab_frames[key] = frame

        self._build_resign_tab(self._tab_frames["resign"])
        self._build_cheats_tab(self._tab_frames["cheats"])

        self._select_tab("resign")

    def _select_tab(self, key: str) -> None:
        for k, frame in self._tab_frames.items():
            (frame.grid if k == key else frame.grid_remove)()
        for k, btn in self._tab_buttons.items():
            btn.configure(style="Accent.TButton" if k == key else "TButton")

    def _build_resign_tab(self, parent: ttk.Frame) -> None:
        identity = ttk.Frame(parent)
        identity.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(identity, text="Console ID (32 hex chars):").grid(row=0, column=0, sticky="w", padx=4)
        self.console_id_var = tk.StringVar(value=self._settings.get("last_console_id", ""))
        ttk.Entry(identity, textvariable=self.console_id_var, width=40).grid(row=0, column=1, padx=6, pady=4)

        ttk.Label(identity, text="User ID:").grid(row=0, column=2, sticky="w", padx=4)
        self.user_id_var = tk.StringVar(value="1")
        ttk.Entry(identity, textvariable=self.user_id_var, width=12).grid(row=0, column=3, padx=6)

        ttk.Label(identity, text="Account ID (16 hex chars):").grid(row=1, column=0, sticky="w", padx=4)
        self.account_id_var = tk.StringVar()
        ttk.Entry(identity, textvariable=self.account_id_var, width=40).grid(row=1, column=1, padx=6, pady=(0, 4))

        identity_buttons = ttk.Frame(identity)
        identity_buttons.grid(row=1, column=2, columnspan=2, sticky="e", padx=6)
        ttk.Button(
            identity_buttons, text="History...", command=self.on_open_history,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            identity_buttons, text="Profiles...", image=self._icons.get("profiles.png"), compound="left",
            command=self.on_open_profiles,
        ).pack(side="left")

        buttons = ttk.Frame(parent)
        buttons.grid(row=1, column=0, sticky="ew")
        ttk.Button(buttons, text="Check hashes", command=self.on_check).pack(side="left", padx=(0, 6))
        ttk.Button(
            buttons, text="Resign (in place)", style="Accent.TButton", command=self.on_resign_in_place
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons, text="Resign to new folder...", style="Accent.TButton",
            command=self.on_resign_to_new_folder,
        ).pack(side="left", padx=6)
        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            buttons, text="Backup save folder before resigning in place", variable=self.backup_var
        ).pack(side="left", padx=(12, 0))

    def _build_cheats_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.cheats_status_var = tk.StringVar(value="Load a save to see available cheats.")
        ttk.Label(top, textvariable=self.cheats_status_var).pack(side="left")
        ttk.Button(top, text="Reload", command=self.refresh_cheats).pack(side="right")

        columns = ("file", "name")
        self.cheats_tree = ttk.Treeview(parent, columns=columns, show="headings", height=10)
        self.cheats_tree.heading("file", text="Target file(s)")
        self.cheats_tree.heading("name", text="Cheat")
        self.cheats_tree.column("file", width=160)
        self.cheats_tree.grid(row=1, column=0, sticky="nsew")
        self.cheats_tree.bind("<<TreeviewSelect>>", self._on_cheat_select)
        self._cheat_rows: dict[str, tuple[CheatEntry, list[str]]] = {}

        apply_row = ttk.Frame(parent)
        apply_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(apply_row, text="Apply to file:").pack(side="left")
        self.cheat_target_file_var = tk.StringVar()
        self.cheat_target_combo = ttk.Combobox(
            apply_row, textvariable=self.cheat_target_file_var, state="readonly", width=20
        )
        self.cheat_target_combo.pack(side="left", padx=6)
        ttk.Button(
            apply_row, text="Apply Cheat", style="Accent.TButton", command=self.on_apply_cheat
        ).pack(side="left", padx=6)

        ttk.Label(
            parent,
            text="From the community database (bucanero/apollo-patches). Supports Save Wizard "
                 "code types 0/1/2/3/8/9 (covers most real codes); anything else is rejected "
                 "rather than mis-applied.",
            wraplength=680,
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))

    def refresh_cheats(self) -> None:
        self.cheats_tree.delete(*self.cheats_tree.get_children())
        self._cheat_rows = {}
        self.cheat_target_combo.configure(values=())
        self.cheat_target_file_var.set("")
        if self.save_dir is None:
            self.cheats_status_var.set("Load a save to see available cheats.")
            return

        self.cheats_status_var.set("Loading cheats...")
        self.cheats_tree.update_idletasks()
        try:
            entries = list_cheats_for_save(self.save_dir)
        except CheatsError as e:
            self.cheats_status_var.set(str(e))
            return

        if not entries:
            self.cheats_status_var.set(f"No cheats found for {self.save_dir.name}.")
            return

        unsafe_count = 0
        for i, entry in enumerate(entries):
            files = entry.matching_files(self.save_dir)
            if len(files) > 3:
                file_display = f"{entry.file_pattern} ({len(files)} files)"
            else:
                file_display = ", ".join(files) or entry.file_pattern

            safe = self.pf is not None and any(self.pf.can_rehash_file(f) for f in files)
            if not safe:
                unsafe_count += 1
                file_display = f"⚠ {file_display}"

            iid = str(i)
            self.cheats_tree.insert("", "end", iid=iid, values=(file_display, entry.name))
            self._cheat_rows[iid] = (entry, files)

        status = f"{len(entries)} cheat(s) available for {self.save_dir.name}"
        if unsafe_count:
            status += f" ({unsafe_count} can't be safely applied -- see ⚠)"
        self.cheats_status_var.set(status)

    def _on_cheat_select(self, _event) -> None:
        sel = self.cheats_tree.selection()
        if not sel:
            return
        _entry, files = self._cheat_rows[sel[0]]
        self.cheat_target_combo.configure(values=files)
        self.cheat_target_file_var.set(files[0] if files else "")

    def on_apply_cheat(self) -> None:
        if self.save_dir is None:
            messagebox.showwarning("No folder", "Select a save folder first.")
            return
        sel = self.cheats_tree.selection()
        if not sel:
            messagebox.showwarning("No cheat selected", "Select a cheat first.")
            return
        entry, _files = self._cheat_rows[sel[0]]
        target_name = self.cheat_target_file_var.get()
        if not target_name:
            messagebox.showwarning("No target file", "Choose which file to apply this cheat to.")
            return
        if self.pf is None or not self.pf.can_rehash_file(target_name):
            messagebox.showerror(
                "Can't safely patch this file",
                f"'{target_name}' isn't PARAM.SFO or one of the fixed trophy files -- it needs a "
                "per-game \"secure file ID\" key this tool doesn't have yet. Patching it would "
                "leave its stored hash pointing at the OLD content, and the PS3 rejects that as "
                "corrupted data (error 80029207) instead of accepting the cheat. Refusing to "
                "apply this one.",
            )
            return

        config = self.current_config()
        if config is None:
            return
        if not self.console_id_var.get().strip():
            messagebox.showwarning(
                "Missing console ID",
                "Enter a target console ID first (patching a file invalidates its hash, so the "
                "save needs re-signing right after).",
            )
            return

        do_backup = self.backup_var.get()
        if not messagebox.askyesno(
            "Apply cheat",
            f"This will patch '{target_name}' in:\n{self.save_dir}\nand re-sign the save.\n\n"
            + ("A full backup copy of the folder will be kept. Continue?"
               if do_backup else
               "No backup will be made (checkbox unticked). Continue?"),
        ):
            return

        target_path = self.save_dir / target_name
        backup_dir = None
        try:
            if do_backup:
                backup_dir = self._backup_save_dir()
            patched = apply_sw_codes(target_path.read_bytes(), entry.codes)
            target_path.write_bytes(patched)
            self._do_resign(self.save_dir, config, action=f"apply_cheat:{entry.name}")
        except (UnsupportedCodeError, PfdError, SfoError, OSError) as e:
            messagebox.showerror("Apply cheat failed", str(e))
            return

        self._remember_console_id(config.console_id.hex())
        self.clear_log()
        if backup_dir is not None:
            self.log(f"Backed up original folder to {backup_dir}")
        self.log(f"Applied cheat '{entry.name}' to {target_name}, re-signed to "
                  f"console_id={config.console_id.hex()} user_id={config.user_id.decode()}")
        self.refresh_info()

    def _build_log(self) -> None:
        box = ttk.LabelFrame(self.master, text="Log")
        box.grid(row=4, column=0, sticky="nsew", padx=14, pady=(7, 14))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        self.log_text = tk.Text(box, height=5, state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(box, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    # ---- helpers ----------------------------------------------------

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _check_game_profile(self) -> tuple[games_conf.GameEntry | None, str]:
        """Best-effort games.conf lookup for the loaded save's game, plus a
        human-readable status -- used both to build the resign kwargs
        (secure_file_id_lookup/disc_hash_key) and to tell the user in the
        UI whether a recognized hash profile was actually found, instead
        of silently trusting an unrecognized game's resign to "just work"
        (it still does, for PARAM.SFO/trophies -- just not for other
        files that need a per-game secure_file_id)."""
        if self.save_dir is None:
            return None, "-"
        product_code = extract_product_code(self.save_dir.name)
        if product_code is None:
            return None, "-"
        try:
            entry = games_conf.lookup(product_code)
        except games_conf.GamesConfError as e:
            return None, f"Lookup failed ({e})"
        if entry is None:
            return None, "Not in database (PARAM.SFO/trophies only)"
        return entry, f"Recognized ({len(entry.secure_file_ids)} file key(s))"

    def _games_conf_kwargs(self) -> dict:
        """secure_file_id_lookup (+ disc_hash_key override) for the loaded
        save's game, if games.conf has an entry for it -- best-effort, {}
        on any lookup failure so callers just fall back to today's
        PARAM.SFO/TROP*.DAT-only behavior."""
        entry, _status = self._check_game_profile()
        if entry is None:
            return {}
        kwargs: dict = {"secure_file_id_lookup": entry.secure_file_id_for}
        if entry.disc_hash_key is not None:
            kwargs["disc_hash_key"] = entry.disc_hash_key
        return kwargs

    def current_config(self) -> PfdConfig | None:
        kwargs = self._games_conf_kwargs()
        console_hex = self.console_id_var.get().strip()
        if console_hex:
            try:
                kwargs["console_id"] = PfdConfig.console_id_from_hex(console_hex)
            except PfdError as e:
                messagebox.showerror("Invalid console ID", str(e))
                return None
        user_id_str = self.user_id_var.get().strip()
        if user_id_str:
            try:
                kwargs["user_id"] = _parse_user_id(user_id_str)
            except ValueError as e:
                messagebox.showerror("Invalid user ID", str(e))
                return None
        return PfdConfig(**kwargs)

    def refresh_info(self) -> None:
        for var in self.info_labels.values():
            var.set("-")
        self.file_tree.delete(*self.file_tree.get_children())
        self.icon_image = None
        self.icon_label.configure(image="")

        if self.save_dir is None:
            self.refresh_cheats()
            return

        product_code = extract_product_code(self.save_dir.name)
        self.info_labels["Product code"].set(product_code or "-")

        _entry, profile_status = self._check_game_profile()
        self.info_labels["Hash profile"].set(profile_status)

        icon_path = self.save_dir / "ICON0.PNG"
        if icon_path.exists():
            try:
                img = tk.PhotoImage(file=str(icon_path))
                while img.width() > 160:
                    img = img.subsample(2, 2)
                self.icon_image = img
                self.icon_label.configure(image=self.icon_image)
            except tk.TclError as e:
                self.log(f"Couldn't load ICON0.PNG: {e}")

        sfo_path = self.save_dir / "PARAM.SFO"
        if sfo_path.exists():
            try:
                sfo = SfoFile.load(sfo_path)
                title = sfo.title or "-"
                region = region_label(product_code) if product_code else None
                # Some SFOs already bake the region into the stored title
                # (seen on at least one real save) -- don't double it up.
                if region and region not in title:
                    title = f"{title} {region}"
                self.info_labels["Title"].set(title)
                self.info_labels["Sub-title"].set(sfo.sub_title or "-")
                account_id = sfo.get("ACCOUNT_ID")
                if isinstance(account_id, bytes) and account_id.strip(b"\x00"):
                    self.info_labels["Account ID"].set(account_id.decode("ascii", errors="replace"))
            except SfoError as e:
                self.log(f"Couldn't read PARAM.SFO: {e}")

        try:
            self.pf = PfdFile.load(self.save_dir / "PARAM.PFD", PfdConfig(**self._games_conf_kwargs()))
        except PfdError as e:
            self.pf = None
            self.log(f"Couldn't read PARAM.PFD: {e}")
            self.refresh_cheats()
            return

        self.info_labels["PFD version"].set(str(self.pf.version))
        self.info_labels["Trophy set file"].set("Yes" if self.pf.is_trophy else "No")
        for entry in self.pf.entries[: self.pf.num_used]:
            self.file_tree.insert("", "end", values=(entry.file_name, entry.file_size))
        self.refresh_cheats()

    # ---- actions ----------------------------------------------------

    def _load_save_folder(self, path: Path) -> bool:
        if not (path / "PARAM.PFD").exists():
            messagebox.showerror("Not a save folder", f"No PARAM.PFD found in:\n{path}")
            return False
        self.save_dir = path
        self.folder_var.set(str(path))
        self._remember_recent_folder(path)
        self.clear_log()
        self.refresh_info()
        self.log(f"Loaded {path}")
        return True

    def _on_recent_folder_selected(self, _event) -> None:
        self._load_save_folder(Path(self.folder_var.get()))

    def _setup_drag_drop(self) -> None:
        if DND_FILES is None:
            return
        self.master.drop_target_register(DND_FILES)
        self.master.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event) -> None:
        # event.data is a Tcl list (braces around any path containing
        # spaces) -- tk.splitlist parses that properly instead of a naive
        # str.split(' ').
        paths = self.master.tk.splitlist(event.data)
        if not paths:
            return
        path = Path(paths[0])
        if not path.is_dir():
            messagebox.showerror("Not a folder", f"Dropped item isn't a folder:\n{path}")
            return
        self._load_save_folder(path)

    def on_browse(self) -> None:
        chosen = filedialog.askdirectory(title="Select a PS3 save folder (containing PARAM.PFD)")
        if not chosen:
            return
        self._load_save_folder(Path(chosen))

    def on_check(self) -> None:
        if self.save_dir is None:
            messagebox.showwarning("No folder", "Select a save folder first.")
            return
        config = self.current_config()
        if config is None:
            return

        try:
            pf = PfdFile.load(self.save_dir / "PARAM.PFD", config)
            results = pf.check_file_hashes(self.save_dir)
        except PfdError as e:
            messagebox.showerror("Check failed", str(e))
            return

        self.clear_log()
        self.log(f"Checking against console_id={config.console_id.hex()} user_id={config.user_id.decode()}")
        all_ok = True
        for file_name, per_hash in results.items():
            if not per_hash:
                self.log(f"  {file_name}: SKIPPED (no fixed hash key known for this file)")
                continue
            parts = []
            for which, ok in per_hash.items():
                parts.append(f"{HASH_NAMES[which]}={'OK' if ok else 'MISMATCH'}")
                if not ok:
                    all_ok = False
            self.log(f"  {file_name}: " + " ".join(parts))
        self.log("Result: all checkable hashes OK" if all_ok else "Result: one or more hash mismatches")

    def _backup_save_dir(self) -> Path:
        backup_dir = self.save_dir.parent / f"{self.save_dir.name}_backup_{datetime.now():%Y%m%d_%H%M%S}"
        shutil.copytree(self.save_dir, backup_dir)
        return backup_dir

    def _do_resign(self, target_dir: Path, config: PfdConfig, action: str) -> None:
        account_id = self.account_id_var.get().strip()
        user_id_str = self.user_id_var.get().strip()
        sfo_path = target_dir / "PARAM.SFO"
        if (account_id or user_id_str) and sfo_path.exists():
            sfo = SfoFile.load(sfo_path)
            if account_id:
                sfo.set_account_id(account_id)
            if user_id_str.isdigit():
                sfo.set_user_id(int(user_id_str))
            sfo.save(sfo_path)

        pf = PfdFile.load(target_dir / "PARAM.PFD", config)
        pf.resign(target_dir)
        pf.save(target_dir / "PARAM.PFD")

        append_history(HistoryEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            action=action,
            folder=str(target_dir),
            console_id=config.console_id.hex(),
            user_id=config.user_id.decode(),
            account_id=account_id,
        ))

    def on_resign_in_place(self) -> None:
        if self.save_dir is None:
            messagebox.showwarning("No folder", "Select a save folder first.")
            return
        config = self.current_config()
        if config is None:
            return
        if not self.console_id_var.get().strip():
            messagebox.showwarning("Missing console ID", "Enter a target console ID first.")
            return

        do_backup = self.backup_var.get()
        if not messagebox.askyesno(
            "Resign in place",
            f"This will overwrite files in:\n{self.save_dir}\n\n"
            + ("A full backup copy of the folder will be kept. Continue?"
               if do_backup else
               "No backup will be made (checkbox unticked). Continue?"),
        ):
            return

        backup_dir = None
        try:
            if do_backup:
                backup_dir = self._backup_save_dir()
            self._do_resign(self.save_dir, config, action="resign_in_place")
        except (PfdError, SfoError, OSError) as e:
            messagebox.showerror("Resign failed", str(e))
            return

        self._remember_console_id(config.console_id.hex())
        self.clear_log()
        if backup_dir is not None:
            self.log(f"Backed up original folder to {backup_dir}")
        message = f"Resigned {self.save_dir} to console_id={config.console_id.hex()} user_id={config.user_id.decode()}"
        if self.account_id_var.get().strip():
            message += f" account_id={self.account_id_var.get().strip().lower()}"
        self.log(message)
        self.refresh_info()

    def on_resign_to_new_folder(self) -> None:
        if self.save_dir is None:
            messagebox.showwarning("No folder", "Select a save folder first.")
            return
        config = self.current_config()
        if config is None:
            return
        if not self.console_id_var.get().strip():
            messagebox.showwarning("Missing console ID", "Enter a target console ID first.")
            return

        chosen = filedialog.askdirectory(title="Choose where to create the resigned copy's parent folder")
        if not chosen:
            return
        target_dir = Path(chosen) / self.save_dir.name
        if target_dir.exists():
            messagebox.showerror("Already exists", f"{target_dir} already exists.")
            return

        try:
            shutil.copytree(self.save_dir, target_dir)
            self._do_resign(target_dir, config, action="resign_to_new_folder")
        except (PfdError, SfoError, OSError) as e:
            messagebox.showerror("Resign failed", str(e))
            return

        self._remember_console_id(config.console_id.hex())
        self.clear_log()
        self.log(f"Resigned copy written to {target_dir}")
        message = f"console_id={config.console_id.hex()} user_id={config.user_id.decode()}"
        if self.account_id_var.get().strip():
            message += f" account_id={self.account_id_var.get().strip().lower()}"
        self.log(message)

    def on_open_profiles(self) -> None:
        ProfilesDialog(self)

    def on_open_history(self) -> None:
        HistoryDialog(self)

    def on_scan_usb(self) -> None:
        UsbScanDialog(self)


class ProfilesDialog(tk.Toplevel):
    """Named Owner profiles: pick one to fill in the main window's User ID
    / Account ID, or save the current fields as a new/updated profile --
    same idea as KG's Save Resigner's Profiles window."""

    def __init__(self, app: App):
        super().__init__(app.master)
        self.app = app
        self.title("Profiles")
        self.geometry("640x410")
        self.transient(app.master)
        _round_window_corners(self)

        try:
            self.profiles = load_profiles()
        except ProfileError as e:
            messagebox.showerror("Couldn't load profiles", str(e), parent=self)
            self.profiles = []

        self._build()
        self._refresh_list()
        self.configure(background=theme.palette(app.dark_var.get())["bg"])
        theme.style_listbox(self.listbox, app.dark_var.get())

    def _build(self) -> None:
        self.listbox = tk.Listbox(self, height=10)
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.on_apply())

        form = ttk.Frame(self)
        form.pack(fill="x", padx=10, pady=4)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Profile Name:").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="User ID:").grid(row=1, column=0, sticky="w")
        self.user_id_var = tk.StringVar(value="0")
        ttk.Entry(form, textvariable=self.user_id_var).grid(row=1, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="Account ID:").grid(row=2, column=0, sticky="w")
        self.account_id_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.account_id_var).grid(row=2, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(form, text="Console ID (optional):").grid(row=3, column=0, sticky="w")
        self.console_id_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.console_id_var).grid(row=3, column=1, sticky="ew", padx=6, pady=2)

        self._icons = {
            name: icon
            for name in ("load.png", "save.png", "add.png", "delete.png")
            if (icon := _load_icon(name)) is not None
        }

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(
            buttons, text="Load From PARAM.SFO", image=self._icons.get("load.png"), compound="left",
            command=self.on_load_from_sfo,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            buttons, text="Save Profile Changes", image=self._icons.get("save.png"), compound="left",
            command=self.on_save_changes,
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons, text="Add New Profile", image=self._icons.get("add.png"), compound="left",
            command=self.on_add_new,
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons, text="Delete", image=self._icons.get("delete.png"), compound="left",
            command=self.on_delete,
        ).pack(side="left", padx=6)

        apply_row = ttk.Frame(self)
        apply_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(apply_row, text="Use in main window", command=self.on_apply).pack(side="right")

    def _refresh_list(self, select_index: int | None = None) -> None:
        self.listbox.delete(0, "end")
        for p in self.profiles:
            self.listbox.insert("end", p.name)
        if select_index is not None and 0 <= select_index < len(self.profiles):
            self.listbox.selection_set(select_index)
            self.listbox.see(select_index)

    def _selected_index(self) -> int | None:
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _on_select(self, _event) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        p = self.profiles[idx]
        self.name_var.set(p.name)
        self.user_id_var.set(str(p.user_id))
        self.account_id_var.set(p.account_id)
        self.console_id_var.set(p.console_id)

    def _fields_as_profile(self) -> Profile | None:
        try:
            user_id = int(self.user_id_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Invalid User ID", "User ID must be an integer.", parent=self)
            return None
        profile = Profile(
            name=self.name_var.get().strip(),
            user_id=user_id,
            account_id=self.account_id_var.get().strip().lower(),
            console_id=self.console_id_var.get().strip().lower(),
        )
        try:
            profile.validate()
        except ProfileError as e:
            messagebox.showerror("Invalid profile", str(e), parent=self)
            return None
        return profile

    def _persist(self) -> None:
        try:
            save_profiles(self.profiles)
        except ProfileError as e:
            messagebox.showerror("Couldn't save profiles", str(e), parent=self)

    def on_load_from_sfo(self) -> None:
        if self.app.save_dir is None:
            messagebox.showwarning("No folder", "Load a save folder in the main window first.", parent=self)
            return
        sfo_path = self.app.save_dir / "PARAM.SFO"
        if not sfo_path.exists():
            messagebox.showwarning("No PARAM.SFO", f"No PARAM.SFO in {self.app.save_dir}", parent=self)
            return
        sfo = SfoFile.load(sfo_path)
        user_id, account_id = sfo.get_owner()
        if user_id is not None:
            self.user_id_var.set(str(user_id))
        if account_id is not None:
            self.account_id_var.set(account_id)
        if not self.name_var.get().strip() and sfo.title:
            self.name_var.set(sfo.title)

    def on_save_changes(self) -> None:
        idx = self._selected_index()
        if idx is None:
            messagebox.showwarning("No profile selected", "Select a profile to update first.", parent=self)
            return
        profile = self._fields_as_profile()
        if profile is None:
            return
        self.profiles[idx] = profile
        self._persist()
        self._refresh_list(select_index=idx)

    def on_add_new(self) -> None:
        profile = self._fields_as_profile()
        if profile is None:
            return
        if any(p.name == profile.name for p in self.profiles):
            messagebox.showerror("Name already used", f"A profile named '{profile.name}' already exists.",
                                  parent=self)
            return
        self.profiles.append(profile)
        self._persist()
        self._refresh_list(select_index=len(self.profiles) - 1)

    def on_delete(self) -> None:
        idx = self._selected_index()
        if idx is None:
            messagebox.showwarning("No profile selected", "Select a profile to delete first.", parent=self)
            return
        del self.profiles[idx]
        self._persist()
        self._refresh_list()

    def on_apply(self) -> None:
        idx = self._selected_index()
        if idx is None:
            messagebox.showwarning("No profile selected", "Select a profile first.", parent=self)
            return
        p = self.profiles[idx]
        self.app.user_id_var.set(str(p.user_id))
        self.app.account_id_var.set(p.account_id)
        message = f"Applied profile '{p.name}' (user_id={p.user_id}, account_id={p.account_id}"
        if p.console_id:
            self.app.console_id_var.set(p.console_id)
            message += f", console_id={p.console_id}"
        self.app.log(message + ")")


class HistoryDialog(tk.Toplevel):
    """Read-only log of past resign operations (plain resign, resign-to-
    new-folder, apply-cheat) -- newest first. Double-clicking a row
    reloads that save folder in the main window, same as picking one
    from the "Récents" combobox."""

    def __init__(self, app: App):
        super().__init__(app.master)
        self.app = app
        self.title("History")
        self.geometry("760x420")
        self.transient(app.master)
        _round_window_corners(self)

        self.entries: list[HistoryEntry] = list(reversed(load_history()))

        self._build()
        self._refresh_list()
        self.configure(background=theme.palette(app.dark_var.get())["bg"])

    def _build(self) -> None:
        columns = ("timestamp", "action", "folder", "console_id", "account_id")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=14)
        self.tree.heading("timestamp", text="Date")
        self.tree.heading("action", text="Action")
        self.tree.heading("folder", text="Folder")
        self.tree.heading("console_id", text="Console ID")
        self.tree.heading("account_id", text="Account ID")
        self.tree.column("timestamp", width=130)
        self.tree.column("action", width=130)
        self.tree.column("folder", width=260)
        self.tree.column("console_id", width=140)
        self.tree.column("account_id", width=110)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        self.tree.bind("<Double-Button-1>", lambda _e: self.on_load_folder())

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="Load folder", command=self.on_load_folder).pack(side="left")
        ttk.Button(buttons, text="Clear history", command=self.on_clear).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

    def _refresh_list(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, entry in enumerate(self.entries):
            self.tree.insert("", "end", iid=str(i), values=(
                entry.timestamp, entry.action, entry.folder, entry.console_id, entry.account_id,
            ))

    def _selected_entry(self) -> HistoryEntry | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.entries[int(sel[0])]

    def on_load_folder(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            messagebox.showwarning("No entry selected", "Select a history entry first.", parent=self)
            return
        path = Path(entry.folder)
        if not path.exists():
            messagebox.showerror("Folder not found", f"This folder no longer exists:\n{path}", parent=self)
            return
        self.app._load_save_folder(path)

    def on_clear(self) -> None:
        if not messagebox.askyesno("Clear history", "Delete the whole resign history? This can't be undone.",
                                    parent=self):
            return
        clear_history()
        self.entries = []
        self._refresh_list()


class UsbScanDialog(tk.Toplevel):
    """Lists every PS3 save folder found under <drive>/PS3/SAVEDATA/ on
    any currently mounted drive -- the layout the PS3 itself (and tools
    like multiMAN) write to when backing up saves to a USB stick."""

    def __init__(self, app: App):
        super().__init__(app.master)
        self.app = app
        self.title("Scan USB")
        self.geometry("640x380")
        self.transient(app.master)
        _round_window_corners(self)

        self.found: list[Path] = []

        self._build()
        self.configure(background=theme.palette(app.dark_var.get())["bg"])
        self.on_rescan()

    def _build(self) -> None:
        self.status_var = tk.StringVar(value="Scanning...")
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", padx=10, pady=(10, 4), anchor="w")

        columns = ("drive", "folder", "title")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=12)
        self.tree.heading("drive", text="Drive")
        self.tree.heading("folder", text="Save folder")
        self.tree.heading("title", text="Title")
        self.tree.column("drive", width=60, anchor="center")
        self.tree.column("folder", width=200)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.tree.bind("<Double-Button-1>", lambda _e: self.on_load())

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="Load", command=self.on_load).pack(side="left")
        ttk.Button(buttons, text="Rescan", command=self.on_rescan).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

    def on_rescan(self) -> None:
        self.status_var.set("Scanning...")
        self.update_idletasks()

        self.found = find_ps3_save_folders()
        self.tree.delete(*self.tree.get_children())
        for i, path in enumerate(self.found):
            title = "-"
            sfo_path = path / "PARAM.SFO"
            if sfo_path.exists():
                try:
                    title = SfoFile.load(sfo_path).title or "-"
                except SfoError:
                    pass
            self.tree.insert("", "end", iid=str(i), values=(path.drive or path.anchor, path.name, title))

        if not self.found:
            self.status_var.set("No PS3/SAVEDATA folder found on any currently mounted drive.")
        else:
            self.status_var.set(f"{len(self.found)} save folder(s) found.")

    def on_load(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("No folder selected", "Select a save folder first.", parent=self)
            return
        path = self.found[int(sel[0])]
        if self.app._load_save_folder(path):
            self.destroy()


def main() -> None:
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
