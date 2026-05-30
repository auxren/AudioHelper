import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from . import __version__
from .config import Config
from .runner import JobRunner
from .tools import get_tool
from .encode_wav import EncodeWavDialog
from .reencode_flac import ReencodeFlacDialog
from .merge import MergeDialog
from .decode import DecodeDialog
from .audio_editor import AudioEditorDialog
from .dsd_edit import DsdEditDialog
from .checksum_dialogs import CreateChecksumDialog, VerifyChecksumDialog
from .torrent_dialogs import CreateTorrentDialog, CheckTorrentDialog
from .details import ShowDetailsDialog
from .test_encoded import TestEncodedDialog
from .repair_audio import RepairAudioDialog
from .wav_mpeg import TestMpegDialog
from .sbe_dialogs import CheckSbeDialog, FixSbeDialog
from .convert import ConvertDialog
from .strip_header import StripHeaderDialog
from .jedi_tagger import JediTaggerDialog
from .show_splitter import ShowSplitterDialog
from .replaygain import ReplayGainDialog
from .batch_rename import BatchRenameDialog
from .prefs import PreferencesDialog
from .update_tools import UpdateToolsDialog
from .action_picker import ActionPickerDialog

# Optional drag-and-drop support via tkinterdnd2. App stays functional if absent.
_DND_REASON = ""
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore
    _DND_AVAILABLE = True
    _BaseTk = TkinterDnD.Tk
except Exception:
    _DND_AVAILABLE = False
    _DND_REASON = "not installed"
    DND_FILES = None  # type: ignore
    _BaseTk = tk.Tk  # type: ignore


