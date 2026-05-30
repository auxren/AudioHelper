"""Create / Check torrent dialogs.

Tracker list is read from and written to trackers.txt (next to config.json).
Users can edit that file directly in any text editor, or use the Edit… dialog.
"""

import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from . import torrent as tr
from .config import CONFIG_PATH

TRACKERS_FILE = CONFIG_PATH.parent / "trackers.txt"

_DEFAULT_TRACKERS = [
    "http://bt.etree.org:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.stealth.si:80/announce",
]

_TRACKERS_HEADER = (
    "# Trader's Little Jedi — tracker list\n"
    "# One announce URL per line.\n"
    "# Lines starting with # are comments and are ignored.\n"
    "# Edit this file directly or use Torrent → Create torrent file → Edit…\n\n"
)


def _load_trackers() -> list[str]:
    """Return tracker URLs from trackers.txt; create the file if absent."""
    if not TRACKERS_FILE.exists():
        _save_trackers(_DEFAULT_TRACKERS)
        return list(_DEFAULT_TRACKERS)
    lines = TRACKERS_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _save_trackers(urls: list[str]) -> None:
    TRACKERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRACKERS_FILE.write_text(
        _TRACKERS_HEADER + "\n".join(urls) + "\n",
        encoding="utf-8",
    )


# ── Tracker list editor ────────────────────────────────────────────────────────


