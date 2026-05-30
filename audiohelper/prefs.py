import tkinter as tk
from tkinter import ttk, filedialog


_APE_LEVELS   = ["fast", "normal", "high", "extra high", "insane"]
_CHK_TYPES    = [
    "wholefile md5 checksum file (.md5)",
    "flac fingerprint file (.ffp)",
    "simple file verification file (.sfv)",
    "composite fingerprint file (.cfp)",
    "shorten tag fingerprint file (.st5)",
]
_STARTUP_PAGES = [
    "(None)", "Encode wav files", "Re-encode flac files", "Decode audio files",
    "Convert encoding format", "Create checksum file", "Verify checksum files",
    "Create torrent file", "Check torrent",
]


class PreferencesDialog(tk.Toplevel):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.title("Preferences")
        self.config_obj = config
        self.geometry("740x560")
        self.minsize(680, 480)
        self.transient(parent)
        self.grab_set()

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(body, show="tree", selectmode="browse")
        self.tree.column("#0", width=195)
        self.tree.pack(side="left", fill="y")

        self.detail = ttk.Frame(body, padding=10, relief="groove", borderwidth=1)
        self.detail.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self._panels: dict[str, ttk.Frame] = {}
        self._build_categories()

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.selection_set("audio_compression")

    # ------------------------------------------------------------------ #
    # Tree                                                                 #
    # ------------------------------------------------------------------ #

    def _build_categories(self) -> None:
        g = self.tree.insert("", "end", "general",      text="General",  open=True)
        self.tree.insert(g, "end", "audio_compression", text="Audio compression")
        self.tree.insert(g, "end", "directories",        text="Directories")
        self.tree.insert(g, "end", "shell_integration",  text="Shell integration")
        self.tree.insert(g, "end", "other",               text="Other")

        f = self.tree.insert("", "end", "format_grp", text="Format", open=True)
        self.tree.insert(f, "end", "encode_wav",     text="Encode wav files")
        self.tree.insert(f, "end", "reencode_flac",  text="Re-encode flac files")
        self.tree.insert(f, "end", "convert",        text="Convert encoding format")

        c = self.tree.insert("", "end", "checksum_grp",   text="Checksum", open=True)
        self.tree.insert(c, "end", "create_checksum",     text="Create checksum file")
        self.tree.insert(c, "end", "verify_checksum",     text="Verify checksum files")

        t = self.tree.insert("", "end", "torrent_grp", text="Torrent", open=True)
        self.tree.insert(t, "end", "create_torrent",   text="Create torrent file")
        self.tree.insert(t, "end", "check_torrent",    text="Check torrent")

        a = self.tree.insert("", "end", "analysis_grp",    text="Analysis", open=True)
        self.tree.insert(a, "end", "show_audio_details",   text="Show audio file details")
        self.tree.insert(a, "end", "test_wav_mpeg",         text="Test wav files for MP3")

        tl = self.tree.insert("", "end", "tools_grp", text="Tools", open=True)
        self.tree.insert(tl, "end", "fix_sbe",         text="Fix SBEs")
        self.tree.insert(tl, "end", "strip_header",    text="Strip audio file header")

        self._panels["audio_compression"]  = self._panel_audio_compression()
        self._panels["directories"]         = self._panel_directories()
        self._panels["shell_integration"]   = self._panel_shell_integration()
        self._panels["other"]               = self._panel_other()
        self._panels["encode_wav"]          = self._panel_encode_wav()
        self._panels["reencode_flac"]       = self._panel_reencode_flac()
        self._panels["convert"]             = self._panel_convert()
        self._panels["create_checksum"]     = self._panel_create_checksum()
        self._panels["verify_checksum"]     = self._panel_verify_checksum()
        self._panels["create_torrent"]      = self._panel_create_torrent()
        self._panels["check_torrent"]       = self._panel_check_torrent()
        self._panels["show_audio_details"]  = self._panel_show_details()
        self._panels["test_wav_mpeg"]       = self._panel_test_mpeg()
        self._panels["fix_sbe"]             = self._panel_fix_sbe()
        self._panels["strip_header"]        = self._panel_strip_header()

    # ------------------------------------------------------------------ #
    # Shared helpers                                                       #
    # ------------------------------------------------------------------ #

    def _head(self, parent, text: str) -> None:
        ttk.Label(parent, text=text,
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

    def _browse(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(parent=self, initialdir=var.get() or None)
        if d:
            var.set(d)

    def _dir_row(self, parent, var: tk.StringVar) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(2, 0))
        ttk.Entry(row, textvariable=var, width=44).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3,
                   command=lambda: self._browse(var)).pack(side="left", padx=(4, 0))

    # ------------------------------------------------------------------ #
    # Panels                                                               #
    # ------------------------------------------------------------------ #

    def _panel_audio_compression(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Audio compression")

        sect = ttk.LabelFrame(f, text="Compression format settings", padding=8)
        sect.pack(fill="x", pady=(0, 6))
        ttk.Label(sect, text="APE compression level:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_ape = tk.StringVar(value=str(c.get("ape_compression_level", "normal")))
        ttk.Combobox(sect, textvariable=self.var_ape, width=14, state="readonly",
                     values=_APE_LEVELS).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(sect, text="FLAC compression level:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_flac = tk.IntVar(value=int(c.get("flac_compression_level", 8)))
        ttk.Combobox(sect, textvariable=self.var_flac, width=5, state="readonly",
                     values=list(range(0, 9))).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(sect, text="MP3 encoding mode:").grid(row=2, column=0, sticky="w", pady=2)
        self.var_mp3 = tk.StringVar(value=str(c.get("mp3_mode", "VBR (variable bitrate)")))
        ttk.Combobox(sect, textvariable=self.var_mp3, width=24, state="readonly",
                     values=["CBR (constant bitrate)", "ABR (average bitrate)",
                             "VBR (variable bitrate)"]).grid(row=2, column=1, sticky="w", padx=6)

        sect2 = ttk.LabelFrame(f, text="MP3 encoding mode settings", padding=8)
        sect2.pack(fill="x")
        ttk.Label(sect2, text="CBR:  bitrate").grid(row=0, column=0, sticky="w", pady=2)
        self.var_cbr = tk.IntVar(value=int(c.get("lame_cbr_bitrate", 128)))
        ttk.Combobox(sect2, textvariable=self.var_cbr, width=6, state="readonly",
                     values=[96, 128, 160, 192, 224, 256, 320]).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(sect2, text="kbit/s").grid(row=0, column=2, sticky="w")
        ttk.Label(sect2, text="ABR:  bitrate").grid(row=1, column=0, sticky="w", pady=2)
        self.var_abr = tk.IntVar(value=int(c.get("lame_abr_bitrate", 160)))
        ttk.Spinbox(sect2, from_=64, to=320, increment=16, textvariable=self.var_abr,
                    width=6).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(sect2, text="kbit/s").grid(row=1, column=2, sticky="w")
        ttk.Label(sect2, text="VBR:  quality").grid(row=2, column=0, sticky="w", pady=2)
        self.var_vbr = tk.IntVar(value=int(c.get("lame_vbr_quality", 2)))
        ttk.Combobox(sect2, textvariable=self.var_vbr, width=5, state="readonly",
                     values=list(range(0, 10))).grid(row=2, column=1, sticky="w", padx=4)
        self.var_vbr_new = tk.BooleanVar(value=bool(c.get("lame_vbr_new_routine", True)))
        ttk.Checkbutton(sect2, text="use new variable bitrate routine",
                        variable=self.var_vbr_new).grid(row=2, column=3, sticky="w", padx=(16, 0))
        return f

    def _panel_directories(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Directories")

        ttk.Label(f, text="Directory to add all files except '.torrent' files from:").pack(anchor="w")
        self.var_src_dir = tk.StringVar(value=c.get("default_source_dir", "C:\\"))
        self._dir_row(f, self.var_src_dir)

        ttk.Label(f, text="Directory to write processed files to:").pack(
            anchor="w", pady=(12, 0))
        self.var_out_mode = tk.StringVar(value=c.get("output_dir_mode", "source"))
        ttk.Radiobutton(f, text="Same as source files",
                        variable=self.var_out_mode, value="source").pack(anchor="w", pady=(2, 0))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="This one:",
                        variable=self.var_out_mode, value="custom").pack(side="left")
        self.var_custom_out = tk.StringVar(value=c.get("custom_output_dir", ""))
        ttk.Entry(row, textvariable=self.var_custom_out,
                  width=36).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(row, text="…", width=3,
                   command=lambda: self._browse(self.var_custom_out)).pack(side="left", padx=(4, 0))
        return f

    def _panel_shell_integration(self) -> ttk.Frame:
        f = ttk.Frame(self.detail)
        self._head(f, "Shell integration")
        ttk.Label(f, text="Windows Explorer context menu integration.",
                  foreground="gray").pack(anchor="w")
        ttk.Label(f, text="(Coming in a future release.)",
                  foreground="gray").pack(anchor="w", pady=(4, 0))
        return f

    def _panel_other(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Other")

        su = ttk.LabelFrame(f, text="On startup", padding=8)
        su.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(su)
        row.pack(anchor="w")
        ttk.Label(row, text="Open page:").pack(side="left")
        self.var_startup_page = tk.StringVar(value=c.get("startup_page", "(None)"))
        ttk.Combobox(row, textvariable=self.var_startup_page, state="readonly",
                     width=28, values=_STARTUP_PAGES).pack(side="left", padx=(6, 0))

        ex = ttk.LabelFrame(f, text="On exit", padding=8)
        ex.pack(fill="x")
        self.var_confirm_exit = tk.BooleanVar(value=bool(c.get("confirm_exit", True)))
        ttk.Checkbutton(ex, text="Confirm exit before shutting down Trader's Little Jedi",
                        variable=self.var_confirm_exit).pack(anchor="w")
        self.var_save_win_pos = tk.BooleanVar(value=bool(c.get("save_window_pos", True)))
        ttk.Checkbutton(ex, text="Save window position and size",
                        variable=self.var_save_win_pos).pack(anchor="w", pady=(4, 0))
        return f

    def _panel_encode_wav(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Encode wav files")

        ef = ttk.LabelFrame(f, text="Encoding format", padding=8)
        ef.pack(fill="x", pady=(0, 4))
        self.var_enc_fmt = tk.StringVar(
            value=c.get("encode_wav_format", "FLAC (Free Lossless Audio Codec)"))
        ttk.Combobox(ef, textvariable=self.var_enc_fmt, state="readonly", width=36,
                     values=["FLAC (Free Lossless Audio Codec)", "APE (Monkey's Audio)",
                             "MP3 (MPEG Layer 3)", "SHN (Shorten)"]).pack(anchor="w")

        cf = ttk.LabelFrame(f, text="Confirmation dialog box", padding=8)
        cf.pack(fill="x", pady=(0, 4))
        self.var_enc_confirm_align = tk.BooleanVar(
            value=bool(c.get("encode_wav_confirm_align", True)))
        ttk.Checkbutton(cf,
                        text="Confirm order of files before encoding with 'Align …' option enabled",
                        variable=self.var_enc_confirm_align).pack(anchor="w")

        pt = ttk.LabelFrame(f, text="Post encoding tasks", padding=8)
        pt.pack(fill="x", pady=(0, 4))
        self.var_enc_post_test = tk.BooleanVar(value=bool(c.get("encode_wav_post_test", False)))
        ttk.Checkbutton(pt, text="Test encoded files",
                        variable=self.var_enc_post_test).pack(anchor="w")
        self.var_enc_post_chk = tk.BooleanVar(value=bool(c.get("encode_wav_post_checksum", False)))
        ttk.Checkbutton(pt, text="Create checksum file",
                        variable=self.var_enc_post_chk).pack(anchor="w")
        self.var_enc_post_del = tk.BooleanVar(value=bool(c.get("encode_wav_post_delete", False)))
        ttk.Checkbutton(pt, text="Delete source files",
                        variable=self.var_enc_post_del).pack(anchor="w")

        ck = ttk.LabelFrame(f, text="Type of checksum file to create (post encoding)", padding=8)
        ck.pack(fill="x")
        for lbl, key, default in [
            ("Encoding format APE:",  "encode_wav_chk_ape",
             "wholefile md5 checksum file (.md5)"),
            ("Encoding format FLAC:", "encode_wav_chk_flac",
             "flac fingerprint file (.ffp)"),
            ("Encoding format MP3:",  "encode_wav_chk_mp3",
             "simple file verification file (.sfv)"),
            ("Encoding format SHN:",  "encode_wav_chk_shn",
             "wholefile md5 checksum file (.md5)"),
        ]:
            row = ttk.Frame(ck)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=lbl, width=21, anchor="w").pack(side="left")
            var = tk.StringVar(value=c.get(key, default))
            setattr(self, f"var_{key}", var)
            ttk.Combobox(row, textvariable=var, state="readonly",
                         width=38, values=_CHK_TYPES).pack(side="left", padx=(4, 0))
        return f

    def _panel_reencode_flac(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Re-encode flac files")

        lf = ttk.LabelFrame(f, text="Re-encoding level", padding=8)
        lf.pack(fill="x", pady=(0, 4))
        self.var_re_level = tk.IntVar(
            value=int(c.get("reencode_flac_level", c.get("flac_compression_level", 8))))
        ttk.Combobox(lf, textvariable=self.var_re_level, width=5, state="readonly",
                     values=list(range(0, 9))).pack(anchor="w")

        cf = ttk.LabelFrame(f, text="Confirmation dialog box", padding=8)
        cf.pack(fill="x", pady=(0, 4))
        self.var_re_confirm = tk.BooleanVar(
            value=bool(c.get("reencode_flac_confirm_overwrite", True)))
        ttk.Checkbutton(cf, text="Confirm overwrite of source flac files before re-encoding",
                        variable=self.var_re_confirm).pack(anchor="w")

        pt = ttk.LabelFrame(f, text="Post re-encoding tasks", padding=8)
        pt.pack(fill="x", pady=(0, 4))
        self.var_re_post_test = tk.BooleanVar(value=bool(c.get("reencode_flac_post_test", False)))
        ttk.Checkbutton(pt, text="Test re-encoded files",
                        variable=self.var_re_post_test).pack(anchor="w")
        self.var_re_post_chk = tk.BooleanVar(
            value=bool(c.get("reencode_flac_post_checksum", False)))
        ttk.Checkbutton(pt, text="Create checksum file",
                        variable=self.var_re_post_chk).pack(anchor="w")
        self.var_re_post_del = tk.BooleanVar(
            value=bool(c.get("reencode_flac_post_delete", False)))
        ttk.Checkbutton(pt, text="Delete source files",
                        variable=self.var_re_post_del).pack(anchor="w")

        ck = ttk.LabelFrame(f, text="Type of checksum file to create (post re-encoding)", padding=8)
        ck.pack(fill="x")
        self.var_re_chk_type = tk.StringVar(
            value=c.get("reencode_flac_chk_type", "flac fingerprint file (.ffp)"))
        ttk.Combobox(ck, textvariable=self.var_re_chk_type, state="readonly",
                     width=38, values=_CHK_TYPES).pack(anchor="w")
        return f

    def _panel_convert(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Convert encoding format")

        tf = ttk.LabelFrame(f, text="Target encoding format", padding=8)
        tf.pack(fill="x")
        self.var_convert_fmt = tk.StringVar(value=c.get("convert_target_format", "FLAC"))
        ttk.Radiobutton(tf, text="FLAC",
                        variable=self.var_convert_fmt, value="FLAC").pack(anchor="w")
        ttk.Radiobutton(tf, text="MP3",
                        variable=self.var_convert_fmt, value="MP3").pack(anchor="w")
        return f

    def _panel_create_checksum(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Create checksum file")

        sh = ttk.LabelFrame(
            f, text="Procedure when adding files by means of shell integration", padding=8)
        sh.pack(fill="x", pady=(0, 4))
        self.var_chk_clear = tk.BooleanVar(value=bool(c.get("chk_create_clear_on_add", True)))
        ttk.Checkbutton(sh, text="Clear existing file list before adding files",
                        variable=self.var_chk_clear).pack(anchor="w")
        self.var_chk_subfolders = tk.BooleanVar(
            value=bool(c.get("chk_create_include_subfolders", False)))
        ttk.Checkbutton(sh, text="Include files from subfolders when adding a folder",
                        variable=self.var_chk_subfolders).pack(anchor="w")
        self.var_chk_auto_start = tk.BooleanVar(
            value=bool(c.get("chk_create_auto_start", False)))
        ttk.Checkbutton(sh, text="Start creating checksum file automatically after adding files",
                        variable=self.var_chk_auto_start).pack(anchor="w")

        tt = ttk.LabelFrame(f, text="Type of checksum file to create", padding=8)
        tt.pack(fill="x", pady=(0, 4))
        self.var_chk_type = tk.StringVar(
            value=c.get("chk_create_type", "wholefile md5 checksum file (.md5)"))
        ttk.Combobox(tt, textvariable=self.var_chk_type, state="readonly",
                     width=40, values=_CHK_TYPES).pack(anchor="w")

        cd = ttk.LabelFrame(f, text="Confirmation dialog box", padding=8)
        cd.pack(fill="x", pady=(0, 4))
        self.var_chk_confirm_cfp = tk.BooleanVar(
            value=bool(c.get("chk_create_confirm_cfp", True)))
        ttk.Checkbutton(
            cd,
            text="Confirm order of files before creating a '.cfp' "
                 "(composite md5 fingerprint) file",
            variable=self.var_chk_confirm_cfp).pack(anchor="w")

        ih = ttk.LabelFrame(f, text="Info at beginning of a checksum file", padding=8)
        ih.pack(fill="x", pady=(0, 4))
        self.var_chk_add_info = tk.BooleanVar(
            value=bool(c.get("chk_create_add_info_header", False)))
        ttk.Checkbutton(
            ih,
            text="Add type of file, name of file generating application, "
                 "creation date and time",
            variable=self.var_chk_add_info).pack(anchor="w")

        sf = ttk.LabelFrame(f, text="Checksum file", padding=8)
        sf.pack(fill="x")
        self.var_chk_auto_save = tk.BooleanVar(
            value=bool(c.get("chk_create_auto_save", False)))
        ttk.Checkbutton(
            sf,
            text="Save automatically as "
                 "'<name of source file or root folder>.<type of file>'",
            variable=self.var_chk_auto_save).pack(anchor="w")
        return f

    def _panel_verify_checksum(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Verify checksum files")

        sh = ttk.LabelFrame(
            f,
            text="Procedure when adding checksum files by means of shell integration",
            padding=8)
        sh.pack(fill="x")
        self.var_ver_clear = tk.BooleanVar(value=bool(c.get("chk_verify_clear_on_add", True)))
        ttk.Checkbutton(sh, text="Clear existing file list before adding checksum files",
                        variable=self.var_ver_clear).pack(anchor="w")
        self.var_ver_auto = tk.BooleanVar(value=bool(c.get("chk_verify_auto_start", False)))
        ttk.Checkbutton(sh, text="Start verifying automatically after adding checksum files",
                        variable=self.var_ver_auto).pack(anchor="w")
        return f

    def _panel_create_torrent(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Create torrent file")

        ta = ttk.LabelFrame(f, text="Tracker / Announce", padding=8)
        ta.pack(fill="x", pady=(0, 4))
        trackers = c.get("trackers", [])
        choices = ["(no default tracker)"] + (trackers if isinstance(trackers, list) else [])
        self.var_torrent_tracker = tk.StringVar(
            value=c.get("torrent_default_tracker", "(no default tracker)"))
        ttk.Combobox(ta, textvariable=self.var_torrent_tracker,
                     width=50, values=choices).pack(fill="x")

        pr = ttk.LabelFrame(f, text="Torrent properties", padding=8)
        pr.pack(fill="x", pady=(0, 4))
        self.var_tor_private = tk.BooleanVar(value=bool(c.get("torrent_private", True)))
        ttk.Checkbutton(pr, text="Private torrent",
                        variable=self.var_tor_private).pack(anchor="w")
        self.var_tor_utf8 = tk.BooleanVar(value=bool(c.get("torrent_utf8", True)))
        ttk.Checkbutton(pr, text="UTF-8 encoding",
                        variable=self.var_tor_utf8).pack(anchor="w")

        ex = ttk.LabelFrame(f, text="Multi-file torrent", padding=8)
        ex.pack(fill="x", pady=(0, 4))
        ttk.Label(ex, text="Exclude from file list:").pack(anchor="w")
        self.var_tor_excl_db = tk.BooleanVar(value=bool(c.get("torrent_exclude_db", True)))
        ttk.Checkbutton(ex, text="*.db",
                        variable=self.var_tor_excl_db).pack(anchor="w", padx=(16, 0))
        self.var_tor_excl_tor = tk.BooleanVar(
            value=bool(c.get("torrent_exclude_torrent", True)))
        ttk.Checkbutton(ex, text="*.torrent",
                        variable=self.var_tor_excl_tor).pack(anchor="w", padx=(16, 0))

        sl = ttk.LabelFrame(f, text="Location to save torrent files to", padding=8)
        sl.pack(fill="x")
        self.var_tor_save_mode = tk.StringVar(value=c.get("torrent_save_mode", "source"))
        ttk.Radiobutton(sl,
                        text="Folder containing the source the torrent file is for",
                        variable=self.var_tor_save_mode, value="source").pack(anchor="w")
        row = ttk.Frame(sl)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="This one:",
                        variable=self.var_tor_save_mode, value="custom").pack(side="left")
        self.var_tor_save_dir = tk.StringVar(value=c.get("torrent_save_dir", ""))
        ttk.Entry(row, textvariable=self.var_tor_save_dir,
                  width=34).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(row, text="…", width=3,
                   command=lambda: self._browse(self.var_tor_save_dir)).pack(
                       side="left", padx=(4, 0))
        return f

    def _panel_check_torrent(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Check torrent")

        ttk.Label(f, text="Directory to add torrent files from:").pack(anchor="w")
        self.var_chk_tor_dir = tk.StringVar(value=c.get("torrent_check_dir", "C:\\"))
        self._dir_row(f, self.var_chk_tor_dir)

        ttk.Label(f, text="Properties page when loading a torrent file:").pack(
            anchor="w", pady=(12, 0))
        self.var_tor_prop_page = tk.StringVar(
            value=c.get("torrent_check_properties_page", "Summary"))
        ttk.Combobox(f, textvariable=self.var_tor_prop_page, state="readonly",
                     width=24, values=["Summary", "Files",
                                       "Trackers"]).pack(anchor="w", pady=(2, 8))

        pa = ttk.LabelFrame(f, text="Piece analysis", padding=8)
        pa.pack(fill="x")
        self.var_tor_out_of_pos = tk.BooleanVar(
            value=bool(c.get("torrent_check_out_of_position", False)))
        ttk.Checkbutton(pa, text="Include matching pieces that are out of position",
                        variable=self.var_tor_out_of_pos).pack(anchor="w")
        return f

    def _panel_show_details(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Show audio file details")

        su = ttk.LabelFrame(f, text="Size units (len mode only)", padding=8)
        su.pack(fill="x", pady=(0, 8))
        _units = ["bytes", "kbytes", "Mbytes", "Gbytes", "auto"]
        for lbl, attr, key in [
            ("Files:",  "var_det_files_unit",  "details_size_unit_files"),
            ("Totals:", "var_det_totals_unit", "details_size_unit_totals"),
        ]:
            row = ttk.Frame(su)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=lbl, width=8, anchor="w").pack(side="left")
            var = tk.StringVar(value=c.get(key, "bytes"))
            setattr(self, attr, var)
            ttk.Combobox(row, textvariable=var, state="readonly",
                         width=10, values=_units).pack(side="left", padx=(4, 0))

        lg = ttk.LabelFrame(f, text="Procedure when saving the process log", padding=8)
        lg.pack(fill="x")
        self.var_det_shntool_info = tk.BooleanVar(
            value=bool(c.get("details_add_shntool_info", False)))
        ttk.Checkbutton(lg, text="Add version and copyright of shntool used for analysis",
                        variable=self.var_det_shntool_info).pack(anchor="w")
        return f

    def _panel_test_mpeg(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Test wav files for MP3")

        dm = ttk.LabelFrame(f, text="Detect mode", padding=8)
        dm.pack(fill="x")
        row = ttk.Frame(dm)
        row.pack(anchor="w")
        self.var_mpeg_mode = tk.IntVar(value=int(c.get("mpeg_detect_mode", 8)))
        ttk.Spinbox(row, from_=0, to=40, textvariable=self.var_mpeg_mode,
                    width=6).pack(side="left")
        ttk.Label(row,
                  text="Range:  0 (slow and most accurate) … 40 (fast, but less accurate)",
                  foreground="gray").pack(side="left", padx=(10, 0))
        return f

    def _panel_fix_sbe(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Fix SBEs")

        rl = ttk.LabelFrame(f, text="Re-encoding level", padding=8)
        rl.pack(fill="x", pady=(0, 4))
        for lbl, attr, key, default, widget_type, values in [
            ("APE:", "var_sbe_ape", "sbe_ape_level", "2 (normal)", "combo",
             ["1 (fast)", "2 (normal)", "3 (high)", "4 (extra high)", "5 (insane)"]),
            ("FLAC:", "var_sbe_flac", "sbe_flac_level", 6, "combo",
             list(range(0, 9))),
        ]:
            row = ttk.Frame(rl)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=lbl, width=10, anchor="w").pack(side="left")
            if widget_type == "combo":
                var = tk.StringVar(value=str(c.get(key, default))) \
                    if isinstance(default, str) \
                    else tk.IntVar(value=int(c.get(key, default)))
                setattr(self, attr, var)
                ttk.Combobox(row, textvariable=var, state="readonly",
                             width=16, values=values).pack(side="left", padx=(4, 0))
        row_mkw = ttk.Frame(rl)
        row_mkw.pack(fill="x", pady=2)
        ttk.Label(row_mkw, text="MKW/SHN:", width=10, anchor="w").pack(side="left")
        ttk.Label(row_mkw, text="not supported", foreground="gray").pack(side="left", padx=(4, 0))

        tn = ttk.LabelFrame(f, text="Target file names", padding=8)
        tn.pack(fill="x", pady=(0, 4))
        self.var_sbe_fixed = tk.BooleanVar(value=bool(c.get("sbe_append_fixed", True)))
        ttk.Checkbutton(tn, text="Append string '-fixed'  (fix mode only)",
                        variable=self.var_sbe_fixed).pack(anchor="w")
        self.var_sbe_padded = tk.BooleanVar(value=bool(c.get("sbe_append_padded", True)))
        ttk.Checkbutton(tn, text="Append string '-padded'  (pad mode only)",
                        variable=self.var_sbe_padded).pack(anchor="w")
        ttk.Label(tn,
                  text="Not appending the string will work only if the target file\n"
                       "will be written to another directory than the source file directory.",
                  foreground="gray", justify="left").pack(anchor="w", pady=(4, 0))

        cd = ttk.LabelFrame(f, text="Confirmation dialog box", padding=8)
        cd.pack(fill="x")
        self.var_sbe_confirm = tk.BooleanVar(value=bool(c.get("sbe_confirm_order", True)))
        ttk.Checkbutton(
            cd,
            text="Confirm order of files before fixing sector boundary errors in 'fix' mode",
            variable=self.var_sbe_confirm).pack(anchor="w")
        return f

    def _panel_strip_header(self) -> ttk.Frame:
        c = self.config_obj
        f = ttk.Frame(self.detail)
        self._head(f, "Strip audio file header")

        tn = ttk.LabelFrame(f, text="Target file names", padding=8)
        tn.pack(fill="x")
        self.var_strip_suffix = tk.BooleanVar(value=bool(c.get("strip_append_stripped", True)))
        ttk.Checkbutton(tn, text="Append string '-stripped'",
                        variable=self.var_strip_suffix).pack(anchor="w")
        ttk.Label(tn,
                  text="Not appending the string will work only if the target file\n"
                       "will be written to another directory than the source file directory.",
                  foreground="gray", justify="left").pack(anchor="w", pady=(4, 0))
        return f

    # ------------------------------------------------------------------ #
    # Selection handler                                                    #
    # ------------------------------------------------------------------ #

    def _on_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        for w in self.detail.winfo_children():
            w.pack_forget()
        panel = self._panels.get(sel[0])
        if panel:
            panel.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    def _ok(self) -> None:
        c = self.config_obj

        # Audio compression
        if hasattr(self, "var_flac"):    c["flac_compression_level"] = int(self.var_flac.get())
        if hasattr(self, "var_ape"):     c["ape_compression_level"]  = self.var_ape.get()
        if hasattr(self, "var_mp3"):     c["mp3_mode"]               = self.var_mp3.get()
        if hasattr(self, "var_cbr"):     c["lame_cbr_bitrate"]       = int(self.var_cbr.get())
        if hasattr(self, "var_abr"):     c["lame_abr_bitrate"]       = int(self.var_abr.get())
        if hasattr(self, "var_vbr"):     c["lame_vbr_quality"]       = int(self.var_vbr.get())
        if hasattr(self, "var_vbr_new"): c["lame_vbr_new_routine"]   = bool(self.var_vbr_new.get())

        # Directories
        if hasattr(self, "var_src_dir"):    c["default_source_dir"] = self.var_src_dir.get()
        if hasattr(self, "var_out_mode"):   c["output_dir_mode"]    = self.var_out_mode.get()
        if hasattr(self, "var_custom_out"): c["custom_output_dir"]  = self.var_custom_out.get()

        # Other
        if hasattr(self, "var_startup_page"): c["startup_page"]    = self.var_startup_page.get()
        if hasattr(self, "var_confirm_exit"):  c["confirm_exit"]   = bool(self.var_confirm_exit.get())
        if hasattr(self, "var_save_win_pos"):  c["save_window_pos"] = bool(self.var_save_win_pos.get())

        # Encode wav
        if hasattr(self, "var_enc_fmt"):          c["encode_wav_format"]         = self.var_enc_fmt.get()
        if hasattr(self, "var_enc_confirm_align"): c["encode_wav_confirm_align"] = bool(self.var_enc_confirm_align.get())
        if hasattr(self, "var_enc_post_test"):    c["encode_wav_post_test"]      = bool(self.var_enc_post_test.get())
        if hasattr(self, "var_enc_post_chk"):     c["encode_wav_post_checksum"]  = bool(self.var_enc_post_chk.get())
        if hasattr(self, "var_enc_post_del"):     c["encode_wav_post_delete"]    = bool(self.var_enc_post_del.get())
        for key in ("encode_wav_chk_ape", "encode_wav_chk_flac",
                    "encode_wav_chk_mp3", "encode_wav_chk_shn"):
            var = getattr(self, f"var_{key}", None)
            if var is not None:
                c[key] = var.get()

        # Re-encode FLAC
        if hasattr(self, "var_re_level"):     c["reencode_flac_level"]            = int(self.var_re_level.get())
        if hasattr(self, "var_re_confirm"):   c["reencode_flac_confirm_overwrite"] = bool(self.var_re_confirm.get())
        if hasattr(self, "var_re_post_test"): c["reencode_flac_post_test"]         = bool(self.var_re_post_test.get())
        if hasattr(self, "var_re_post_chk"):  c["reencode_flac_post_checksum"]     = bool(self.var_re_post_chk.get())
        if hasattr(self, "var_re_post_del"):  c["reencode_flac_post_delete"]       = bool(self.var_re_post_del.get())
        if hasattr(self, "var_re_chk_type"):  c["reencode_flac_chk_type"]          = self.var_re_chk_type.get()

        # Convert
        if hasattr(self, "var_convert_fmt"): c["convert_target_format"] = self.var_convert_fmt.get()

        # Create checksum
        if hasattr(self, "var_chk_clear"):       c["chk_create_clear_on_add"]       = bool(self.var_chk_clear.get())
        if hasattr(self, "var_chk_subfolders"):  c["chk_create_include_subfolders"] = bool(self.var_chk_subfolders.get())
        if hasattr(self, "var_chk_auto_start"):  c["chk_create_auto_start"]         = bool(self.var_chk_auto_start.get())
        if hasattr(self, "var_chk_type"):        c["chk_create_type"]               = self.var_chk_type.get()
        if hasattr(self, "var_chk_confirm_cfp"): c["chk_create_confirm_cfp"]        = bool(self.var_chk_confirm_cfp.get())
        if hasattr(self, "var_chk_add_info"):    c["chk_create_add_info_header"]    = bool(self.var_chk_add_info.get())
        if hasattr(self, "var_chk_auto_save"):   c["chk_create_auto_save"]          = bool(self.var_chk_auto_save.get())

        # Verify checksum
        if hasattr(self, "var_ver_clear"): c["chk_verify_clear_on_add"] = bool(self.var_ver_clear.get())
        if hasattr(self, "var_ver_auto"):  c["chk_verify_auto_start"]   = bool(self.var_ver_auto.get())

        # Create torrent
        if hasattr(self, "var_torrent_tracker"): c["torrent_default_tracker"]    = self.var_torrent_tracker.get()
        if hasattr(self, "var_tor_private"):     c["torrent_private"]            = bool(self.var_tor_private.get())
        if hasattr(self, "var_tor_utf8"):        c["torrent_utf8"]               = bool(self.var_tor_utf8.get())
        if hasattr(self, "var_tor_excl_db"):     c["torrent_exclude_db"]         = bool(self.var_tor_excl_db.get())
        if hasattr(self, "var_tor_excl_tor"):    c["torrent_exclude_torrent"]    = bool(self.var_tor_excl_tor.get())
        if hasattr(self, "var_tor_save_mode"):   c["torrent_save_mode"]          = self.var_tor_save_mode.get()
        if hasattr(self, "var_tor_save_dir"):    c["torrent_save_dir"]           = self.var_tor_save_dir.get()

        # Check torrent
        if hasattr(self, "var_chk_tor_dir"):    c["torrent_check_dir"]               = self.var_chk_tor_dir.get()
        if hasattr(self, "var_tor_prop_page"):  c["torrent_check_properties_page"]   = self.var_tor_prop_page.get()
        if hasattr(self, "var_tor_out_of_pos"): c["torrent_check_out_of_position"]   = bool(self.var_tor_out_of_pos.get())

        # Show audio details
        if hasattr(self, "var_det_files_unit"):   c["details_size_unit_files"]   = self.var_det_files_unit.get()
        if hasattr(self, "var_det_totals_unit"):  c["details_size_unit_totals"]  = self.var_det_totals_unit.get()
        if hasattr(self, "var_det_shntool_info"): c["details_add_shntool_info"]  = bool(self.var_det_shntool_info.get())

        # Test wav for MPEG
        if hasattr(self, "var_mpeg_mode"): c["mpeg_detect_mode"] = int(self.var_mpeg_mode.get())

        # Fix SBEs
        if hasattr(self, "var_sbe_ape"):    c["sbe_ape_level"]     = self.var_sbe_ape.get()
        if hasattr(self, "var_sbe_flac"):   c["sbe_flac_level"]    = int(self.var_sbe_flac.get())
        if hasattr(self, "var_sbe_fixed"):  c["sbe_append_fixed"]  = bool(self.var_sbe_fixed.get())
        if hasattr(self, "var_sbe_padded"): c["sbe_append_padded"] = bool(self.var_sbe_padded.get())
        if hasattr(self, "var_sbe_confirm"): c["sbe_confirm_order"] = bool(self.var_sbe_confirm.get())

        # Strip header
        if hasattr(self, "var_strip_suffix"): c["strip_append_stripped"] = bool(self.var_strip_suffix.get())

        c.save()
        self.destroy()
