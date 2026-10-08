"""Minimal tkinter GUI for ps3save: browse a save folder, inspect it,
check its hashes and resign it to a new console_id/user_id.

Pure stdlib (tkinter) + pfd.py/sfo.py; no extra GUI dependency, so it
runs the same way on Windows and macOS.
"""

from __future__ import annotations

import shutil
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .pfd import PfdConfig, PfdError, PfdFile
from .profiles import Profile, ProfileError, load_profiles, save_profiles
from .sfo import SfoError, SfoFile

HASH_NAMES = {0: "FILE", 1: "FILE_CID", 2: "FILE_DHK_CID2", 3: "FILE_AID_UID"}


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
        master.geometry("720x560")
        self.grid(row=0, column=0, sticky="nsew")
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)

        self.save_dir: Path | None = None
        self.pf: PfdFile | None = None

        self._build_folder_row()
        self._build_info_panel()
        self._build_target_row()
        self._build_buttons()
        self._build_log()

        self.rowconfigure(4, weight=1)
        self.columnconfigure(0, weight=1)

    # ---- layout ---------------------------------------------------

    def _build_folder_row(self) -> None:
        row = ttk.Frame(self)
        row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        row.columnconfigure(1, weight=1)

        ttk.Label(row, text="Save folder:").grid(row=0, column=0, sticky="w")
        self.folder_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.folder_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(row, text="Browse...", command=self.on_browse).grid(row=0, column=2)

    def _build_info_panel(self) -> None:
        box = ttk.LabelFrame(self, text="Save info")
        box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        self.info_labels: dict[str, tk.StringVar] = {}
        for i, field in enumerate(["Title", "Sub-title", "PFD version", "Trophy save", "Account ID"]):
            ttk.Label(box, text=f"{field}:").grid(row=i, column=0, sticky="w", padx=(4, 8))
            var = tk.StringVar(value="-")
            ttk.Label(box, textvariable=var).grid(row=i, column=1, sticky="w")
            self.info_labels[field] = var

        columns = ("name", "size")
        self.file_tree = ttk.Treeview(box, columns=columns, show="headings", height=6)
        self.file_tree.heading("name", text="File")
        self.file_tree.heading("size", text="Size (bytes)")
        self.file_tree.column("size", width=120, anchor="e")
        self.file_tree.grid(row=0, column=2, rowspan=5, sticky="nsew", padx=(12, 4))
        box.columnconfigure(2, weight=1)

        self.icon_image: tk.PhotoImage | None = None
        self.icon_label = ttk.Label(box)
        self.icon_label.grid(row=0, column=3, rowspan=5, sticky="n", padx=(4, 4))

    def _build_target_row(self) -> None:
        row = ttk.LabelFrame(self, text="Target identity")
        row.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(row, text="Console ID (32 hex chars):").grid(row=0, column=0, sticky="w", padx=4)
        self.console_id_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.console_id_var, width=40).grid(row=0, column=1, padx=6, pady=4)

        ttk.Label(row, text="User ID:").grid(row=0, column=2, sticky="w", padx=4)
        self.user_id_var = tk.StringVar(value="1")
        ttk.Entry(row, textvariable=self.user_id_var, width=12).grid(row=0, column=3, padx=6)

        ttk.Label(row, text="Account ID (16 hex chars):").grid(row=1, column=0, sticky="w", padx=4)
        self.account_id_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.account_id_var, width=40).grid(row=1, column=1, padx=6, pady=(0, 4))

        ttk.Button(row, text="Profiles...", command=self.on_open_profiles).grid(
            row=1, column=2, columnspan=2, sticky="e", padx=6
        )

    def _build_buttons(self) -> None:
        row = ttk.Frame(self)
        row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(row, text="Check hashes", command=self.on_check).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Resign (in place)", command=self.on_resign_in_place).pack(side="left", padx=6)
        ttk.Button(row, text="Resign to new folder...", command=self.on_resign_to_new_folder).pack(
            side="left", padx=6
        )
        self.backup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row, text="Backup save folder before resigning in place", variable=self.backup_var
        ).pack(side="left", padx=(12, 0))

    def _build_log(self) -> None:
        box = ttk.LabelFrame(self, text="Log")
        box.grid(row=4, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        self.log_text = tk.Text(box, height=12, state="disabled", wrap="word")
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

    def current_config(self) -> PfdConfig | None:
        kwargs = {}
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
            return

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
                self.info_labels["Title"].set(sfo.title or "-")
                self.info_labels["Sub-title"].set(sfo.sub_title or "-")
                account_id = sfo.get("ACCOUNT_ID")
                if isinstance(account_id, bytes) and account_id.strip(b"\x00"):
                    self.info_labels["Account ID"].set(account_id.decode("ascii", errors="replace"))
            except SfoError as e:
                self.log(f"Couldn't read PARAM.SFO: {e}")

        try:
            self.pf = PfdFile.load(self.save_dir / "PARAM.PFD", PfdConfig())
        except PfdError as e:
            self.pf = None
            self.log(f"Couldn't read PARAM.PFD: {e}")
            return

        self.info_labels["PFD version"].set(str(self.pf.version))
        self.info_labels["Trophy save"].set("Yes" if self.pf.is_trophy else "No")
        for entry in self.pf.entries[: self.pf.num_used]:
            self.file_tree.insert("", "end", values=(entry.file_name, entry.file_size))

    # ---- actions ----------------------------------------------------

    def on_browse(self) -> None:
        chosen = filedialog.askdirectory(title="Select a PS3 save folder (containing PARAM.PFD)")
        if not chosen:
            return
        path = Path(chosen)
        if not (path / "PARAM.PFD").exists():
            messagebox.showerror("Not a save folder", f"No PARAM.PFD found in:\n{path}")
            return
        self.save_dir = path
        self.folder_var.set(str(path))
        self.clear_log()
        self.refresh_info()
        self.log(f"Loaded {path}")

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

    def _do_resign(self, target_dir: Path, config: PfdConfig) -> None:
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
                backup_dir = self.save_dir.parent / f"{self.save_dir.name}_backup_{datetime.now():%Y%m%d_%H%M%S}"
                shutil.copytree(self.save_dir, backup_dir)
            self._do_resign(self.save_dir, config)
        except (PfdError, SfoError, OSError) as e:
            messagebox.showerror("Resign failed", str(e))
            return

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
            self._do_resign(target_dir, config)
        except (PfdError, SfoError, OSError) as e:
            messagebox.showerror("Resign failed", str(e))
            return

        self.clear_log()
        self.log(f"Resigned copy written to {target_dir}")
        message = f"console_id={config.console_id.hex()} user_id={config.user_id.decode()}"
        if self.account_id_var.get().strip():
            message += f" account_id={self.account_id_var.get().strip().lower()}"
        self.log(message)

    def on_open_profiles(self) -> None:
        ProfilesDialog(self)


class ProfilesDialog(tk.Toplevel):
    """Named Owner profiles: pick one to fill in the main window's User ID
    / Account ID, or save the current fields as a new/updated profile --
    same idea as KG's Save Resigner's Profiles window."""

    def __init__(self, app: App):
        super().__init__(app.master)
        self.app = app
        self.title("Profiles")
        self.geometry("520x380")
        self.transient(app.master)

        try:
            self.profiles = load_profiles()
        except ProfileError as e:
            messagebox.showerror("Couldn't load profiles", str(e), parent=self)
            self.profiles = []

        self._build()
        self._refresh_list()

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

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(buttons, text="Load From PARAM.SFO", command=self.on_load_from_sfo).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(buttons, text="Save Profile Changes", command=self.on_save_changes).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Add New Profile", command=self.on_add_new).pack(side="left", padx=6)
        ttk.Button(buttons, text="Delete", command=self.on_delete).pack(side="left", padx=6)

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
        self.app.log(f"Applied profile '{p.name}' (user_id={p.user_id}, account_id={p.account_id})")


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
