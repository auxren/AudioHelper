"""Tools → Update all CLI tools dialog."""

import threading
import tkinter as tk
from tkinter import ttk

from .tools import TOOLS, get_tool
from .prereqs import REQUIRED_TOOL_NAMES


class UpdateToolsDialog(tk.Toplevel):
    def __init__(self, parent, config, log_callback):
        super().__init__(parent)
        self.title("Update CLI tools")
        self.config_obj = config
        self.log = log_callback
        self.transient(parent)
        self.geometry("860x500")
        self.minsize(700, 380)
        self.resizable(True, True)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ── Header note ────────────────────────────────────────────────────
        ttk.Label(
            frm,
            text=(
                "Gray / italic rows are legacy tools with no upstream updates. "
                "They still work if installed manually."
            ),
            foreground="gray",
            font=("Segoe UI", 8, "italic"),
        ).pack(anchor="w", pady=(0, 6))

        # ── Tool table ─────────────────────────────────────────────────────
        cols = ("tool", "installed", "current", "latest", "status")
        widths = (120, 70, 130, 130, 270)
        tv_frame = ttk.Frame(frm)
        tv_frame.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(
            tv_frame, columns=cols, show="headings", selectmode="extended"
        )
        for c, w in zip(cols, widths):
            self.tv.heading(c, text=c.title(), anchor="w")
            self.tv.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Row styles
        self.tv.tag_configure("prereq",  foreground="#000000")
        self.tv.tag_configure("optional", foreground="#333333")
        self.tv.tag_configure("legacy",  foreground="#999999",
                               font=("Segoe UI", 9, "italic"))

        for t in TOOLS:
            if t.name in REQUIRED_TOOL_NAMES:
                tag = "prereq"
            elif t.legacy:
                tag = "legacy"
            else:
                tag = "optional"
            self.tv.insert(
                "", "end", iid=t.name,
                values=(t.name, "?", "?",
                        "(legacy — no updates)" if t.legacy else "?",
                        t.description),
                tags=(tag,),
            )

        # ── Buttons ────────────────────────────────────────────────────────
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(btns, text="Check versions",
                   command=self._check_versions).pack(side="left")
        ttk.Button(btns, text="Update selected",
                   command=lambda: self._update(
                       [n for n in self.tv.selection()
                        if not _is_legacy(n)]
                   )).pack(side="left", padx=4)
        ttk.Button(btns, text="Update all auto-updatable",
                   command=lambda: self._update(
                       [t.name for t in TOOLS
                        if t.upstream_install and not t.legacy]
                   )).pack(side="left")
        ttk.Button(btns, text="Install prerequisites",
                   command=self._install_prereqs).pack(side="left", padx=(16, 0))
        ttk.Button(btns, text="Open homepage",
                   command=self._open_homepage).pack(side="left", padx=8)
        ttk.Button(btns, text="Close",
                   command=self.destroy).pack(side="right")

        self.after(50, self._check_versions)

    # ── helpers ────────────────────────────────────────────────────────────

    def _set(self, name: str, col: str, value: str) -> None:
        idx = {"tool": 0, "installed": 1, "current": 2, "latest": 3, "status": 4}[col]

        def f():
            if not self.tv.exists(name):
                return
            vals = list(self.tv.item(name, "values"))
            vals[idx] = value
            self.tv.item(name, values=vals)

        self.after(0, f)

    def _check_versions(self) -> None:
        def worker():
            for tool in TOOLS:
                self._set(tool.name, "status", "checking…")
                p = tool.path(self.config_obj)
                installed = p.exists()
                self._set(tool.name, "installed", "yes" if installed else "no")
                self._set(tool.name, "current",
                          (tool.current_version(self.config_obj) or "?") if installed else "-")
                if tool.legacy:
                    self._set(tool.name, "latest", "(legacy — no updates)")
                    self._set(tool.name, "status",
                              tool.description + "  [legacy / abandoned]")
                    continue
                if tool.upstream_check:
                    try:
                        latest = tool.upstream_check() or "?"
                    except Exception as e:
                        latest = "check error"
                        self.log(f"[update] {tool.name} version check failed: {e}\n")
                else:
                    latest = "(manual install)"
                self._set(tool.name, "latest", latest)
                self._set(tool.name, "status", tool.description)

        threading.Thread(target=worker, daemon=True).start()

    def _update(self, names: list[str]) -> None:
        if not names:
            return

        def worker():
            for name in names:
                try:
                    tool = get_tool(name)
                except KeyError:
                    continue
                if tool.legacy:
                    self._set(name, "status", "legacy — manual install only")
                    continue
                if not tool.upstream_install or not tool.upstream_check:
                    self._set(name, "status", "manual install — see Open homepage")
                    continue
                self._set(name, "status", "fetching version…")
                try:
                    latest = tool.upstream_check()
                except Exception as e:
                    self._set(name, "status", f"check failed: {e}")
                    self.log(f"[update] {name} check failed: {e}\n")
                    continue
                if not latest:
                    self._set(name, "status", "could not determine latest version")
                    continue
                self._set(name, "status", f"installing {latest}…")
                try:
                    tool.upstream_install(latest)
                except Exception as e:
                    self._set(name, "status", f"install failed: {e}")
                    self.log(f"[update] {name} install failed: {e}\n")
                    continue
                cur = tool.current_version(self.config_obj) or latest
                self._set(name, "current", cur)
                self._set(name, "installed", "yes")
                self._set(name, "status", f"updated to {latest}")
                self.log(f"[update] {name} → {latest}\n")

        threading.Thread(target=worker, daemon=True).start()

    def _install_prereqs(self) -> None:
        from .prereqs import PrereqDialog
        PrereqDialog(self, self.config_obj, self.log)

    def _open_homepage(self) -> None:
        import webbrowser
        for name in self.tv.selection():
            try:
                t = get_tool(name)
            except KeyError:
                continue
            if t.homepage:
                webbrowser.open(t.homepage)


def _is_legacy(name: str) -> bool:
    try:
        return get_tool(name).legacy
    except KeyError:
        return False
