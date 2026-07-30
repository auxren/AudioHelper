"""Tests for the Show Chopper (lite) pure helpers."""
from audiohelper.lite import guess_date, parse_loose_setlist, plan_output_name


def test_guess_date_from_path():
    assert guess_date(["/Vol/Tapes/2026-07-28/Raw Files/set1.wav"]) == "2026-07-28"
    assert guess_date(["/x/ph2026.07.28 set2.wav"]) == "2026-07-28"
    assert guess_date(["/x/nothing_here.wav"]) == ""


def test_parse_phishnet_paste():
    text = ("Set 1: The Moma Dance, Rift > Maze, Sample in a Jar\n"
            "Set 2: Tweezer > Prince Caspian > Sand\n"
            "Encore: Slave to the Traffic Light")
    sets = parse_loose_setlist(text)
    assert [label for label, _ in sets] == ["Set 1", "Set 2", "Encore"]
    s1 = sets[0][1]
    assert s1 == [("The Moma Dance", False), ("Rift", True),
                  ("Maze", False), ("Sample in a Jar", False)]
    s2 = sets[1][1]
    assert s2 == [("Tweezer", True), ("Prince Caspian", True), ("Sand", False)]
    assert sets[2][1] == [("Slave to the Traffic Light", False)]


def test_parse_etree_numbered_lines():
    text = ("Set 1:\n01. Ride Me High\n02. Driving Song >\n03. I'm Not Alone\n"
            "Encore:\n04. The Waker")
    sets = parse_loose_setlist(text)
    assert sets[0][1] == [("Ride Me High", False), ("Driving Song", True),
                          ("I'm Not Alone", False)]
    assert sets[1][0] == "Encore"


def test_parse_bare_lines_single_set():
    sets = parse_loose_setlist("Song One\nSong Two\nSong Three\n")
    assert len(sets) == 1
    assert sets[0][0] == "Set 1"
    assert [t for t, _ in sets[0][1]] == ["Song One", "Song Two", "Song Three"]


def test_parse_skips_metadata_lines():
    text = "Artist: Phish\nDate: 2026-07-28\nSet 1: Foam, Stash\n"
    sets = parse_loose_setlist(text)
    assert len(sets) == 1
    assert [t for t, _ in sets[0][1]] == ["Foam", "Stash"]


def test_parse_empty():
    assert parse_loose_setlist("") == []
    assert parse_loose_setlist("\n\n") == []


def test_plan_output_name():
    assert plan_output_name("Phish", "2026-07-28") == "ph2026-07-28"
    assert plan_output_name("Widespread Panic", "2026-07-18") == "wsp2026-07-18"
    assert plan_output_name("Some New Band", "2026-01-01") == "snb2026-01-01"
    assert plan_output_name("", "2026-01-01") == "show2026-01-01"


def test_parse_phishnet_html():
    from audiohelper.lite import parse_phishnet_html
    html = (
        "<div class='setlist-body'><p><i>Soundcheck</i>: Jam"
        "<p><span class='set-label'>SET 1</span>: "
        "<a href='/song/a' class='setlist-song' title='A'>Alpha</a> > "
        "<a data-toggle=\"tooltip\" href='/song/b' class='setlist-song' "
        "title='segued -> into &quot;Gamma&quot;.'>Beta</a>"
        "<sup title='note'>[1]</sup> -> "
        "<a href='/song/c' class='setlist-song' title='C'>Gamma</a>, "
        "<a href='/song/d' class='setlist-song' title='D'>Delta</a> "
        "<p><span class='set-label'>ENCORE</span>: "
        "<a href='/song/e' class='setlist-song' title='E'>Echo</a> "
        "<div class='footer'>x</div></div>")
    out = parse_phishnet_html(html)
    assert out == ("Set 1: Alpha > Beta > Gamma, Delta\n"
                   "Encore: Echo")
    assert parse_phishnet_html("<html>nope</html>") == ""
