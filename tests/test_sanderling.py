import json
import subprocess
from unittest.mock import Mock

import pytest

from Bot import sanderling as sm


def node(kind="EveLabelMedium", value="", x=0, y=0, children=None, **data):
    return {
        "pythonObjectTypeName": kind,
        "dictEntriesOfInterest": {
            "_text": value,
            "_displayX": x,
            "_displayY": y,
            "_displayWidth": 80,
            "_displayHeight": 20,
            **data,
        },
        "children": children or [],
    }


def bookmark(name, y=0):
    return node("PlaceEntry", children=[node(value=name, y=y)])


def asteroid(name, distance, y=0):
    # Deliberately reorder columns: names/distances must not rely on child indexes.
    return node(
        "OverviewScrollEntry", children=[node(value=distance), node(value=name, y=y)]
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("5 km", 5000),
        ("12 km", 12000),
        ("12.5 km", 12500),
        ("12,5 km", 12500),
        ("500 m", 500),
        ("1,500 m", 1500),
        ("1.500 m", 1500),
        ("1 500 m", 1500),
        ("1\u202f500 m", 1500),
        ("1’500 m", 1500),
        ("1,234.5 m", 1234.5),
        ("1.234,5 m", 1234.5),
        ("0 m", 0),
        ("15000.1 m", 15000.1),
    ],
)
def test_distances_preserve_fraction_and_grouping(value, expected):
    assert sm.parse_distance_in_meters(value) == expected


@pytest.mark.parametrize("value", ["", "unknown", "-5 km", "nan m", "1 AU", "1,2,3 m"])
def test_invalid_distances(value):
    with pytest.raises(ValueError):
        sm.parse_distance_in_meters(value)


def test_nested_coordinates_keep_parent_offset_and_do_not_mutate():
    leaf = node(x={"int_low32": 4}, y=5, _displayWidth={"int_low32": 60})
    tree = node(x=100, y=200, children=[{"children": [leaf]}])
    adjusted = sm.adjust_display_positions(tree)
    assert sm.get_center_position(adjusted["children"][0]["children"][0]) == (134, 215)
    assert leaf["dictEntriesOfInterest"]["_displayX"] == {"int_low32": 4}


def test_named_bookmarks_ignore_order_and_skip_previous():
    tree = node(
        children=[
            bookmark("Mining 2", 40),
            bookmark("Other"),
            bookmark("Home"),
            bookmark("Mining 1", 20),
        ]
    )
    assert sm.select_bookmark(tree, "HOME", "Mining", "mining 2")[0] == "mining 1"
    assert sm.select_bookmark(tree, "Home", "Mining", home=True)[0] == "home"


def test_single_mining_bookmark_can_be_reused():
    tree = node(children=[bookmark("Home"), bookmark("Mining 1")])
    assert sm.select_bookmark(tree, "Home", "Mining", "mining 1")[0] == "mining 1"


@pytest.mark.parametrize(
    "entries",
    [
        [bookmark("Mining 1")],
        [bookmark("Home"), bookmark("Other")],
        [bookmark("Home"), bookmark("Home"), bookmark("Mining 1")],
    ],
)
def test_missing_or_ambiguous_bookmarks_fail(entries):
    with pytest.raises(sm.UIElementError):
        sm.select_bookmark(node(children=entries), "Home", "Mining")


def test_asteroids_filter_type_range_and_sort_distance():
    tree = node(
        children=[
            asteroid("Station", "1 km", 10),
            asteroid("Asteroid (far)", "15.1 km", 20),
            asteroid("Asteroid (second)", "12.5 km", 30),
            asteroid("Asteroid (first)", "5 km", 40),
            asteroid("Asteroid (invalid)", "unknown", 50),
        ]
    )
    assert sm.find_asteroids(tree, 15000, r"^Asteroid\b") == [(40, 50), (40, 40)]


@pytest.mark.parametrize("rows", [[], [asteroid("Asteroid", "1 km")]])
def test_not_enough_targets_has_actionable_error(rows):
    with pytest.raises(sm.MiningTargetsUnavailable):
        sm.find_asteroids(node(children=rows), 15000, r"^Asteroid\b")


@pytest.mark.parametrize(
    "limit, pattern", [(0, "Asteroid"), (float("nan"), "Asteroid"), (1, ""), (1, "[")]
)
def test_invalid_target_configuration(limit, pattern):
    with pytest.raises(sm.UIElementError):
        sm.find_asteroids(node(), limit, pattern)


def test_missing_undock_fails():
    with pytest.raises(sm.UIElementError):
        sm.find_undock_button(node())


def test_hidden_bookmarks_are_not_clicked():
    hidden = node(children=[bookmark("Home")], _display=False)
    with pytest.raises(sm.UIElementError):
        sm.select_bookmark(
            node(children=[hidden, bookmark("Mining 1")]), "Home", "Mining"
        )


@pytest.fixture
def reader(tmp_path, monkeypatch):
    executable = tmp_path / "reader.exe"
    executable.touch()
    monkeypatch.setattr(sm.platform, "system", lambda: "Windows")
    return sm.MemoryReader(123, executable=executable)


