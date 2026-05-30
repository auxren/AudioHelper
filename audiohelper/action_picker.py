"""TLH-style 'Action to perform' modal — shown on drag-and-drop onto the main window."""

import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Callable

from .tools import get_tool


AUDIO_EXTS = {".wav", ".bwf", ".flac", ".mp3", ".ape", ".shn", ".aac", ".m4a",
              ".ogg", ".wv", ".tta", ".aiff", ".aif", ".dff", ".dsf", ".wma"}
DSD_EXTS = {".dff", ".dsf"}
LOSSLESS_EXTS = {".wav", ".bwf", ".flac", ".ape", ".shn", ".wv", ".tta",
                 ".aiff", ".aif", ".dff", ".dsf"}
CHK_EXTS = {".md5", ".sfv", ".ffp", ".sha1", ".sha256"}
TORRENT_EXTS = {".torrent"}


class ActionPickerDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, files: list[str]):
        super().__init__(parent)
        self.title("Action to perform")
        self.parent = parent
        self.config_obj = config
        self.runner = runner
        self.files = files
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        exts = {Path(f).suffix.lower() for f in files}
        has_audio = bool(exts & AUDIO_EXTS)
        has_dsd   = bool(exts & DSD_EXTS)
        has_wav   = bool(exts & {".wav", ".bwf", ".aiff", ".aif"})
        has_flac  = ".flac" in exts
        has_lossless = bool(exts & LOSSLESS_EXTS)
        has_chk  = bool(exts & CHK_EXTS)
        has_torrent = bool(exts & TORRENT_EXTS)
        has_sbe_candidate = bool(exts & {".wav", ".shn"})
        audio_files = [f for f in files if Path(f).suffix.lower() in AUDIO_EXTS]
        can_merge = len(audio_files) >= 2
        has_tascam    = get_tool("tascam_hireseditor").path(config).exists()
        has_audiogate = get_tool("korg_audiogate").path(config).exists()
        has_spek      = get_tool("spek").path(config).exists()
        has_xrecode   = get_tool("xrecode3").path(config).exists()
        has_bru       = get_tool("bru").path(config).exists()

        # (group, label, enabled, handler-key)
        self.action_table: list[tuple[str, str, bool, str]] = [
            ("Format", "Encode WAV / AIFF files", has_wav, "encode_wav"),
            ("Format", "Re-encode flac files", has_flac, "reencode_flac"),
            ("Format", "Decode audio files", has_audio, "decode"),
            ("Format", "Convert encoding format", has_audio, "convert"),
            ("Format", "Merge audio files (gapless)", can_merge, "merge"),
            ("Format", "Audio editor / split / track out", has_audio, "audio_edit"),
            ("Checksum", "Create checksum file", has_audio or len(files) > 0, "create_chk"),
            ("Checksum", "Verify checksum files", has_chk, "verify_chk"),
            ("Torrent", "Create torrent file", True, "create_torrent"),
            ("Torrent", "Check torrent", has_torrent, "check_torrent"),
            ("Analysis", "Show file details (MediaInfo)", len(files) > 0, "audio_details"),
            ("Analysis", "Test encoded audio files", has_lossless, "test_encoded"),
            ("Analysis", "Check audio files for SBEs", has_sbe_candidate, "check_sbe"),
            ("Analysis", "Test wav files for MPEG", has_wav, "test_mpeg"),
            ("Analysis", "Repair FLAC / WAV files",
             bool(exts & {".flac", ".wav", ".bwf"}), "repair_audio"),
            ("Analysis", "Scan ReplayGain tags", has_flac, "replaygain"),
            ("Tools", "Fix SBEs", has_sbe_candidate, "fix_sbe"),
            ("Tools", "Strip audio file header / metadata", has_audio, "strip_header"),
            ("Tools", "Live Show Tagger (tag + setlist)", has_audio, "jedi_tagger"),
            ("Tools", "Batch rename files", len(files) > 0, "batch_rename"),
            ("Other Useful Tools", "Open in TASCAM Hi-Res Editor",
             has_audio and has_tascam, "open_tascam"),
            ("Other Useful Tools", "Open in KORG AudioGate",
             has_audio and has_audiogate, "open_audiogate"),
            ("Other Useful Tools", "Open in Spek (spectral analysis)",
             has_audio and has_spek, "open_spek"),
            ("Other Useful Tools", "Open in xrecode3 (batch converter)",
             has_audio and has_xrecode, "open_xrecode"),
            ("Other Useful Tools", "Open in Bulk Rename Utility",
             len(files) > 0 and has_bru, "open_bru"),
        ]

        self.var_choice = tk.StringVar()
        first_enabled = next((key for _, _, en, key in self.action_table if en), None)
        if first_enabled:
            self.var_choice.set(first_enabled)

        # ----- UI -----
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        if len(files) == 1:
            header_text = f"1 file:  {Path(files[0]).name}"
        else:
            header_text = f"{len(files)} files selected"
        ttk.Label(body, text=header_text, font=("Segoe UI", 9, "italic"),
                  foreground="gray").pack(anchor="w", pady=(0, 8))

        seen: list[str] = []
        for group, _, _, _ in self.action_table:
            if group not in seen:
                seen.append(group)

        for group in seen:
            frame = ttk.LabelFrame(body, text=group, padding=6)
            frame.pack(fill="x", pady=2)
            for g, label, enabled, key in self.action_table:
                if g != group:
                    continue
                rb = ttk.Radiobutton(frame, text=label, variable=self.var_choice, value=key)
                if not enabled:
                    rb.state(["disabled"])
                rb.pack(anchor="w")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._on_ok).pack(side="right", padx=4)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._on_ok())

    def _on_ok(self) -> None:
        choice = self.var_choice.get()
        if not choice:
            return
        handler = self._handlers().get(choice)
        if not handler:
            return
        files = self.files
        self.destroy()
        handler(files)

    def _handlers(self) -> dict[str, Callable[[list[str]], None]]:
        from .repair_audio import RepairAudioDialog
        from .replaygain import ReplayGainDialog
        from .batch_rename import BatchRenameDialog
        from .encode_wav import EncodeWavDialog
        from .reencode_flac import ReencodeFlacDialog
        from .merge import MergeDialog
        from .decode import DecodeDialog
        from .audio_editor import AudioEditorDialog
        from .checksum_dialogs import CreateChecksumDialog, VerifyChecksumDialog
        from .torrent_dialogs import CreateTorrentDialog, CheckTorrentDialog
        from .details import ShowDetailsDialog
        from .test_encoded import TestEncodedDialog
        from .wav_mpeg import TestMpegDialog
        from .sbe_dialogs import CheckSbeDialog, FixSbeDialog
        from .convert import ConvertDialog
        from .strip_header import StripHeaderDialog
        from .jedi_tagger import JediTaggerDialog

        def open_dlg(cls):
            def f(files: list[str]) -> None:
                cls(self.parent, self.config_obj, self.runner, initial_files=files)
            return f

        def stub(name: str):
            def f(files: list[str]) -> None:
                messagebox.showinfo(
                    "Trader's Little Jedi",
                    f"'{name}' is not yet implemented in this build.\n\n"
                    f"{len(files)} file(s) were prepared.",
                )
            return f

        def launch_app(tool_name: str):
            def f(files: list[str]) -> None:
                exe = get_tool(tool_name).path(self.config_obj)
                if not exe.exists():
                    messagebox.showerror(
                        "Trader's Little Jedi",
                        f"{tool_name} not found at:\n{exe}\n\n"
                        "Check Tools → Update all CLI tools.",
                    )
                    return
                try:
                    subprocess.Popen(
                        [str(exe)] + files,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except OSError as e:
                    messagebox.showerror("Trader's Little Jedi", f"Could not launch {tool_name}:\n{e}")
            return f

        return {
            "encode_wav":    open_dlg(EncodeWavDialog),
            "reencode_flac": open_dlg(ReencodeFlacDialog),
            "decode":        open_dlg(DecodeDialog),
            "convert":       open_dlg(ConvertDialog),
            "merge":         open_dlg(MergeDialog),
            "audio_edit":    open_dlg(AudioEditorDialog),
            "create_chk":    open_dlg(CreateChecksumDialog),
            "verify_chk":    open_dlg(VerifyChecksumDialog),
            "create_torrent": open_dlg(CreateTorrentDialog),
            "check_torrent": open_dlg(CheckTorrentDialog),
            "audio_details": open_dlg(ShowDetailsDialog),
            "test_encoded":  open_dlg(TestEncodedDialog),
            "check_sbe":     open_dlg(CheckSbeDialog),
            "test_mpeg":     open_dlg(TestMpegDialog),
            "fix_sbe":       open_dlg(FixSbeDialog),
            "strip_header":  lambda files: StripHeaderDialog.open_or_add(self.parent, self.config_obj, self.runner, files),
            "jedi_tagger":   lambda files: JediTaggerDialog.open_or_add(self.parent, self.config_obj, self.runner, files),
            "repair_audio":  open_dlg(RepairAudioDialog),
            "replaygain":    open_dlg(ReplayGainDialog),
            "batch_rename":  open_dlg(BatchRenameDialog),
            "open_tascam":    launch_app("tascam_hireseditor"),
            "open_audiogate": launch_app("korg_audiogate"),
            "open_spek":      launch_app("spek"),
            "open_xrecode":   launch_app("xrecode3"),
            "open_bru":       launch_app("bru"),
        }