class MainWindow(_BaseTk):  # type: ignore[misc,valid-type]
    def __init__(self):
        global _DND_AVAILABLE
        try:
            super().__init__()
        except RuntimeError:
            # tkdnd native library imported fine but unusable at runtime
            # (common on Apple Silicon with certain Python/Tk builds).
            # Fall back silently to plain tk.Tk.
            _DND_AVAILABLE = False
            _DND_REASON = "native tkdnd library failed to load (Apple Silicon + Python 3.14 known issue)"
            tk.Tk.__init__(self)
        self.title("Trader's Little Jedi")
        self.config_obj = Config()
        self.geometry(self.config_obj.get("window_geometry", "820x520"))
        self.minsize(640, 360)

        self._build_menu()
        self._build_body()

        self.runner = JobRunner(self._append_log_threadsafe, self._on_job_done)

        # Check prerequisites after the window is visible
        if not self.config_obj.get("skip_prereq_check", False):
            self.after(400, self._check_prereqs)

        if _DND_AVAILABLE:
            self._register_dnd()
            self.append_log("Drag-and-drop enabled. Drop files or folders onto this window.\n")
        else:
            if _DND_REASON == "not installed":
                self.append_log(
                    "Drag-and-drop unavailable (tkinterdnd2 not installed).\n"
                    "  Run the installer or:  pip install tkinterdnd2\n"
                )
            else:
                self.append_log(f"Drag-and-drop unavailable ({_DND_REASON}).\n"
                                "  Use File menus and the Add buttons instead.\n")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- layout -----

    def _build_menu(self) -> None:
        mb = tk.Menu(self)

        fmt = tk.Menu(mb, tearoff=0)
        fmt.add_command(label="Encode wav files", command=self._open_encode_wav)
        fmt.add_command(label="Re-encode flac files", command=self._open_reencode_flac)
        fmt.add_command(label="Decode audio files", command=self._open_decode)
        fmt.add_separator()
        fmt.add_command(label="Convert encoding format", command=self._open_convert)
        fmt.add_separator()
        fmt.add_command(label="Merge audio files (gapless)…", command=self._open_merge)
        fmt.add_separator()
        fmt.add_command(label="Audio editor / split / track out…", command=self._open_audio_editor)
        fmt.add_separator()
        fmt.add_command(label="Exit", accelerator="Alt+F4", command=self._on_close)
        mb.add_cascade(label="Format", menu=fmt)

        chk = tk.Menu(mb, tearoff=0)
        chk.add_command(label="Create checksum file", command=self._open_create_chk)
        chk.add_command(label="Verify checksum files", command=self._open_verify_chk)
        mb.add_cascade(label="Checksum", menu=chk)

        tor = tk.Menu(mb, tearoff=0)
        tor.add_command(label="Create torrent file", command=self._open_create_torrent)
        tor.add_command(label="Check torrent", command=self._open_check_torrent)
        mb.add_cascade(label="Torrent", menu=tor)

        ana = tk.Menu(mb, tearoff=0)
        ana.add_command(label="Show file details (MediaInfo)…", command=self._open_details)
        ana.add_command(label="Test encoded audio files", command=self._open_test_encoded)
        ana.add_command(label="Check audio files for SBEs", command=self._open_check_sbe)
        ana.add_command(label="Test wav files for MPEG", command=self._open_test_mpeg)
        ana.add_separator()
        ana.add_command(label="Repair FLAC / WAV files…", command=self._open_repair)
        ana.add_separator()
        ana.add_command(label="Scan ReplayGain…", command=self._open_replaygain)
        mb.add_cascade(label="Analysis", menu=ana)

        tls = tk.Menu(mb, tearoff=0, postcommand=self._refresh_tools_menu)
        tls.add_command(label="Fix SBEs", command=self._open_fix_sbe)
        tls.add_command(label="Strip audio file header / metadata", command=self._open_strip_header)
        tls.add_command(label="Live Show Tagger…",             command=self._open_jedi_tagger)
        tls.add_command(label="Show Splitter (WAV → tracks)…", command=self._open_show_splitter)
        tls.add_command(label="Batch rename files…",          command=self._open_batch_rename)
        tls.add_separator()
        tls.add_command(label="Launch TASCAM Hi-Res Editor", command=self._launch_tascam)
        tls.add_command(label="Launch KORG AudioGate", command=self._launch_audiogate)
        tls.add_command(label="Launch Spek…", command=self._launch_spek)
        tls.add_command(label="Launch xrecode3…", command=self._launch_xrecode)
        tls.add_command(label="Launch Bulk Rename Utility…", command=self._launch_bru)
        tls.add_separator()
        tls.add_command(label="Update all CLI tools…", command=self._open_update_tools)
        self._tools_menu = tls
        mb.add_cascade(label="Tools", menu=tls)

        opt = tk.Menu(mb, tearoff=0)
        opt.add_command(label="Preferences…", command=self._open_prefs)
        mb.add_cascade(label="Options", menu=opt)

        hlp = tk.Menu(mb, tearoff=0)
        hlp.add_command(label="About", command=self._about)
        mb.add_cascade(label="Help", menu=hlp)

        self.config(menu=mb)

    def _build_body(self) -> None:
        # ── Persistent file queue bar ──────────────────────────────────────
        self._queued_files: list[str] = []
        qbar = ttk.Frame(self)
        qbar.pack(fill="x", padx=6, pady=(6, 2))
        self._queue_label = ttk.Label(qbar, text="No files loaded", foreground="gray")
        self._queue_label.pack(side="left")
        self._queue_actions_btn = ttk.Button(
            qbar, text="Open actions…", state="disabled",
            command=self._reopen_picker)
        self._queue_actions_btn.pack(side="left", padx=6)
        ttk.Button(qbar, text="Clear files",
                   command=self._clear_queue).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=6)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Label(bar, text="Log").pack(side="left")
        ttk.Button(bar, text="Cancel job", command=lambda: self.runner.cancel()).pack(side="right", padx=4)
        ttk.Button(bar, text="Clear", command=lambda: self.log.delete("1.0", "end")).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=6, pady=6)
        self.log = tk.Text(
            body, height=20, wrap="none",
            background="#101418", foreground="#d8d8d8",
            insertbackground="#d8d8d8", font=("Consolas", 9),
        )
        ys = ttk.Scrollbar(body, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", f"Trader's Little Jedi {__version__} ready.\n")

        self.status = ttk.Label(self, text="Ready", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

    # ----- drag-and-drop -----

    def _register_dnd(self) -> None:
        # Register both the window and the log widget; tkdnd routes to whichever is hit.
        for widget in (self, self.log):
            try:
                widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                widget.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _on_drop(self, event) -> None:
        try:
            data = event.data
        except AttributeError:
            return
        paths = self._parse_dnd_data(data)
        new_files = self._expand_paths(paths)
        if not new_files:
            self.append_log("[drop] no files found in dropped item(s)\n")
            return

        if self._queued_files:
            answer = messagebox.askyesnocancel(
                "Trader's Little Jedi",
                f"{len(self._queued_files)} file(s) already loaded.\n\n"
                f"Add the {len(new_files)} new file(s) to the existing list?\n\n"
                "Yes = Add    No = Replace    Cancel = Abort",
                parent=self,
            )
            if answer is None:
                return
            if answer:
                existing = set(self._queued_files)
                self._queued_files.extend(f for f in new_files if f not in existing)
            else:
                self._queued_files = new_files
        else:
            self._queued_files = new_files

        self._update_queue_label()
        self.append_log(f"[drop] {len(self._queued_files)} file(s) loaded — opening action picker\n")
        ActionPickerDialog(self, self.config_obj, self.runner, self._queued_files)

    def _reopen_picker(self) -> None:
        if self._queued_files:
            ActionPickerDialog(self, self.config_obj, self.runner, self._queued_files)

    def _clear_queue(self) -> None:
        self._queued_files = []
        self._update_queue_label()

    def _update_queue_label(self) -> None:
        from pathlib import Path
        n = len(self._queued_files)
        if n == 0:
            self._queue_label.configure(text="No files loaded", foreground="gray")
            self._queue_actions_btn.configure(state="disabled")
        else:
            dirs = {str(Path(f).parent) for f in self._queued_files}
            if len(dirs) == 1:
                folder = Path(self._queued_files[0]).parent.name
            else:
                folder = f"{len(dirs)} folders"
            self._queue_label.configure(
                text=f"{n} file(s) — {folder}", foreground="")
            self._queue_actions_btn.configure(state="normal")

    @staticmethod
    def _parse_dnd_data(data: str) -> list[str]:
        """Parse tkdnd's Tcl-list-formatted file list: {C:/path with spaces/x} C:/y."""
        paths: list[str] = []
        i, n = 0, len(data)
        while i < n:
            while i < n and data[i] in (" ", "\t"):
                i += 1
            if i >= n:
                break
            if data[i] == "{":
                end = data.find("}", i + 1)
                if end == -1:
                    paths.append(data[i + 1:])
                    break
                paths.append(data[i + 1:end])
                i = end + 1
            else:
                end = i
                while end < n and data[end] not in (" ", "\t"):
                    end += 1
                paths.append(data[i:end])
                i = end + 1
        return [p for p in paths if p]

    @staticmethod
    def _expand_paths(paths: list[str]) -> list[str]:
        out: list[str] = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                for sub in sorted(pp.rglob("*")):
                    if sub.is_file():
                        out.append(str(sub))
            elif pp.is_file():
                out.append(str(pp))
        return out

    # ----- log -----

    def append_log(self, text: str) -> None:
        i = 0
        n = len(text)
        while i < n:
            j = i
            while j < n and text[j] not in "\r\n":
                j += 1
            if j > i:
                self.log.insert("end", text[i:j])
            if j < n:
                ch = text[j]
                if ch == "\r":
                    if j + 1 < n and text[j + 1] == "\n":
                        self.log.insert("end", "\n")
                        j += 1
                    else:
                        self.log.delete("end-1c linestart", "end-1c")
                else:
                    self.log.insert("end", "\n")
            i = j + 1
        self.log.see("end")

    def _append_log_threadsafe(self, text: str) -> None:
        self.after(0, lambda: self.append_log(text))

    def _on_job_done(self, rc: int) -> None:
        def f():
            self.status.config(text=("Job exited " + str(rc)) if rc else "Job complete")
        self.after(0, f)

    # ----- dialog launchers -----

    def _open_encode_wav(self) -> None:
        EncodeWavDialog(self, self.config_obj, self.runner)

    def _open_reencode_flac(self) -> None:
        ReencodeFlacDialog(self, self.config_obj, self.runner)

    def _open_decode(self) -> None:
        DecodeDialog(self, self.config_obj, self.runner)

    def _open_merge(self) -> None:
        MergeDialog(self, self.config_obj, self.runner)

    def _open_audio_editor(self) -> None:
        AudioEditorDialog(self, self.config_obj, self.runner)

    def _open_create_chk(self) -> None:
        CreateChecksumDialog(self, self.config_obj, self.runner)

    def _open_verify_chk(self) -> None:
        VerifyChecksumDialog(self, self.config_obj, self.runner)

    def _open_create_torrent(self) -> None:
        CreateTorrentDialog(self, self.config_obj, self.runner)

    def _open_check_torrent(self) -> None:
        CheckTorrentDialog(self, self.config_obj, self.runner)

    def _open_details(self) -> None:
        ShowDetailsDialog(self, self.config_obj, self.runner)

    def _open_test_encoded(self) -> None:
        TestEncodedDialog(self, self.config_obj, self.runner)

    def _open_check_sbe(self) -> None:
        CheckSbeDialog(self, self.config_obj, self.runner)

    def _open_fix_sbe(self) -> None:
        FixSbeDialog(self, self.config_obj, self.runner)

    def _open_test_mpeg(self) -> None:
        TestMpegDialog(self, self.config_obj, self.runner)

    def _open_repair(self) -> None:
        RepairAudioDialog(self, self.config_obj, self.runner)

    def _open_strip_header(self) -> None:
        StripHeaderDialog.open_or_add(self, self.config_obj, self.runner)

    def _open_convert(self) -> None:
        ConvertDialog(self, self.config_obj, self.runner)

    def _open_jedi_tagger(self) -> None:
        JediTaggerDialog.open_or_add(self, self.config_obj, self.runner)

    def _open_show_splitter(self) -> None:
        ShowSplitterDialog(self, self.config_obj, self.runner)

    def _open_dsd_edit(self) -> None:
        DsdEditDialog(self, self.config_obj, self.runner)

    def _open_replaygain(self) -> None:
        ReplayGainDialog(self, self.config_obj, self.runner)

    def _open_batch_rename(self) -> None:
        BatchRenameDialog(self, self.config_obj, self.runner)

    def _launch_spek(self) -> None:
        self._launch_app("spek", "Spek")

    def _launch_xrecode(self) -> None:
        self._launch_app("xrecode3", "xrecode3")

    def _launch_bru(self) -> None:
        self._launch_app("bru", "Bulk Rename Utility")

    def _refresh_tools_menu(self) -> None:
        for label, tool_name in [
            ("Launch TASCAM Hi-Res Editor", "tascam_hireseditor"),
            ("Launch KORG AudioGate", "korg_audiogate"),
            ("Launch Spek…", "spek"),
            ("Launch xrecode3…", "xrecode3"),
            ("Launch Bulk Rename Utility…", "bru"),
        ]:
            state = "normal" if get_tool(tool_name).path(self.config_obj).exists() else "disabled"
            self._tools_menu.entryconfig(label, state=state)

    def _launch_tascam(self) -> None:
        self._launch_app("tascam_hireseditor", "TASCAM Hi-Res Editor")

    def _launch_audiogate(self) -> None:
        self._launch_app("korg_audiogate", "KORG AudioGate")

    def _launch_app(self, tool_name: str, display_name: str) -> None:
        exe = get_tool(tool_name).path(self.config_obj)
        if not exe.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"{display_name} not found.\n\n"
                f"Expected path:\n{exe}\n\n"
                "Install it from its homepage, then check\n"
                "Tools → Update all CLI tools for the current path.",
            )
            return
        try:
            subprocess.Popen(
                [str(exe)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"Could not launch {display_name}:\n{e}")

    def _open_prefs(self) -> None:
        PreferencesDialog(self, self.config_obj)

    def _open_update_tools(self) -> None:
        UpdateToolsDialog(self, self.config_obj, self._append_log_threadsafe)

    def _check_prereqs(self) -> None:
        from .prereqs import check_missing, PrereqDialog
        missing = check_missing(self.config_obj)
        if missing:
            self.append_log(
                f"[prereq] Missing tools: {', '.join(missing)}\n"
                "  Open Tools → Update all CLI tools to install.\n"
            )
            PrereqDialog(self, self.config_obj, self._append_log_threadsafe)

    def _stub(self) -> None:
        messagebox.showinfo("Trader's Little Jedi", "Not yet implemented in this build.")

    def _about(self) -> None:
        dnd = "yes" if _DND_AVAILABLE else "no (pip install tkinterdnd2)"
        messagebox.showinfo(
            "About Trader's Little Jedi",
            f"Trader's Little Jedi {__version__}\n"
            "Lightweight TLH-style audio toolkit.\n\n"
            f"Drag-and-drop: {dnd}\n"
            "Tools live in: tools\\  (use Tools → Update all CLI tools to install)",
        )

    def _on_close(self) -> None:
        if self.config_obj.get("confirm_exit", True):
            if not messagebox.askokcancel("Trader's Little Jedi",
                                          "Exit Trader's Little Jedi?",
                                          parent=self):
                return
        try:
            if self.config_obj.get("save_window_pos", True):
                self.config_obj["window_geometry"] = self.geometry()
                self.config_obj.save()
        finally:
            self.destroy()


def main() -> None:
    MainWindow().mainloop()
