"""Automatic track-boundary alignment for the Show Splitter (prototype).

Given (a) an RMS envelope of a continuous set recording, (b) an ordered
setlist, and (c) an optional rough transcript from a local Whisper run,
propose a split time for every track boundary plus a per-boundary
confidence, ready to load as Show Splitter markers.

No network, no API keys, no heavy deps — pure stdlib. The envelope comes
from decoding with ffmpeg (mono, low rate) and computing dB RMS frames;
the transcript from faster-whisper / whisper.cpp running locally. Song
titles double as chorus lyrics often enough that fuzzy title matching,
combined with dip prominence and duration priors, pins most boundaries;
the rest are flagged low-confidence for manual nudging in the editor.

Pipeline:
    dips  = find_dips(env_db, frame_sec)
    marks = align(setlist, dips, transcript, total_sec)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class Dip:
    """A candidate boundary: a local minimum of the RMS envelope."""
    time: float
    depth_db: float
    prominence: float


@dataclass
class SetlistEntry:
    title: str
    segue: bool = False          # segues into the NEXT track ('>')


@dataclass
class Boundary:
    """A proposed split point between track `index` and `index + 1`."""
    index: int
    time: float
    confidence: float            # 0..1
    lyric_score: float
    dip_prominence: float


# ── Envelope dip detection ────────────────────────────────────────────────────

def envelope_db(samples: list[float], duration: float,
                frame_sec: float = 0.25,
                smooth_sec: float = 3.0) -> tuple[list[float], float]:
    """Reduce a linear amplitude envelope (e.g. WaveformView._samples) to a
    median-smoothed dB RMS envelope. Returns (env_db, frame_sec)."""
    n = len(samples)
    if n == 0 or duration <= 0:
        return [], frame_sec
    per = max(1, int(n * frame_sec / duration))
    frames: list[float] = []
    for i in range(0, n - per + 1, per):
        acc = 0.0
        for j in range(i, i + per):
            acc += samples[j] * samples[j]
        rms = math.sqrt(acc / per)
        frames.append(20.0 * math.log10(rms + 1e-9))
    half = max(1, int(smooth_sec / frame_sec / 2))
    out: list[float] = []
    for i in range(len(frames)):
        w = sorted(frames[max(0, i - half):i + half + 1])
        out.append(w[len(w) // 2])
    return out, frame_sec


def find_dips(env_db: list[float], frame_sec: float,
              min_gap_sec: float = 90.0,
              min_prominence: float = 3.0) -> list[Dip]:
    """Local minima of a (pre-smoothed) dB envelope, prominence-filtered.

    Prominence of a minimum is how far the envelope rises on BOTH sides
    before dipping lower than the minimum again.
    """
    n = len(env_db)
    dist = max(1, int(min_gap_sec / frame_sec))
    dips: list[Dip] = []
    for i in range(1, n - 1):
        v = env_db[i]
        if env_db[i - 1] <= v or env_db[i + 1] < v:
            continue
        # Rise on each side until a value below v (or the edge).
        proms = []
        for step in (-1, 1):
            peak = v
            j = i + step
            while 0 <= j < n and env_db[j] >= v:
                peak = max(peak, env_db[j])
                j += step
            proms.append(peak - v)
        prom = min(proms)
        if prom >= min_prominence:
            dips.append(Dip(i * frame_sec, v, prom))
    # Enforce spacing: keep the deeper of any pair closer than min_gap_sec.
    dips.sort(key=lambda d: d.depth_db)
    kept: list[Dip] = []
    for d in dips:
        if all(abs(d.time - k.time) >= min_gap_sec for k in kept):
            kept.append(d)
    kept.sort(key=lambda d: d.time)
    return kept


# ── Transcript handling ───────────────────────────────────────────────────────

_WORD = re.compile(r"[a-z']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def clean_transcript(segments: list[tuple[float, float, str]],
                     max_repeats: int = 3) -> list[tuple[float, str]]:
    """Collapse Whisper hallucination loops (the same phrase repeated many
    times) and return (time, word) pairs."""
    out: list[tuple[float, str]] = []
    prev_text, run = None, 0
    for start, end, text in segments:
        norm = " ".join(_words(text))
        if not norm:
            continue
        if norm == prev_text:
            run += 1
            if run >= max_repeats:
                continue
        else:
            prev_text, run = norm, 1
        ws = _words(text)
        step = (end - start) / max(len(ws), 1)
        for i, w in enumerate(ws):
            out.append((start + i * step, w))
    return out


def title_matches(title: str, words: list[tuple[float, str]],
                  threshold: float = 0.72) -> list[tuple[float, float]]:
    """Times where the transcript fuzzily contains the title.

    Slides a window of len(title-words) over the transcript and scores
    with difflib. Returns (time, ratio) hits. Whisper mangles live vocals,
    so the threshold is forgiving; the aligner weighs ratios, not counts.
    """
    tw = _words(title)
    tw = [w for w in tw if len(w) > 2] or tw
    if not tw:
        return []
    target = " ".join(tw)
    span = len(tw)
    hits: list[tuple[float, float]] = []
    for i in range(len(words) - span + 1):
        window = " ".join(w for _, w in words[i:i + span])
        r = SequenceMatcher(None, target, window).ratio()
        if r >= threshold:
            hits.append((words[i][0], r))
    return hits


# ── Alignment ────────────────────────────────────────────────────────────────

def _cluster_hits(hits: list[tuple[float, float]],
                  gap: float = 120.0) -> list[list[tuple[float, float]]]:
    """Split title hits into temporal clusters (runs separated by > gap)."""
    clusters: list[list[tuple[float, float]]] = []
    for h in sorted(hits):
        if clusters and h[0] - clusters[-1][-1][0] <= gap:
            clusters[-1].append(h)
        else:
            clusters.append([h])
    return clusters


def _hits_per_entry(setlist: list[SetlistEntry],
                    words: list[tuple[float, str]]) -> list[list[tuple[float, float]]]:
    """Title hits for each setlist entry, with repeated titles (sandwiches
    like Driving Song > X > Driving Song) disambiguated: each temporal
    cluster of hits goes to one occurrence, clusters and occurrences taken
    in time/set order. Without this, a reprise's chorus pulls the first
    occurrence's boundary toward the reprise (off-by-one chains)."""
    raw = [title_matches(e.title, words) for e in setlist]
    by_title: dict[str, list[int]] = {}
    for i, e in enumerate(setlist):
        by_title.setdefault(e.title.lower(), []).append(i)
    out: list[list[tuple[float, float]]] = [[] for _ in setlist]
    for title, occs in by_title.items():
        if len(occs) == 1:
            out[occs[0]] = raw[occs[0]]
            continue
        clusters = _cluster_hits(raw[occs[0]])
        for k, cluster in enumerate(clusters):
            out[occs[min(k, len(occs) - 1)]].extend(cluster)
    return out


