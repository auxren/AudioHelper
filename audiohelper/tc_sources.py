"""Source and microphone detection for live recordings.

Ported from TagCleaner (github.com/auxren/TagCleaner) — detects SBD/AUD/FM
and identifies mic/recorder models from folder names, filenames, and info.txt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SourceInfo:
    kind: str | None = None       # SBD | AUD | FM | Pre-FM | Matrix | DAT
    mics: list[str] = field(default_factory=list)
    taper: str | None = None

    def label(self) -> str:
        parts: list[str] = []
        if self.kind:
            parts.append(self.kind)
        parts.extend(self.mics)
        return ("[" + " ".join(parts) + "]") if parts else ""


_KIND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bpre[\s._-]*fm\b", re.I), "Pre-FM"),
    (re.compile(r"\bmtx\b|\bmatrix\b", re.I), "Matrix"),
    (re.compile(r"\bsbd\b|\bsoundboard\b|\bboard\b", re.I), "SBD"),
    (re.compile(r"\baud\b|\baudience\b", re.I), "AUD"),
    (re.compile(r"\bfm\b|\bbroadcast\b|\bpre-?broadcast\b", re.I), "FM"),
    (re.compile(r"\bdigital\s*master\b|\bdat\b", re.I), "DAT"),
]

_MIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bschoeps\s*(ccm\s*\d+\w*|cmc\s*\d+\w*|mk\s*\d+\w*|m\s*\d+\w*)", re.I), "Schoeps {1}"),
    (re.compile(r"\bmk\s*(4|41|5|6|21|22|2s)\b", re.I), "Schoeps MK{1}"),
    (re.compile(r"\bakg\s*(c?\s*\d{2,3}[a-z]*|ck\s*\d+\w*)", re.I), "AKG {1}"),
    (re.compile(r"\bakg\b", re.I), "AKG"),
    (re.compile(r"\bneumann\s*(km\s*\d+\w*|u\s*\d+\w*|tlm\s*\d+)", re.I), "Neumann {1}"),
    (re.compile(r"\bneumann\b", re.I), "Neumann"),
    (re.compile(r"\bsennheiser\s*(mkh?\s*\d+\w*|mke\s*\d+|md\s*\d+\w*|\d{3,4})", re.I), "Sennheiser {1}"),
    (re.compile(r"\bmkh\s*(\d+\w*)", re.I), "Sennheiser MKH{1}"),
    (re.compile(r"\bdpa\s*(\d{4})", re.I), "DPA {1}"),
    (re.compile(r"\bdpa\b", re.I), "DPA"),
    (re.compile(r"\bnak(?:amichi)?\s*(cm-?\s*\d+\w*|dr-?\s*\d+\w*|cr-?\s*\d+\w*)", re.I), "Nak {1}"),
    (re.compile(r"\bnak\s*(\d+\w*)", re.I), "Nak {1}"),
    (re.compile(r"\bnak\b", re.I), "Nak"),
    (re.compile(r"\bsony\s*(pcm-?[dm]\s*\d+\w*|tc-?d\s*\d+\w*|d\s*\d+)", re.I), "Sony {1}"),
    (re.compile(r"\bcore\s*sound\s*(\w+)?", re.I), "CoreSound {1}"),
    (re.compile(r"\bbinaural\b", re.I), "Binaural"),
    (re.compile(r"\bshure\s*(ksm\d+|sm\d+)", re.I), "Shure {1}"),
    (re.compile(r"\bmicrotech\s*gefell\s*([a-z]{0,4}\s*\d+\w*)", re.I), "MT Gefell {1}"),
    (re.compile(r"\bmicrotech\s*gefell\b", re.I), "MT Gefell"),
    (re.compile(r"\bmilab\s*(vm-?\s*\d+\w*(?:\s*link)?|[a-z]+\d+\w*)", re.I), "Milab {1}"),
    (re.compile(r"\bearthworks\s*(sr\s*\d+\w*|qtc\s*\d+\w*|[a-z]{1,3}\s*\d+\w*)", re.I), "Earthworks {1}"),
    (re.compile(r"\baudio[-\s]?technica\s*(at\s*\d+\w*)", re.I), "AT {1}"),
    (re.compile(r"\bat\s*(\d{3,4}\w*)\b", re.I), "AT {1}"),
    (re.compile(r"\btascam\s*(dr-?\s*\d+\w*|da-?\s*\d+\w*|hd-?p?\s*\d+\w*)", re.I), "Tascam {1}"),
    (re.compile(r"\btascam\b", re.I), "Tascam"),
    (re.compile(r"\bedirol\s*(r-?\s*\d+\w*|ua-?\s*\d+\w*)", re.I), "Edirol {1}"),
    (re.compile(r"\bedirol\b", re.I), "Edirol"),
    (re.compile(r"\bzoom\s*([hf]\s*\d+\w*)", re.I), "Zoom {1}"),
    (re.compile(r"\bsound\s*devices\s*(mix\s*pre\s*\d+\w*|\d{3,4}\w*)", re.I), "SD {1}"),
]


def detect_source(*texts: str) -> SourceInfo:
    """Scan strings (folder name, filenames, info.txt) and return a SourceInfo."""
    blob = "  ".join(t or "" for t in texts)

    kind: str | None = None
    for pat, label in _KIND_PATTERNS:
        if pat.search(blob):
            kind = label
            break

    mics: list[str] = []
    seen: set[str] = set()
    for pat, template in _MIC_PATTERNS:
        for m in pat.finditer(blob):
            groups = [m.group(0)] + list(m.groups())
            try:
                label = template.format(*groups)
            except IndexError:
                label = template
            label = re.sub(r"\s+", " ", label).strip()
            key = label.lower()
            if key in seen or not label:
                continue
            seen.add(key)
            mics.append(label)

    specific = [m for m in mics if re.search(r"\d", m)]
    families_with_spec = {m.split()[0].lower() for m in specific}
    mics = [m for m in mics if re.search(r"\d", m) or m.split()[0].lower() not in families_with_spec]

    return SourceInfo(kind=kind, mics=mics)
