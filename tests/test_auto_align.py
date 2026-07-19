"""Tests for the automatic boundary aligner (auto_align)."""
import math

from audiohelper.auto_align import (
    Dip, SetlistEntry, align, clean_transcript, envelope_db, find_dips,
    title_matches,
)


# ── Envelope + dips ───────────────────────────────────────────────────────────

def _synth_env(total_sec, gaps, frame=0.25, loud=-12.0, quiet=-30.0):
    """Flat 'music' envelope with quiet gaps (each ~4 s) at the given times."""
    n = int(total_sec / frame)
    env = [loud] * n
    for g in gaps:
        c = int(g / frame)
        for i in range(max(0, c - 8), min(n, c + 8)):
            env[i] = quiet
    return env


def test_find_dips_finds_gaps():
    env = _synth_env(1200, [300.0, 700.0])
    dips = [d.time for d in find_dips(env, 0.25)]
    assert len(dips) == 2
    assert abs(dips[0] - 300.0) < 5 and abs(dips[1] - 700.0) < 5


def test_find_dips_prominence_filter():
    env = _synth_env(1200, [500.0], quiet=-13.5)   # 1.5 dB dip: not a gap
    assert find_dips(env, 0.25) == []


def test_find_dips_min_spacing():
    env = _synth_env(1200, [500.0, 530.0])         # two dips 30 s apart
    assert len(find_dips(env, 0.25, min_gap_sec=90)) == 1


def test_envelope_db_shape_and_level():
    # 100 s of amplitude 0.5 at 100 samples/s → flat envelope near -6 dB.
    samples = [0.5] * 10_000
    env, frame = envelope_db(samples, 100.0)
    assert frame == 0.25
    assert len(env) == int(100 / 0.25)
    assert all(abs(v - 20 * math.log10(0.5)) < 0.1 for v in env)


# ── Transcript helpers ────────────────────────────────────────────────────────

def test_clean_transcript_kills_hallucination_loops():
    segs = [(float(i * 2), float(i * 2 + 2), "leave you leave you")
            for i in range(30)]
    words = clean_transcript(segs)
    # Collapsed after max_repeats, far fewer than 30 segments' worth.
    assert 0 < len(words) <= 3 * 4


def test_title_matches_tolerates_garble():
    words = clean_transcript([(100.0, 103.0, "I'm just your big ill mama")])
    hits = title_matches("Bowlegged Woman", words)
    assert hits == []                        # unrelated garble: no match
    words = clean_transcript([(100.0, 103.0, "you should be glad tonight")])
    hits = title_matches("You Should Be Glad", words)
    assert hits and abs(hits[0][0] - 100.0) < 2


# ── Alignment ─────────────────────────────────────────────────────────────────

def test_align_prefers_lyric_evidence_over_uniform():
    # 3 tracks / 1200 s. Dips at 300 and 700 are true; 500 is a decoy
    # quiet-jam dip. "Song B" sung right after 300 anchors boundary 1.
    setlist = [SetlistEntry("Alpha Song"), SetlistEntry("Bravo Tune"),
               SetlistEntry("Charlie Jam")]
    dips = [Dip(300.0, -30, 15), Dip(500.0, -30, 15), Dip(700.0, -30, 15)]
    transcript = [(320.0, 324.0, "bravo tune yeah"),
                  (720.0, 724.0, "charlie jam")]
    bounds = align(setlist, dips, transcript, 1200.0)
    assert [b.time for b in bounds] == [300.0, 700.0]
    assert all(b.confidence > 0 for b in bounds)


def test_align_without_transcript_uses_priors():
    setlist = [SetlistEntry(f"Song {i}") for i in range(3)]
    dips = [Dip(400.0, -28, 12), Dip(800.0, -28, 12)]
    bounds = align(setlist, dips, [], 1200.0)
    assert [b.time for b in bounds] == [400.0, 800.0]


def test_align_respects_min_track_length():
    setlist = [SetlistEntry("A"), SetlistEntry("B")]
    dips = [Dip(20.0, -30, 15), Dip(600.0, -25, 8)]
    bounds = align(setlist, dips, [], 1200.0)
    assert bounds[0].time == 600.0           # 20 s first track is impossible


def test_align_segue_lowers_confidence():
    setlist = [SetlistEntry("Alpha", segue=True), SetlistEntry("Beta"),
               SetlistEntry("Gamma")]
    dips = [Dip(300.0, -30, 15), Dip(700.0, -30, 15)]
    hint = [(310.0, 314.0, "beta"), (710.0, 714.0, "gamma")]
    b_seg, b_gap = align(setlist, dips, hint, 1000.0)
    assert b_seg.confidence < b_gap.confidence


def test_align_empty_inputs():
    assert align([], [], [], 100.0) == []
    assert align([SetlistEntry("A")], [Dip(50, -30, 10)], [], 100.0) == []
    assert align([SetlistEntry("A"), SetlistEntry("B")], [], [], 100.0) == []
