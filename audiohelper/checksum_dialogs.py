"""Create / Verify checksum dialogs. Pure-Python hashing; metaflac only for .ffp."""

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from . import checksums as ck
from .tools import get_tool


class CreateChecksumDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Create checksum file")
        self.config_obj = config
        self.runner = runner  # unused; we run our own worker thread
        self.transient(parent)
        self.grab_set()
        self.geometry("680x500")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Files to checksum:").grid(row=0, column=0, sticky="w")
        list_frame = ttk.Frame(frm)
        list_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=4)
        self.listbox = tk.Listbox(list_frame, selectmode="extended", activestyle="dotbox")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Button(btn_row, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(btn_row, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(btn_row, text="Clear", command=lambda: self.listbox.delete(0, "end")).pack(side="left", padx=4)

        opt = ttk.LabelFrame(frm, text="Options", padding=8)
        opt.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(opt, text="Format:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_fmt = tk.StringVar(value="md5")
        ttk.Combobox(opt, textvariable=self.var_fmt, state="readonly", width=8,
                     values=list(ck.FORMATS)).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(opt, text="(ffp = MD5 of FLAC audio samples, requires metaflac)",
                  foreground="gray").grid(row=0, column=2, sticky="w", padx=10)

        ttk.Label(opt, text="Output file:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_outfile = tk.StringVar()
        ttk.Entry(opt, textvariable=self.var_outfile, width=46).grid(row=1, column=1, columnspan=2, sticky="ew", padx=6)
        ttk.Button(opt, text="…", width=3, command=self._pick_output).grid(row=1, column=3, sticky="w")
        opt.columnconfigure(2, weight=1)

        self.progress = ttk.Label(frm, text="", anchor="w")
        self.progress.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.pbar = ttk.Progressbar(frm, mode="determinate")
        self.pbar.grid(row=5, column=0, columnspan=4, sticky="ew")

        act = ttk.Frame(frm)
        act.grid(row=6, column=0, columnspan=4, sticky="e", pady=(10, 0))
        self.btn_close = ttk.Button(act, text="Cancel", command=self.destroy)
        self.btn_close.pack(side="right")
        self.btn_start = ttk.Button(act, text="Start", command=self._start)
        self.btn_start.pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if initial_files:
            for f in initial_files:
                if Path(f).is_file():
                    self.listbox.insert("end", f)
            self._suggest_output()

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select files",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
            self._suggest_output()

    def _add_folder(self):
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        for p in sorted(Path(d).rglob("*")):
            if p.is_file():
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._suggest_output()

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _suggest_output(self):
        if self.var_outfile.get() or self.listbox.size() == 0:
            return
        first = Path(self.listbox.get(0))
        parent = first.parent
        base = parent.name or first.stem
        self.var_outfile.set(str(parent / f"{base}.{self.var_fmt.get()}"))

    def _pick_output(self):
        cur = self.var_outfile.get()
        f = filedialog.asksaveasfilename(
            parent=self, title="Output file",
            initialdir=str(Path(cur).parent) if cur else None,
            initialfile=Path(cur).name if cur else "",
            defaultextension=f".{self.var_fmt.get()}",
        )
        if f:
            self.var_outfile.set(f)

    # ---- worker ----

    def _start(self):
        files = [Path(self.listbox.get(i)) for i in range(self.listbox.size())]
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return
        out = self.var_outfile.get().strip()
        if not out:
            messagebox.showwarning("Trader's Little Jedi", "Pick an output filename.")
            return
        out_p = Path(out)
        fmt = self.var_fmt.get()

        metaflac_exe = None
        if fmt == "ffp":
            mf = get_tool("metaflac")
            metaflac_exe = mf.path(self.config_obj)
            if not metaflac_exe.exists():
                messagebox.showerror(
                    "Trader's Little Jedi",
                    "metaflac.exe is required for FFP. Open Tools → Update all CLI tools.",
                )
                return
            non_flac = [p for p in files if p.suffix.lower() != ".flac"]
            if non_flac:
                messagebox.showerror(
                    "Trader's Little Jedi",
                    f"FFP only applies to FLAC files. {len(non_flac)} non-FLAC selected.",
                )
                return

        self.btn_start.state(["disabled"])
        self.pbar.configure(maximum=len(files), value=0)
        threading.Thread(
            target=self._worker,
            args=(files, fmt, out_p, metaflac_exe),
            daemon=True,
        ).start()

    def _worker(self, files: list[Path], fmt: str, out_p: Path, metaflac_exe):
        entries: list[tuple[str, str]] = []
        base = out_p.parent
        try:
            for i, p in enumerate(files, 1):
                self._set_progress(i, len(files), p.name)
                try:
                    rel = str(p.relative_to(base))
                except ValueError:
                    rel = p.name
                rel = rel.replace("\\", "/")
                if fmt == "md5":
                    entries.append((rel, ck.md5sum(p)))
                elif fmt == "sha1":
                    entries.append((rel, ck.sha1sum(p)))
                elif fmt == "sfv":
                    entries.append((rel, ck.crc32(p)))
                elif fmt == "ffp":
                    entries.append((rel, ck.flac_fingerprint(p, metaflac_exe)))
            writer = {
                "md5": ck.write_md5, "sha1": ck.write_sha1,
                "sfv": ck.write_sfv, "ffp": ck.write_ffp,
            }[fmt]
            writer(entries, out_p)
            self.after(0, lambda: self._done(True, out_p, None))
        except Exception as e:
            self.after(0, lambda: self._done(False, out_p, str(e)))

    def _set_progress(self, n: int, total: int, name: str):
        def f():
            self.pbar.configure(value=n)
            self.progress.configure(text=f"[{n}/{total}] {name}")
        self.after(0, f)

    def _done(self, ok: bool, out_p: Path, err: str | None):
        self.btn_start.state(["!disabled"])
        if ok:
            messagebox.showinfo("Trader's Little Jedi", f"Wrote {out_p}")
            self.destroy()
        else:
            messagebox.showerror("Trader's Little Jedi", f"Checksum creation failed:\n{err}")


class VerifyChecksumDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Verify checksum files")
        self.config_obj = config
        self.transient(parent)
        self.grab_set()
        self.geometry("760x520")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="Checksum file:").pack(side="left")
        self.var_chk = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_chk).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="…", width=3, command=self._pick).pack(side="left")
        self.btn_start = ttk.Button(top, text="Verify", command=self._start)
        self.btn_start.pack(side="left", padx=4)

        self.tv = ttk.Treeview(frm, columns=("file", "expected", "actual", "status"),
                               show="headings", height=14)
        for c, w in (("file", 280), ("expected", 160), ("actual", 160), ("status", 80)):
            self.tv.heading(c, text=c.title())
            self.tv.column(c, width=w, anchor="w")
        self.tv.pack(fill="both", expand=True, pady=(8, 4))

        self.status = ttk.Label(frm, text="", anchor="w")
        self.status.pack(fill="x")

        act = ttk.Frame(frm)
        act.pack(fill="x", pady=(8, 0))
        ttk.Button(act, text="Close", command=self.destroy).pack(side="right")

        # Tag rows by status
        self.tv.tag_configure("ok", foreground="#2c7a2c")
        self.tv.tag_configure("fail", foreground="#b03030")
        self.tv.tag_configure("miss", foreground="#9a8400")

        if initial_files:
            for f in initial_files:
                if ck.detect_format(Path(f)):
                    self.var_chk.set(f)
                    break

    def _pick(self):
        f = filedialog.askopenfilename(
            parent=self, title="Select checksum file",
            filetypes=[("Checksum files", "*.md5 *.sha1 *.sfv *.ffp"), ("All files", "*.*")],
        )
        if f:
            self.var_chk.set(f)

    def _start(self):
        chk_path = Path(self.var_chk.get().strip())
        if not chk_path.is_file():
            messagebox.showwarning("Trader's Little Jedi", "Select an existing checksum file first.")
            return
        fmt = ck.detect_format(chk_path)
        if not fmt:
            messagebox.showerror("Trader's Little Jedi",
                                 f"Unrecognized checksum extension: {chk_path.suffix}")
            return

        metaflac_exe = None
        if fmt == "ffp":
            mf = get_tool("metaflac")
            metaflac_exe = mf.path(self.config_obj)
            if not metaflac_exe.exists():
                messagebox.showerror("Trader's Little Jedi",
                                     "metaflac.exe is required to verify FFP fingerprints.")
                return

        parser = {"md5": ck.parse_md5, "sha1": ck.parse_sha1,
                  "sfv": ck.parse_sfv, "ffp": ck.parse_ffp}[fmt]
        entries = parser(chk_path)
        if not entries:
            messagebox.showerror("Trader's Little Jedi", "No entries found in checksum file.")
            return

        for r in self.tv.get_children():
            self.tv.delete(r)
        self.btn_start.state(["disabled"])
        threading.Thread(
            target=self._worker,
            args=(chk_path.parent, fmt, entries, metaflac_exe),
            daemon=True,
        ).start()

    def _worker(self, base_dir: Path, fmt: str, entries: list[tuple[str, str]], metaflac_exe):
        passed = 0
        failed = 0
        missing = 0
        for name, expected in entries:
            target = (base_dir / name).resolve()
            row = self._insert_row(name, expected, "", "")
            if not target.exists():
                self._update_row(row, "", "MISSING", "miss")
                missing += 1
                continue
            try:
                if fmt == "md5":
                    actual = ck.md5sum(target)
                elif fmt == "sha1":
                    actual = ck.sha1sum(target)
                elif fmt == "sfv":
                    actual = ck.crc32(target)
                elif fmt == "ffp":
                    actual = ck.flac_fingerprint(target, metaflac_exe)
                else:
                    actual = ""
            except Exception as e:
                self._update_row(row, "", f"err: {e}", "fail")
                failed += 1
                continue
            ok = actual.lower() == expected.lower()
            if ok:
                self._update_row(row, actual, "OK", "ok")
                passed += 1
            else:
                self._update_row(row, actual, "FAIL", "fail")
                failed += 1
        self.after(0, lambda: self._finish(passed, failed, missing))

    def _insert_row(self, *cols) -> str:
        iid_box = [""]
        evt = threading.Event()
        def f():
            iid_box[0] = self.tv.insert("", "end", values=cols)
            evt.set()
        self.after(0, f)
        evt.wait()
        return iid_box[0]

    def _update_row(self, iid: str, actual: str, status: str, tag: str):
        def f():
            if not self.tv.exists(iid):
                return
            vals = list(self.tv.item(iid, "values"))
            vals[2] = actual
            vals[3] = status
            self.tv.item(iid, values=vals, tags=(tag,))
        self.after(0, f)

    def _finish(self, passed: int, failed: int, missing: int):
        total = passed + failed + missing
        self.status.configure(
            text=f"Done. {passed} passed, {failed} failed, {missing} missing  ({total} total)"
        )
        self.btn_start.state(["!disabled"])