def _lyric_score(t: float, before_hits: list[tuple[float, float]],
                 after_hits: list[tuple[float, float]]) -> float:
    """Evidence that a boundary at time *t* is correct: the earlier song's
    title heard shortly before it, the later song's shortly after — minus
    the same evidence on the wrong side."""
    s = 0.0
    for ht, r in before_hits:
        dt = t - ht
        s += (r * math.exp(-dt / 240.0)) if dt >= 0 else -r * math.exp(dt / 60.0)
    for ht, r in after_hits:
        dt = ht - t
        s += (r * math.exp(-dt / 240.0)) if dt >= 0 else -r * math.exp(dt / 60.0)
    return s


def align(setlist: list[SetlistEntry], dips: list[Dip],
          transcript: list[tuple[float, float, str]],
          total_sec: float, min_track_sec: float = 100.0) -> list[Boundary]:
    """Assign one dip to each of the len(setlist)-1 boundaries (in order)
    by dynamic programming over dip choices.

    Score = lyric evidence + dip prominence + duration prior (tracks are
    roughly total/n long, very loose). Returns one Boundary per gap with
    a confidence from the DP margin and local evidence.
    """
    n = len(setlist)
    if n < 2 or not dips:
        return []
    words = clean_transcript(transcript)
    hits = _hits_per_entry(setlist, words)
    mean = total_sec / n
    sigma = 0.55 * mean

    def dur_prior(d: float) -> float:
        if d < min_track_sec:
            return -1e9
        return -0.5 * ((d - mean) / sigma) ** 2

    nb, nd = n - 1, len(dips)
    NEG = float("-inf")
    local = [[1.5 * _lyric_score(d.time, hits[b], hits[b + 1])
              + min(d.prominence, 20.0) / 10.0
              for d in dips] for b in range(nb)]

    # Forward DP: fwd[b][c] = best score of boundaries 0..b with b at dip c.
    fwd = [[NEG] * nd for _ in range(nb)]
    back = [[-1] * nd for _ in range(nb)]
    for c, d in enumerate(dips):
        fwd[0][c] = local[0][c] + dur_prior(d.time)
    for b in range(1, nb):
        for c, d in enumerate(dips):
            for p in range(c):
                if fwd[b - 1][p] == NEG:
                    continue
                s = fwd[b - 1][p] + local[b][c] + dur_prior(d.time - dips[p].time)
                if s > fwd[b][c]:
                    fwd[b][c] = s
                    back[b][c] = p
    # Backward DP: bwd[b][c] = best score of everything after boundary b,
    # given b sits at dip c (its own local score counted in fwd).
    bwd = [[NEG] * nd for _ in range(nb)]
    for c, d in enumerate(dips):
        bwd[nb - 1][c] = dur_prior(total_sec - d.time)
    for b in range(nb - 2, -1, -1):
        for c, d in enumerate(dips):
            for nc in range(c + 1, nd):
                if bwd[b + 1][nc] == NEG:
                    continue
                s = (local[b + 1][nc] + dur_prior(dips[nc].time - d.time)
                     + bwd[b + 1][nc])
                if s > bwd[b][c]:
                    bwd[b][c] = s

    best_c, best_s = -1, NEG
    for c in range(nd):
        if fwd[nb - 1][c] == NEG:
            continue
        s = fwd[nb - 1][c] + bwd[nb - 1][c]
        if s > best_s:
            best_c, best_s = c, s
    if best_c < 0:
        return []
    path = [best_c]
    for b in range(nb - 1, 0, -1):
        path.append(back[b][path[-1]])
    path.reverse()

    out = []
    for b, c in enumerate(path):
        d = dips[c]
        # Confidence = DP margin: how much worse the full show alignment
        # gets if this boundary is forced onto any OTHER dip. A repeated
        # chorus can inflate a local lyric score, but it can't fake a
        # margin — the alternative path would score nearly as well and
        # confidence stays low.
        second = NEG
        for c2 in range(nd):
            if c2 == c or fwd[b][c2] == NEG or bwd[b][c2] == NEG:
                continue
            second = max(second, fwd[b][c2] + bwd[b][c2])
        margin = best_s - second if second > NEG else float("inf")
        conf = 1.0 if margin == float("inf") else 1.0 - math.exp(-margin / 2.0)
        out.append(Boundary(b, d.time, round(min(conf, 1.0), 2),
                            round(local[b][c], 2), round(d.prominence, 1)))
    return out