def reading(address=456):
    return subprocess.CompletedProcess(
        [], 0, json.dumps({"pythonObjectAddress": address, "children": [node()]}), ""
    )


def test_reader_retries_cached_root_and_uses_timeout(reader, monkeypatch):
    reader.root_address = "1234"
    run = Mock(side_effect=[subprocess.CompletedProcess([], 1, "", "stale"), reading()])
    monkeypatch.setattr(sm.subprocess, "run", run)
    reader.read()
    assert "--root-address" in run.call_args_list[0].args[0]
    assert "--root-address" not in run.call_args_list[1].args[0]
    assert run.call_args.kwargs["timeout"] == 120
    assert run.call_args.args[0][-1] == "123"
    assert reader.root_address == "456"


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess([], 0, "not json", ""),
        subprocess.CompletedProcess([], 0, "{}", ""),
        subprocess.CompletedProcess([], 1, "", "failure"),
        subprocess.TimeoutExpired("reader", 120),
    ],
)
def test_reader_failures_are_bounded(reader, monkeypatch, result):
    run = (
        Mock(side_effect=result)
        if isinstance(result, Exception)
        else Mock(return_value=result)
    )
    monkeypatch.setattr(sm.subprocess, "run", run)
    with pytest.raises(sm.MemoryReadError):
        reader.read()
    assert run.call_count == 1
    assert reader.root_address is None


def test_session_reads_fresh_and_translates_window_origin(monkeypatch):
    monkeypatch.setattr(sm, "get_pid_by_hwnd", lambda hwnd: 123)
    monkeypatch.setattr(sm, "client_origin", lambda hwnd: (100, 200))
    session = sm.Session(1, "Home", "Mining", 15000, r"^Asteroid\b", 120)
    trees = [
        node(children=[bookmark("Home", 10)]),
        node(children=[bookmark("Home", 40)]),
    ]
    session.reader.read = Mock(side_effect=trees)
    assert session.bookmark(home=True) == (140, 220)
    assert session.bookmark(home=True) == (140, 250)
    assert session.reader.read.call_count == 2


def test_session_rejects_changed_process(monkeypatch):
    monkeypatch.setattr(sm, "get_pid_by_hwnd", lambda hwnd: 123)
    session = sm.Session(1, "Home", "Mining", 15000, "Asteroid", 120)
    monkeypatch.setattr(sm, "get_pid_by_hwnd", lambda hwnd: 456)
    with pytest.raises(sm.MemoryReadError, match="process changed"):
        session.read()


def test_null_children_from_live_reader():
    snapshot = node(children=[node()])
    snapshot["children"][0]["children"] = None
    assert len(list(sm.walk(sm.adjust_display_positions(snapshot)))) == 2


def ore_overview():
    headers = node(
        "SortHeaders",
        children=[
            node("Header", x=0, _displayWidth=100, children=[node(value="Distance")]),
            node(
                "Header", x=100, _displayWidth=180, children=[node(value="Name", x=100)]
            ),
            node(
                "Header", x=280, _displayWidth=100, children=[node(value="Type", x=280)]
            ),
            node(
                "Header", x=380, _displayWidth=100, children=[node(value="Size", x=380)]
            ),
        ],
    )
    rows = []
    for name, distance, y in [
        ("Plagioclase", "500 m", 100),
        ("Veldspar", "5 km", 130),
        ("Veldspar-II Grade", "12.5 km", 160),
    ]:
        rows.append(
            node(
                "OverviewScrollEntry",
                children=[
                    node("OverviewLabel", value="1.370 m", x=390, y=y),
                    node("OverviewLabel", value="Asteroid", x=290, y=y),
                    node("OverviewLabel", value=name, x=110, y=y),
                    node("OverviewLabel", value=distance, x=10, y=y),
                ],
            )
        )
    return node("OverviewWindow", children=[headers] + rows)


def test_distance_column_is_not_confused_with_size():
    result = sm.asteroid_candidates(ore_overview(), 15000, r"^Asteroid\b")
    assert [(item.name, item.distance) for item in result] == [
        ("Plagioclase", 500),
        ("Veldspar", 5000),
        ("Veldspar-II Grade", 12500),
    ]


def test_ore_priority_beats_distance_within_range():
    result = sm.asteroid_candidates(
        ore_overview(),
        15000,
        r"^Asteroid\b",
        ["Veldspar-II Grade", "Veldspar", "Plagioclase"],
    )
    assert [item.name for item in result] == [
        "Veldspar-II Grade",
        "Veldspar",
        "Plagioclase",
    ]


def test_ore_priority_never_exceeds_laser_range():
    result = sm.asteroid_candidates(
        ore_overview(), 10000, r"^Asteroid\b", ["Veldspar-II Grade", "Veldspar"]
    )
    assert [item.name for item in result] == ["Veldspar", "Plagioclase"]


def test_unlisted_ore_can_be_excluded():
    result = sm.asteroid_candidates(
        ore_overview(), 15000, r"^Asteroid\b", ["veldspar"], False
    )
    assert [item.name for item in result] == ["Veldspar"]
    with pytest.raises(sm.MiningTargetsUnavailable):
        sm.find_asteroids(ore_overview(), 15000, r"^Asteroid\b", ["Veldspar"], False)