class TrackerEditDialog(tk.Toplevel):
    """Edit the tracker list stored in trackers.txt."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Edit Tracker List")
        self.transient(parent)
        self.grab_set()
        self.geometry("560x440")
        self.minsize(420, 320)
        self._result: list[str] | None = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="One announce URL per line.  Lines starting with # are ignored.",
            foreground="gray",
        ).pack(anchor="w", pady=(0, 4))

        tf = ttk.Frame(frm)
        tf.pack(fill="both", expand=True)
        self.txt = tk.Text(tf, font=("Consolas", 9), wrap="none", undo=True)
        sb_y = ttk.Scrollbar(tf, orient="vertical",   command=self.txt.yview)
        sb_x = ttk.Scrollbar(tf, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right", fill="y")
        sb_x.pack(side="bottom", fill="x")
        self.txt.pack(fill="both", expand=True)

        trackers = _load_trackers()
        self.txt.insert("1.0", "\n".join(trackers))

        path_lbl = ttk.Label(
            frm,
            text=f"File: {TRACKERS_FILE}",
            foreground="gray",
            font=("Segoe UI", 8),
        )
        path_lbl.pack(anchor="w", pady=(4, 0))

        act = ttk.Frame(frm)
        act.pack(fill="x", pady=(8, 0))
        ttk.Button(act, text="Open in Notepad", command=self._open_notepad).pack(side="left")
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Save", command=self._save).pack(side="right", padx=4)

        self.bind("<Escape>", lambda _e: self.destroy())

    def _save(self) -> None:
        lines = [
            ln.strip()
            for ln in self.txt.get("1.0", "end").splitlines()
            if ln.strip()
        ]
        _save_trackers(lines)
        self._result = lines
        self.destroy()

    def _open_notepad(self) -> None:
        if not TRACKERS_FILE.exists():
            _save_trackers(_load_trackers())
        try:
            subprocess.Popen(["notepad.exe", str(TRACKERS_FILE)])
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"Could not open Notepad:\n{e}")

    def get_result(self) -> list[str] | None:
        return self._result


# ── Create torrent ─────────────────────────────────────────────────────────────


class CreateTorrentDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Create torrent file")
        self.config_obj = config
        self.transient(parent)
        self.grab_set()
        self.geometry("660x590")
        self.minsize(560, 520)

        self._cancel_evt = threading.Event()
        self._trackers: list[str] = _load_trackers()
        self._comment_expanded = False
        self._log_expanded = False

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ── Source ──────────────────────────────────────────────────────
        src = ttk.LabelFrame(frm, text="Source to create torrent from", padding=8)
        src.pack(fill="x")
        src.columnconfigure(1, weight=1)

        self.var_src_type = tk.StringVar(value=config.get("torrent_src_type", "directory"))

        ttk.Radiobutton(
            src, text="Single file:", variable=self.var_src_type,
            value="file", command=self._on_src_type_change,
        ).grid(row=0, column=0, sticky="w", pady=2)
        self.var_src_file = tk.StringVar()
        self.ent_src_file = ttk.Entry(src, textvariable=self.var_src_file)
        self.ent_src_file.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=2)

        ttk.Radiobutton(
            src, text="Directory:", variable=self.var_src_type,
            value="directory", command=self._on_src_type_change,
        ).grid(row=1, column=0, sticky="w", pady=2)
        self.var_src_dir = tk.StringVar()
        self.ent_src_dir = ttk.Entry(src, textvariable=self.var_src_dir)
        self.ent_src_dir.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=2)

        self.btn_browse_src = ttk.Button(src, text="Browse…", width=9, command=self._browse_src)
        self.btn_browse_src.grid(row=0, column=2, rowspan=2, padx=(8, 0), sticky="ns")

        # Update suggestion whenever source path changes
        self.var_src_file.trace_add("write", lambda *_: self._suggest_all())
        self.var_src_dir.trace_add("write",  lambda *_: self._suggest_all())

        # ── Torrent properties ───────────────────────────────────────────
        props = ttk.LabelFrame(frm, text="Torrent properties", padding=8)
        props.pack(fill="x", pady=(8, 0))
        props.columnconfigure(1, weight=1)

        # Downloads as folder
        ttk.Label(props, text="Downloads as folder:").grid(
            row=0, column=0, sticky="w", pady=3)
        self.var_folder_name = tk.StringVar()
        ttk.Entry(props, textvariable=self.var_folder_name).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=3)

        # Peers source + Private checkbox
        ttk.Label(props, text="Peers source:").grid(row=1, column=0, sticky="w", pady=3)
        pr = ttk.Frame(props)
        pr.grid(row=1, column=1, columnspan=2, sticky="w", pady=3)
        self.var_peers = tk.StringVar(
            value=config.get("torrent_peers_source", "single tracker"))
        self.cmb_peers = ttk.Combobox(
            pr, textvariable=self.var_peers, state="readonly", width=16,
            values=["single tracker", "multi-tracker", "trackerless (DHT)"],
        )
        self.cmb_peers.pack(side="left")
        self.cmb_peers.bind("<<ComboboxSelected>>", lambda _e: self._on_peers_change())
        self.var_private = tk.BooleanVar(value=config.get("torrent_private", False))
        ttk.Checkbutton(pr, text="Private torrent", variable=self.var_private).pack(
            side="left", padx=(14, 0))

        # Tracker / Announce
        ttk.Label(props, text="Tracker / Announce:").grid(row=2, column=0, sticky="w", pady=3)
        tr_row = ttk.Frame(props)
        tr_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        tr_row.columnconfigure(0, weight=1)
        self.var_tracker = tk.StringVar()
        self.cmb_tracker = ttk.Combobox(tr_row, textvariable=self.var_tracker,
                                         values=self._trackers)
        self.cmb_tracker.grid(row=0, column=0, sticky="ew")
        if self._trackers:
            self.cmb_tracker.current(0)
        ttk.Button(tr_row, text="Edit…", width=7, command=self._edit_trackers).grid(
            row=0, column=1, padx=(6, 0))

        # Piece size
        ttk.Label(props, text="Piece size:").grid(row=3, column=0, sticky="w", pady=3)
        self.var_piece = tk.StringVar(
            value=config.get("torrent_piece_size_label", "Auto"))
        ttk.Combobox(
            props, textvariable=self.var_piece, state="readonly", width=14,
            values=[lbl for lbl, _ in tr.PIECE_SIZE_CHOICES],
        ).grid(row=3, column=1, sticky="w", padx=(6, 0), pady=3)

        # Comment + Expand toggle
        ttk.Label(props, text="Comment (optional):").grid(row=4, column=0, sticky="nw", pady=3)
        cmt_row = ttk.Frame(props)
        cmt_row.grid(row=4, column=1, columnspan=2, sticky="ew", pady=3)
        cmt_row.columnconfigure(0, weight=1)
        self.var_comment = tk.StringVar()
        self.ent_comment = ttk.Entry(cmt_row, textvariable=self.var_comment)
        self.ent_comment.grid(row=0, column=0, sticky="ew")
        self.btn_expand_cmt = ttk.Button(cmt_row, text="Expand", width=7,
                                          command=self._toggle_comment)
        self.btn_expand_cmt.grid(row=0, column=1, padx=(6, 0))
        self.txt_comment = tk.Text(cmt_row, height=3, font=("Segoe UI", 9), wrap="word")

        # ── Torrent file to create ────────────────────────────────────────
        out_frame = ttk.LabelFrame(frm, text="Torrent file to create", padding=8)
        out_frame.pack(fill="x", pady=(8, 0))
        out_frame.columnconfigure(0, weight=1)
        self.var_outfile = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.var_outfile).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(out_frame, text="Browse…", width=9, command=self._pick_output).grid(
            row=0, column=1, padx=(6, 0))

        # ── Process log ───────────────────────────────────────────────────
        log_lf = ttk.LabelFrame(frm, text="Process log", padding=4)
        log_lf.pack(fill="both", expand=True, pady=(8, 0))
        log_lf.columnconfigure(0, weight=1)
        log_lf.rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_lf, height=5, font=("Consolas", 8), wrap="none",
            background="#101418", foreground="#d8d8d8",
        )
        log_ys = ttk.Scrollbar(log_lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_ys.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_ys.grid(row=0, column=1, sticky="ns")

        btn_stack = ttk.Frame(log_lf)
        btn_stack.grid(row=0, column=2, sticky="n", padx=(8, 4), pady=4)
        self.btn_start = ttk.Button(btn_stack, text="Create", width=8, command=self._start)
        self.btn_start.pack(pady=(0, 4))
        self.btn_stop = ttk.Button(btn_stack, text="Cancel", width=8, command=self._cancel)
        self.btn_stop.pack()

        # ── Overall progress ──────────────────────────────────────────────
        prog_row = ttk.Frame(frm)
        prog_row.pack(fill="x", pady=(6, 0))
        ttk.Label(prog_row, text="Overall progress:").pack(side="left")
        self.pbar = ttk.Progressbar(prog_row, mode="determinate")
        self.pbar.pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(prog_row, text="Expand", width=7, command=self._toggle_log).pack(side="right")

        # ── Initial state ─────────────────────────────────────────────────
        self._on_peers_change()

        if initial_files:
            paths = [Path(f) for f in initial_files]
            if len(paths) == 1:
                p = paths[0]
                if p.is_dir():
                    self.var_src_type.set("directory")
                    self.var_src_dir.set(str(p))
                else:
                    self.var_src_type.set("file")
                    self.var_src_file.set(str(p))
            else:
                common = paths[0].parent
                if all(Path(f).parent == common for f in initial_files):
                    self.var_src_type.set("directory")
                    self.var_src_dir.set(str(common))
                else:
                    self.var_src_type.set("directory")
                    self.var_src_dir.set(str(common))
            self._on_src_type_change()
            self._suggest_all()

    # ── Source helpers ───────────────────────────────────────────────────

    def _on_src_type_change(self) -> None:
        pass  # visual state unchanged; browse button adapts at click time

    def _browse_src(self) -> None:
        if self.var_src_type.get() == "file":
            p = filedialog.askopenfilename(
                parent=self, title="Select source file",
                initialdir=self.config_obj.get("last_input_dir") or None,
            )
            if p:
                self.var_src_file.set(p)
                self.config_obj["last_input_dir"] = str(Path(p).parent)
                self.config_obj.save()
        else:
            d = filedialog.askdirectory(
                parent=self, title="Select source folder",
                initialdir=self.config_obj.get("last_input_dir") or None,
            )
            if d:
                self.var_src_dir.set(d)
                self.config_obj["last_input_dir"] = d
                self.config_obj.save()

    def _active_source(self) -> str:
        if self.var_src_type.get() == "file":
            return self.var_src_file.get().strip()
        return self.var_src_dir.get().strip()

    def _suggest_all(self) -> None:
        s = self._active_source()
        if not s:
            return
        p = Path(s)
        if not self.var_folder_name.get():
            self.var_folder_name.set(p.name)
        if not self.var_outfile.get():
            out = p.parent / f"{p.name}.torrent"
            self.var_outfile.set(str(out))

    # ── Peers / tracker state ────────────────────────────────────────────

    def _on_peers_change(self) -> None:
        mode = self.var_peers.get()
        if mode == "single tracker":
            self.cmb_tracker.configure(state="normal")
        else:
            self.cmb_tracker.configure(state="disabled")

    def _edit_trackers(self) -> None:
        dlg = TrackerEditDialog(self)
        self.wait_window(dlg)
        result = dlg.get_result()
        if result is not None:
            self._trackers = result
            self.cmb_tracker.configure(values=result)
            if result:
                self.cmb_tracker.current(0)
            else:
                self.var_tracker.set("")

    # ── Comment expand / collapse ────────────────────────────────────────

    def _toggle_comment(self) -> None:
        if not self._comment_expanded:
            text = self.var_comment.get()
            self.ent_comment.grid_remove()
            self.txt_comment.grid(row=0, column=0, sticky="ew", pady=(0, 0))
            self.txt_comment.delete("1.0", "end")
            if text:
                self.txt_comment.insert("1.0", text)
            self.btn_expand_cmt.configure(text="Collapse")
            self._comment_expanded = True
        else:
            text = self.txt_comment.get("1.0", "end").strip()
            self.txt_comment.grid_remove()
            self.ent_comment.grid(row=0, column=0, sticky="ew")
            self.var_comment.set(text)
            self.btn_expand_cmt.configure(text="Expand")
            self._comment_expanded = False

    def _get_comment(self) -> str:
        if self._comment_expanded:
            return self.txt_comment.get("1.0", "end").strip()
        return self.var_comment.get().strip()

    # ── Log expand / collapse ────────────────────────────────────────────

    def _toggle_log(self) -> None:
        if not self._log_expanded:
            self.log_text.configure(height=14)
            self._log_expanded = True
        else:
            self.log_text.configure(height=5)
            self._log_expanded = False

    # ── Output path ──────────────────────────────────────────────────────

    def _pick_output(self) -> None:
        cur = self.var_outfile.get()
        f = filedialog.asksaveasfilename(
            parent=self, title="Output .torrent file",
            initialdir=str(Path(cur).parent) if cur else None,
            initialfile=Path(cur).name if cur else "",
            defaultextension=".torrent",
            filetypes=[("Torrent files", "*.torrent"), ("All files", "*.*")],
        )
        if f:
            self.var_outfile.set(f)

    # ── Log helpers ───────────────────────────────────────────────────────

    def _log(self, text: str) -> None:
        self.log_text.insert("end", text)
        self.log_text.see("end")

    # ── Start / cancel ────────────────────────────────────────────────────

    def _cancel(self) -> None:
        self._cancel_evt.set()
        self.destroy()

    def _start(self) -> None:
        src_str = self._active_source()
        if not src_str:
            messagebox.showwarning("Trader's Little Jedi", "Choose a source file or folder.")
            return
        source = Path(src_str)
        if not source.exists():
            messagebox.showerror("Trader's Little Jedi", f"Source not found:\n{source}")
            return

        mode = self.var_peers.get()
        if mode == "single tracker":
            tracker_url = self.var_tracker.get().strip()
            if not tracker_url:
                messagebox.showwarning("Trader's Little Jedi", "Select or enter a tracker URL.")
                return
            trackers = [tracker_url]
        elif mode == "multi-tracker":
            trackers = list(self._trackers)
            if not trackers:
                messagebox.showwarning("Trader's Little Jedi",
                                       "No trackers in tracker list.\n"
                                       "Add trackers via Edit… then choose multi-tracker.")
                return
        else:  # trackerless
            trackers = []

        out_str = self.var_outfile.get().strip()
        if not out_str:
            messagebox.showwarning("Trader's Little Jedi", "Choose an output .torrent path.")
            return
        out_p = Path(out_str)

        piece_label = self.var_piece.get()
        piece_size = next((v for lbl, v in tr.PIECE_SIZE_CHOICES if lbl == piece_label), 0)
        comment = self._get_comment()
        private = self.var_private.get()
        folder_name = self.var_folder_name.get().strip() or None

        # Save settings
        self.config_obj["torrent_src_type"] = self.var_src_type.get()
        self.config_obj["torrent_peers_source"] = mode
        self.config_obj["torrent_private"] = private
        self.config_obj["torrent_piece_size_label"] = piece_label
        self.config_obj.save()

        self.btn_start.configure(state="disabled")
        self.pbar.configure(value=0, maximum=100)
        self._cancel_evt.clear()
        self.after(0, lambda: self._log(f"Creating torrent from: {source}\n"))

        threading.Thread(
            target=self._worker,
            args=(source, trackers, piece_size, comment, private, folder_name, out_p),
            daemon=True,
        ).start()

    def _worker(self, source, trackers, piece_size, comment, private, folder_name, out_p):
        last_pct = -1

        def progress(done, total):
            nonlocal last_pct
            pct = int(done * 100 / total) if total else 100
            if pct != last_pct:
                last_pct = pct
                self.after(0, lambda p=pct, d=done, t=total: self._set_progress(p, d, t))

        try:
            data = tr.create_torrent(
                source=source,
                trackers=trackers,
                piece_size=piece_size,
                comment=comment,
                private=private,
                progress=progress,
                cancelled=self._cancel_evt.is_set,
            )
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(data)
            self.after(0, lambda: self._done(True, out_p, None))
        except Exception as e:
            self.after(0, lambda err=str(e): self._done(False, out_p, err))

    def _set_progress(self, pct, done, total):
        self.pbar.configure(value=pct)
        self.after(0, lambda: self._log(
            f"Hashing {tr.human_size(done)} / {tr.human_size(total)}  ({pct}%)\n"
        ))

    def _done(self, ok, out_p, err):
        self.btn_start.configure(state="normal")
        if ok:
            self._log(f"\nDone — {out_p}\n")
            self.pbar.configure(value=100)
            messagebox.showinfo("Trader's Little Jedi", f"Torrent created:\n{out_p}")
            self.destroy()
        else:
            self._log(f"\nERROR: {err}\n")
            messagebox.showerror("Trader's Little Jedi", f"Torrent creation failed:\n{err}")


# ── Check torrent ──────────────────────────────────────────────────────────────


class CheckTorrentDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Check torrent")
        self.config_obj = config
        self.transient(parent)
        self.grab_set()
        self.geometry("760x560")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text=".torrent file:").pack(side="left")
        self.var_path = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="…", width=3, command=self._pick).pack(side="left")
        ttk.Button(top, text="Inspect", command=self._inspect).pack(side="left", padx=4)

        info = ttk.LabelFrame(frm, text="Info", padding=8)
        info.pack(fill="x", pady=(8, 0))
        self.info_text = tk.Text(info, height=10, font=("Consolas", 9), wrap="none")
        self.info_text.pack(fill="both", expand=True)
        self.info_text.configure(state="disabled")

        files = ttk.LabelFrame(frm, text="Files", padding=4)
        files.pack(fill="both", expand=True, pady=(8, 0))
        self.tv = ttk.Treeview(files, columns=("path", "size"), show="headings", height=8)
        self.tv.heading("path", text="Path")
        self.tv.heading("size", text="Size")
        self.tv.column("path", width=520, anchor="w")
        self.tv.column("size", width=120, anchor="e")
        self.tv.pack(fill="both", expand=True)

        act = ttk.Frame(frm)
        act.pack(fill="x", pady=(8, 0))
        ttk.Button(act, text="Close", command=self.destroy).pack(side="right")

        if initial_files:
            for f in initial_files:
                if Path(f).suffix.lower() == ".torrent":
                    self.var_path.set(f)
                    self.after(50, self._inspect)
                    break

    def _pick(self):
        f = filedialog.askopenfilename(
            parent=self, title="Select .torrent",
            filetypes=[("Torrent files", "*.torrent"), ("All files", "*.*")],
        )
        if f:
            self.var_path.set(f)
            self._inspect()

    def _inspect(self):
        p = Path(self.var_path.get().strip())
        if not p.is_file():
            messagebox.showwarning("Trader's Little Jedi", "Pick an existing .torrent file.")
            return
        try:
            info = tr.inspect_torrent(p)
        except Exception as e:
            messagebox.showerror("Trader's Little Jedi", f"Could not parse: {e}")
            return

        created = info["creation_date"]
        when = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created))
            if created else "(unknown)"
        )
        lines = [
            f"name        : {info['name']}",
            f"info-hash   : {info['info_hash']}",
            f"piece size  : {tr.human_size(info['piece_length'])}",
            f"piece count : {info['piece_count']}",
            f"total size  : {tr.human_size(info['total_size'])}",
            f"private     : {'yes' if info['private'] else 'no'}",
            f"created by  : {info['created_by']}",
            f"created     : {when}",
            f"comment     : {info['comment']}",
            "trackers:",
        ]
        for t in info["trackers"]:
            lines.append(f"  - {t}")
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("end", "\n".join(lines))
        self.info_text.configure(state="disabled")

        for r in self.tv.get_children():
            self.tv.delete(r)
        for fe in info["files"]:
            self.tv.insert("", "end", values=(fe["path"], tr.human_size(fe["length"])))
