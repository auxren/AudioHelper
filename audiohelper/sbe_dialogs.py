"""Check / Fix SBE dialogs. Pure-Python WAV parsing; no shntool dependency."""

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from . import wav_sbe as ws


def _is_wav(path: Path) -> bool:
    return path.suffix.lower() in {".wav", ".bwf"}


def _fmt_bytes(n: int) -> str:
    return f"{n:,}"


# -------------------------------------------------------------------
# Check
# -------------------------------------------------------------------

class CheckSbeDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Check audio files for SBEs")
        self.config_obj = config
        self.parent = parent
        self.runner = runner
        self.transient(parent)
        self.geometry("960x540")

        # toolbar
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        ttk.Button(top, text="Fix selected SBEs…",
                   command=self._fix_selected).pack(side="left", padx=20)
        self.btn_check = ttk.Button(top, text="Check", command=self._start)
        self.btn_check.pack(side="right")

        # results
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        cols = ("file", "format", "data bytes", "leftover", "status", "message")
        widths = (340, 130, 110, 80, 80, 200)
        self.tv = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, widths):
            self.tv.heading(c, text=c.title())
            self.tv.column(c, width=w, anchor="w")
        ys = ttk.Scrollbar(body, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("ok", foreground="#2c7a2c")
        self.tv.tag_configure("sbe", foreground="#b03030")
        self.tv.tag_configure("na", foreground="#888")
        self.tv.tag_configure("err", foreground="#b03030")

        self.status = ttk.Label(self, text="Ready", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            for f in initial_files:
                if Path(f).is_file() and _is_wav(Path(f)):
                    self._add_row(f)
            self._update_idle_status()
            self.after(50, self._start)

    def _add_row(self, path: str) -> str:
        return self.tv.insert("", "end", values=(path, "", "", "", "pending", ""))

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select WAV files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("WAV / BWF", ("*.wav", "*.bwf")), ("All files", "*.*")],
        )
        for f in files:
            if _is_wav(Path(f)):
                self._add_row(f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
        self._update_idle_status()

    def _add_folder(self):
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and _is_wav(p):
                self._add_row(str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._update_idle_status()

    def _remove(self):
        for iid in self.tv.selection():
            self.tv.delete(iid)
        self._update_idle_status()

    def _clear(self):
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self._update_idle_status()

    def _update_idle_status(self):
        self.status.configure(text=f"{len(self.tv.get_children())} file(s) queued")

    # ----- check -----

    def _start(self):
        rows = list(self.tv.get_children())
        if not rows:
            messagebox.showwarning("Trader's Little Jedi", "Add some WAV files first.")
            return
        self.btn_check.state(["disabled"])
        threading.Thread(target=self._worker, args=(rows,), daemon=True).start()

    def _worker(self, rows: list[str]):
        ok_n = sbe_n = na_n = err_n = 0
        for i, iid in enumerate(rows, 1):
            path_str = self.tv.item(iid, "values")[0]
            path = Path(path_str)
            self._set_status(f"[{i}/{len(rows)}] {path.name}")
            try:
                info = ws.parse_wav_info(path)
            except Exception as e:
                self._set_row(iid, status="error", message=str(e), tag="err")
                err_n += 1
                continue
            fmt = f'{info["sample_rate"]} Hz / {info["channels"]} ch / {info["bits"]}-bit'
            status, leftover = ws.sbe_status(info)
            if status == "ok":
                self._set_row(iid, fmt=fmt,
                              data_bytes=_fmt_bytes(info["data_size"]),
                              leftover="0",
                              status="OK", message="sector-aligned", tag="ok")
                ok_n += 1
            elif status == "sbe":
                pad = ws.CD_FRAME_BYTES - leftover
                self._set_row(iid, fmt=fmt,
                              data_bytes=_fmt_bytes(info["data_size"]),
                              leftover=str(leftover),
                              status="SBE",
                              message=f"would pad {pad} byte(s) of silence",
                              tag="sbe")
                sbe_n += 1
            else:
                self._set_row(iid, fmt=fmt,
                              data_bytes=_fmt_bytes(info["data_size"]),
                              leftover="—",
                              status="N/A",
                              message="not CD format (16/44.1/stereo)",
                              tag="na")
                na_n += 1
        self.after(0, lambda: self._finish(ok_n, sbe_n, na_n, err_n))

    def _set_row(self, iid: str, fmt: str = "", data_bytes: str = "",
                 leftover: str = "", status: str = "", message: str = "",
                 tag: str | None = None):
        def f():
            if not self.tv.exists(iid):
                return
            vals = list(self.tv.item(iid, "values"))
            if fmt:        vals[1] = fmt
            if data_bytes: vals[2] = data_bytes
            if leftover:   vals[3] = leftover
            if status:     vals[4] = status
            if message:    vals[5] = message
            if tag is not None:
                self.tv.item(iid, values=vals, tags=((tag,) if tag else ()))
            else:
                self.tv.item(iid, values=vals)
        self.after(0, f)

    def _set_status(self, text: str):
        self.after(0, lambda: self.status.configure(text=text))

    def _finish(self, ok_n: int, sbe_n: int, na_n: int, err_n: int):
        self.btn_check.state(["!disabled"])
        total = ok_n + sbe_n + na_n + err_n
        self.status.configure(
            text=f"Done. {ok_n} OK, {sbe_n} SBE, {na_n} N/A, {err_n} error  ({total} total)"
        )

    # ----- launch Fix on selected SBE rows -----

    def _fix_selected(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("Trader's Little Jedi", "Select rows with status 'SBE' first.")
            return
        files = []
        for iid in sel:
            vals = self.tv.item(iid, "values")
            if vals[4] == "SBE":
                files.append(vals[0])
        if not files:
            messagebox.showinfo("Trader's Little Jedi", "No SBE rows in the selection.")
            return
        FixSbeDialog(self.parent, self.config_obj, self.runner, initial_files=files)


# -------------------------------------------------------------------
# Fix
# -------------------------------------------------------------------

class FixSbeDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Fix SBEs")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("960x540")

        # toolbar
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        self.btn_start = ttk.Button(top, text="Fix", command=self._start)
        self.btn_start.pack(side="right")

        # options
        opt = ttk.LabelFrame(self, text="Options", padding=8)
        opt.pack(fill="x", padx=8, pady=(8, 0))
        self.var_in_place = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Replace original files (otherwise write *-sbefix.wav alongside)",
                        variable=self.var_in_place).pack(anchor="w")

        # results
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        cols = ("file", "format", "leftover", "pad added", "status", "message")
        widths = (340, 130, 90, 90, 90, 200)
        self.tv = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, widths):
            self.tv.heading(c, text=c.title())
            self.tv.column(c, width=w, anchor="w")
        ys = ttk.Scrollbar(body, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("ok", foreground="#2c7a2c")
        self.tv.tag_configure("fail", foreground="#b03030")
        self.tv.tag_configure("skip", foreground="#888")

        self.status = ttk.Label(self, text="Ready", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            for f in initial_files:
                if Path(f).is_file() and _is_wav(Path(f)):
                    self._add_row(f)
            self._update_idle_status()

    def _add_row(self, path: str) -> str:
        return self.tv.insert("", "end", values=(path, "", "", "", "pending", ""))

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select WAV files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("WAV / BWF", ("*.wav", "*.bwf")), ("All files", "*.*")],
        )
        for f in files:
            if _is_wav(Path(f)):
                self._add_row(f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
        self._update_idle_status()

    def _add_folder(self):
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and _is_wav(p):
                self._add_row(str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._update_idle_status()

    def _remove(self):
        for iid in self.tv.selection():
            self.tv.delete(iid)
        self._update_idle_status()

    def _clear(self):
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self._update_idle_status()

    def _update_idle_status(self):
        self.status.configure(text=f"{len(self.tv.get_children())} file(s) queued")

    # ----- fix -----

    def _start(self):
        rows = list(self.tv.get_children())
        if not rows:
            messagebox.showwarning("Trader's Little Jedi", "Add some WAV files first.")
            return
        in_place = self.var_in_place.get()
        if in_place:
            confirm = messagebox.askyesno(
                "Trader's Little Jedi",
                f"This will overwrite {len(rows)} file(s) in place by appending silence.\n\n"
                "Proceed?",
                default="no",
            )
            if not confirm:
                return
        self.btn_start.state(["disabled"])
        threading.Thread(target=self._worker, args=(rows, in_place), daemon=True).start()

    def _worker(self, rows: list[str], in_place: bool):
        fixed = skipped = failed = 0
        for i, iid in enumerate(rows, 1):
            path_str = self.tv.item(iid, "values")[0]
            path = Path(path_str)
            self._set_status(f"[{i}/{len(rows)}] {path.name}")
            try:
                info = ws.parse_wav_info(path)
            except Exception as e:
                self._set_row(iid, status="FAIL", message=str(e), tag="fail")
                failed += 1
                continue
            fmt = f'{info["sample_rate"]} Hz / {info["channels"]} ch / {info["bits"]}-bit'
            status, leftover = ws.sbe_status(info)
            self._set_row(iid, fmt=fmt, leftover=str(leftover))
            if status == "ok":
                self._set_row(iid, status="skipped", message="already aligned", tag="skip")
                skipped += 1
                continue
            if status == "na":
                self._set_row(iid, status="skipped",
                              message="not CD format (16/44.1/stereo)", tag="skip")
                skipped += 1
                continue
            # status == 'sbe'
            try:
                if in_place:
                    pad = ws.fix_sbe_in_place(path, info)
                    self._set_row(iid, status="OK",
                                  message="replaced in place",
                                  fix_pad=str(pad), tag="ok")
                else:
                    dst = path.with_name(path.stem + "-sbefix" + path.suffix)
                    pad = ws.fix_sbe_to(path, dst, info)
                    self._set_row(iid, status="OK",
                                  message=f"wrote {dst.name}",
                                  fix_pad=str(pad), tag="ok")
                fixed += 1
            except Exception as e:
                self._set_row(iid, status="FAIL", message=str(e), tag="fail")
                failed += 1
        self.after(0, lambda: self._finish(fixed, skipped, failed))

    def _set_row(self, iid: str, fmt: str = "", leftover: str = "",
                 fix_pad: str = "", status: str = "", message: str = "",
                 tag: str | None = None):
        def f():
            if not self.tv.exists(iid):
                return
            vals = list(self.tv.item(iid, "values"))
            if fmt:       vals[1] = fmt
            if leftover:  vals[2] = leftover
            if fix_pad:   vals[3] = fix_pad
            if status:    vals[4] = status
            if message:   vals[5] = message
            if tag is not None:
                self.tv.item(iid, values=vals, tags=((tag,) if tag else ()))
            else:
                self.tv.item(iid, values=vals)
        self.after(0, f)

    def _set_status(self, text: str):
        self.after(0, lambda: self.status.configure(text=text))

    def _finish(self, fixed: int, skipped: int, failed: int):
        self.btn_start.state(["!disabled"])
        total = fixed + skipped + failed
        self.status.configure(
            text=f"Done. {fixed} fixed, {skipped} skipped, {failed} failed  ({total} total)"
        )
