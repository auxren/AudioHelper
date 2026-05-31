"""Upload a finished show folder to the Internet Archive Live Music Archive.

Uses the official `internetarchive` library (optional dependency). Builds the
etree-collection metadata from the show data, lets the user review everything,
and uploads after an explicit confirmation. Only approved LMA uploaders can
write to the `etree` collection.

Files uploaded: the FLACs, the setlist .txt, FFP/MD5 checksums, and cover image
in the folder. Torrents are skipped (archive.org generates its own derivatives
and torrent).
"""

import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import theme as _t
from .action_picker import AUDIO_EXTS
from .tc_sources import detect_source


def internetarchive_available() -> bool:
    try:
        import internetarchive  # noqa: F401
        return True
    except ImportError:
        return False


# Files in a show folder that should NOT be uploaded.
_SKIP_SUFFIXES = {".torrent", ".tljsplit", ".ds_store"}


def _uploadable_files(folder: Path) -> list[Path]:
    out = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.suffix.lower() in _SKIP_SUFFIXES:
            continue
        out.append(p)
    return out


def make_identifier(abbrev: str, date: str, source: str) -> str:
    """Suggest an LMA-style identifier: {abbrev}{date}.{equipment}.flac16.

    The user can (and often must) adjust this to match LMA conventions and to
    be globally unique. Lowercased; only [a-z0-9._-] kept.
    """
    parts = [f"{abbrev}{date}".strip()]
    src = detect_source(source)
    tag = ""
    if src.mics:
        tag = re.sub(r"[^a-z0-9]", "", src.mics[0].lower())
    elif src.kind:
        tag = src.kind.lower()
    if tag:
        parts.append(tag)
    parts.append("flac16")
    ident = ".".join(p for p in parts if p)
    ident = re.sub(r"[^a-zA-Z0-9._-]", "", ident).lower()
    return ident or "show"


def build_metadata(meta: dict) -> dict:
    """Assemble the archive.org etree metadata dict from show fields."""
    creator = meta.get("artist", "").strip()
    date = meta.get("date", "").strip()
    venue = meta.get("venue", "").strip()
    coverage = meta.get("location", "").strip()
    title_bits = [creator]
    if venue:
        title_bits.append(f"Live at {venue}")
    if date:
        title_bits.append(f"on {date}")
    md = {
        "collection": "etree",
        "mediatype": "etree",
        "title": " ".join(b for b in title_bits if b).strip() or (creator or date),
        "creator": creator,
        "date": date,
        "venue": venue,
        "coverage": coverage,
        "source": meta.get("source", "").strip(),
        "taper": meta.get("taper", "").strip(),
        "notes": meta.get("notes", "").strip(),
        "description": meta.get("description", "").strip(),
    }
    subj = meta.get("subject", "").strip()
    if subj:
        md["subject"] = [s.strip() for s in subj.split(",") if s.strip()]
    elif creator:
        md["subject"] = [creator, "Live concert"]
    # Drop empties so we don't post blank fields.
    return {k: v for k, v in md.items() if v}


# ── Dialog ────────────────────────────────────────────────────────────────────

class LmaUploadDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, folder: Optional[Path] = None,
                 meta: Optional[dict] = None):
        super().__init__(parent)
        self.title("Upload to Archive.org (Live Music Archive)")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("760x720")
        self.minsize(680, 600)
        _t.apply(self)
        self.configure(bg=_t.BG_DEEP)

        self._folder = Path(folder) if folder else None
        meta = meta or {}

        self._build(meta)
        if self._folder:
            self._refresh_files()

    def _build(self, meta: dict) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        if not internetarchive_available():
            warn = ttk.Label(
                outer, style="Bold.TLabel",
                text="⚠  The 'internetarchive' library isn't installed.\n"
                     "Install it to enable uploads:  pip install internetarchive")
            warn.pack(anchor="w", pady=(0, 8))

        # ── Folder ────────────────────────────────────────────────────────────
        fr = ttk.LabelFrame(outer, text="Show folder to upload", padding=8)
        fr.pack(fill="x")
        self.var_folder = tk.StringVar(value=str(self._folder or ""))
        row = ttk.Frame(fr); row.pack(fill="x")
        ttk.Entry(row, textvariable=self.var_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=self._browse_folder).pack(side="left", padx=(4, 0))
        self.lbl_files = ttk.Label(fr, text="", style="Dim.TLabel")
        self.lbl_files.pack(anchor="w", pady=(4, 0))

        # ── Identifier ────────────────────────────────────────────────────────
        idf = ttk.LabelFrame(outer, text="Archive.org identifier (must be unique & follow LMA rules)", padding=8)
        idf.pack(fill="x", pady=(8, 0))
        suggested = make_identifier(
            meta.get("abbrev", "") or "", meta.get("date", ""), meta.get("source", ""))
        self.var_ident = tk.StringVar(value=suggested)
        ttk.Entry(idf, textvariable=self.var_ident).pack(fill="x")
        ttk.Label(idf, style="Dim.TLabel",
                  text="Lowercase letters/numbers/dots/dashes. Becomes "
                       "archive.org/details/<identifier>.").pack(anchor="w", pady=(2, 0))

        # ── Metadata ──────────────────────────────────────────────────────────
        mf = ttk.LabelFrame(outer, text="Metadata", padding=8)
        mf.pack(fill="both", expand=True, pady=(8, 0))
        mf.columnconfigure(1, weight=1)
        self._mvars: dict[str, tk.StringVar] = {}
        fields = [
            ("Artist (creator)", "artist"),
            ("Date (YYYY-MM-DD)", "date"),
            ("Venue", "venue"),
            ("Location (coverage)", "location"),
            ("Source / lineage", "source"),
            ("Taper", "taper"),
            ("Subject tags (comma-sep)", "subject"),
        ]
        for r, (label, key) in enumerate(fields):
            ttk.Label(mf, text=f"{label}:").grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=meta.get(key, ""))
            self._mvars[key] = v
            ttk.Entry(mf, textvariable=v).grid(row=r, column=1, sticky="ew", padx=(6, 0), pady=2)
        ttk.Label(mf, text="Notes / description:").grid(
            row=len(fields), column=0, sticky="nw", pady=2)
        self.txt_desc = tk.Text(mf, height=5, wrap="word")
        _t.style_log(self.txt_desc)  # dark styling; reuse
        self.txt_desc.configure(font=("Segoe UI", 10))
        self.txt_desc.grid(row=len(fields), column=1, sticky="ew", padx=(6, 0), pady=2)
        # Prefill description with notes + setlist if provided
        prefill = meta.get("description") or meta.get("notes") or ""
        if prefill:
            self.txt_desc.insert("1.0", prefill)

        # ── Credentials ───────────────────────────────────────────────────────
        cf = ttk.LabelFrame(outer, text="Archive.org S3 keys (archive.org/account/s3.php)", padding=8)
        cf.pack(fill="x", pady=(8, 0))
        cf.columnconfigure(1, weight=1)
        self.var_ak = tk.StringVar(value=self.config_obj.get("ia_access_key", ""))
        self.var_sk = tk.StringVar(value=self.config_obj.get("ia_secret_key", ""))
        ttk.Label(cf, text="Access key:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(cf, textvariable=self.var_ak).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(cf, text="Secret key:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(cf, textvariable=self.var_sk, show="•").grid(row=1, column=1, sticky="ew", padx=(6, 0))
        self.var_remember = tk.BooleanVar(value=bool(self.config_obj.get("ia_remember", False)))
        ttk.Checkbutton(cf, text="Remember keys on this computer (stored in config.json)",
                        variable=self.var_remember).grid(row=2, column=1, sticky="w", pady=(4, 0))

        # ── Actions ───────────────────────────────────────────────────────────
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.btn_upload = ttk.Button(bar, text="Upload to Archive.org",
                                     style="Action.TButton", command=self._confirm_upload)
        self.btn_upload.pack(side="right", padx=4)

        self.status = ttk.Label(self, text="Review the metadata, then upload.",
                                anchor="w", style="Status.TLabel")
        self.status.pack(fill="x", side="bottom")

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Select prepared show folder",
                                    initialdir=self.var_folder.get() or None)
        if d:
            self.var_folder.set(d)
            self._folder = Path(d)
            self._refresh_files()

    def _refresh_files(self) -> None:
        self._folder = Path(self.var_folder.get())
        if not self._folder.is_dir():
            self.lbl_files.configure(text="(folder not found)")
            return
        files = _uploadable_files(self._folder)
        n_audio = sum(1 for f in files if f.suffix.lower() in AUDIO_EXTS)
        total = sum(f.stat().st_size for f in files)
        mb = total / (1024 * 1024)
        self.lbl_files.configure(
            text=f"{len(files)} file(s) to upload — {n_audio} audio, {mb:.1f} MB "
                 "(torrents and session files excluded)")

    def _gather_meta(self) -> dict:
        m = {k: v.get().strip() for k, v in self._mvars.items()}
        m["description"] = self.txt_desc.get("1.0", "end").strip()
        return m

    # ── Upload ──────────────────────────────────────────────────────────────

    def _confirm_upload(self) -> None:
        if not internetarchive_available():
            messagebox.showerror(
                "Upload", "The 'internetarchive' library is required.\n\n"
                          "Install it with:  pip install internetarchive")
            return
        self._refresh_files()
        if not self._folder or not self._folder.is_dir():
            messagebox.showerror("Upload", "Choose a valid show folder first.")
            return
        files = _uploadable_files(self._folder)
        if not files:
            messagebox.showerror("Upload", "No uploadable files in that folder.")
            return
        ident = self.var_ident.get().strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,}", ident):
            messagebox.showerror("Upload",
                                 "Identifier must be lowercase letters/numbers/dots/dashes "
                                 "(at least 3 chars).")
            return
        ak, sk = self.var_ak.get().strip(), self.var_sk.get().strip()
        if not ak or not sk:
            messagebox.showerror("Upload",
                                 "Enter your Archive.org S3 access key and secret key.\n"
                                 "Get them at archive.org/account/s3.php")
            return

        url = f"https://archive.org/details/{ident}"
        if not messagebox.askyesno(
                "Confirm public upload",
                f"This will PUBLICLY upload {len(files)} file(s) to the Live Music "
                f"Archive (etree collection) as:\n\n  {url}\n\n"
                "Once uploaded it is public and indexed. Only proceed for "
                "trade-friendly bands you're approved to post. Continue?",
                icon="warning"):
            return

        # Persist keys if requested
        self.config_obj["ia_remember"] = bool(self.var_remember.get())
        if self.var_remember.get():
            self.config_obj["ia_access_key"] = ak
            self.config_obj["ia_secret_key"] = sk
        else:
            self.config_obj["ia_access_key"] = ""
            self.config_obj["ia_secret_key"] = ""
        self.config_obj.save()

        meta = self._gather_meta()
        md = build_metadata(meta)
        self.btn_upload.configure(state="disabled")
        self.progress.start(12)
        self.status.configure(text=f"Uploading to {url} …")
        threading.Thread(target=self._upload_worker,
                         args=(ident, files, md, ak, sk, url), daemon=True).start()

    def _upload_worker(self, ident, files, md, ak, sk, url) -> None:
        try:
            import internetarchive as ia
            responses = ia.upload(
                ident, files=[str(f) for f in files], metadata=md,
                access_key=ak, secret_key=sk, verbose=False,
                retries=3, retries_sleep=5,
            )
            bad = [r for r in responses if getattr(r, "status_code", 200) not in (200, None)]
            if bad:
                detail = "; ".join(str(getattr(r, "status_code", "?")) for r in bad)
                self.after(0, lambda: self._done(False, f"Some files failed (HTTP {detail})", url))
            else:
                self.after(0, lambda: self._done(True, "", url))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self._done(False, msg, url))

    def _done(self, ok: bool, error: str, url: str) -> None:
        self.progress.stop()
        self.btn_upload.configure(state="normal")
        if ok:
            self.status.configure(text=f"✓ Uploaded — {url}")
            if messagebox.askyesno("Upload complete",
                                   f"Uploaded successfully.\n\n{url}\n\n"
                                   "Archive.org is now deriving files (can take a while).\n"
                                   "Open the page in your browser?"):
                import webbrowser
                webbrowser.open(url)
        else:
            self.status.configure(text=f"Upload failed: {error[:80]}")
            messagebox.showerror("Upload failed",
                                 f"{error}\n\nCommon causes: wrong S3 keys, an identifier "
                                 "that's already taken, or not being approved for the "
                                 "etree (LMA) collection.")
