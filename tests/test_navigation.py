from unittest.mock import Mock

import pytest

from Bot import navigation as nav
from Bot import sanderling as sm
from tests.test_sanderling import node


def docked(name="Test IV - Moon 1 - Warehouse"):
    return node(
        children=[
            node("LobbyWnd", children=[node(value="Undock")]),
            node(
                "InfoPanelLocationInfo",
                children=[
                    node(value=f"<url=showinfo:1//2 alt='Current Station'>{name}</url>")
                ],
            ),
        ]
    )


def test_station_is_captured_only_when_docked():
    assert nav.station_name(docked()) == "Test IV - Moon 1 - Warehouse"
    with pytest.raises(sm.UIElementError):
        nav.station_name(
            node("InfoPanelLocationInfo", children=[node(value="Nearest station")])
        )


def test_nearest_location_is_not_mistaken_for_home():
    snapshot = docked()
    snapshot["children"][1]["children"][0]["dictEntriesOfInterest"][
        "_text"
    ] = "<url alt='Nearest'>Somewhere</url>"
    with pytest.raises(sm.UIElementError):
        nav.station_name(snapshot)


def test_station_name_handles_quotes_and_markup():
    snapshot = docked()
    snapshot["children"][1]["children"][0]["dictEntriesOfInterest"][
        "_text"
    ] = '<a href="showinfo:1//2" alt="Current Station"><b>Test &amp; Co</b></a>'
    assert nav.station_name(snapshot) == "Test & Co"


def test_station_menu_requires_unique_name():
    items = [nav.MenuItem("Test - M1", (1, 2))]
    assert nav.exact_item(items, "Test - Moon 1").position == (1, 2)
    with pytest.raises(sm.UIElementError):
        nav.exact_item(items * 2, "Test - Moon 1")
    with pytest.raises(sm.UIElementError):
        nav.exact_item(items, "Test")


def test_menu_parser_scopes_to_menu_layer_and_handles_null_children():
    layer = node(
        children=[
            node(
                "Menu",
                children=[node("MenuEntryView", children=[node(value="Stations")])],
            )
        ],
        _name="l_menu",
    )
    layer["children"][0]["children"][0]["children"][0]["children"] = None
    snapshot = node(children=[node(value="Stations"), layer])
    assert [item.name for item in nav.menu_groups(snapshot)[0]] == ["Stations"]


def test_location_menu_cascade_is_independent_of_layer_z_order():
    menus = [
        node(
            "Menu", children=[node("MenuEntryView", x=x, children=[node(value=label)])]
        )
        for x, label in [(500, "Dock"), (300, "Station"), (100, "Stations")]
    ]
    snapshot = node(_name="l_menu", children=menus)
    assert [g[0].name for g in nav.menu_groups(snapshot)] == [
        "Stations",
        "Station",
        "Dock",
    ]


@pytest.fixture
def navigation():
    session = Mock()
    session.screen.side_effect = lambda position: position
    session.read.return_value = docked()
    result = nav.AutoNavigation(session, Mock(), lambda: False, timeout=0.1)
    result.prepare_docked()
    return result


def test_prepare_only_remembers_station_without_input(navigation):
    assert navigation.home_station == "Test IV - Moon 1 - Warehouse"
    assert not navigation.inputs.mock_calls


def test_explicit_warp_zero_is_clicked(navigation):
    navigation.warp_zero([nav.MenuItem("Warp to Within 0 m", (3, 4))])
    navigation.inputs.click.assert_called_once_with(3, 4, button="left")


def test_warp_zero_submenu_does_not_use_default_distance(navigation, monkeypatch):
    expand = Mock(
        return_value=[
            nav.MenuItem("Within 0 m", (5, 6)),
            nav.MenuItem("Within 10 km", (7, 8)),
        ]
    )
    monkeypatch.setattr(navigation, "expand", expand)
    navigation.warp_zero([nav.MenuItem("Warp to", (1, 2))])
    navigation.inputs.click.assert_called_once_with(5, 6, button="left")


def test_wrong_warp_distance_is_rejected(navigation):
    with pytest.raises(sm.UIElementError):
        navigation.warp_zero([nav.MenuItem("Warp to Within 10 km", (3, 4))])
    navigation.inputs.click.assert_not_called()


def test_panic_does_not_cancel_its_own_return(navigation, monkeypatch):
    navigation.cancelled = lambda: True
    navigation.session.read.side_effect = [node(), node(), docked()]
    monkeypatch.setattr(
        navigation, "station_menu", lambda: [nav.MenuItem("Dock", (9, 10))]
    )
    navigation.dock()
    navigation.inputs.click.assert_called_once_with(9, 10, button="left")
    assert not navigation.returning_home


def test_return_when_already_home_sends_no_input(navigation):
    navigation.dock()
    assert not navigation.inputs.mock_calls


def test_wrong_docked_station_is_rejected(navigation, monkeypatch):
    navigation.session.read.return_value = docked("Another Station")
    monkeypatch.setattr(
        navigation, "station_menu", lambda: [nav.MenuItem("Dock", (9, 10))]
    )
    with pytest.raises(sm.UIElementError, match="does not match"):
        navigation.dock()


def test_wait_is_bounded(navigation):
    navigation.timeout = 0
    with pytest.raises(sm.UIElementError, match="Timed out"):
        navigation.wait_for(lambda snapshot: False, "menu")


def test_wait_cancelled_before_read(navigation):
    navigation.session.read.reset_mock()
    navigation.cancelled = lambda: True
    with pytest.raises(nav.Cancelled):
        navigation.wait_for(lambda snapshot: True, "menu")
    navigation.session.read.assert_not_called()


def test_open_menu_does_not_press_escape_when_no_menu_exists(navigation, monkeypatch):
    snapshot = node(children=[node("ListSurroundingsBtn")])
    navigation.session.read.return_value = snapshot
    menu = node(
        children=[
            node(
                "Menu",
                children=[node("MenuEntryView", children=[node(value="Stations")])],
            )
        ],
        _name="l_menu",
    )
    monkeypatch.setattr(
        navigation, "wait_for", Mock(return_value=node(children=[menu]))
    )
    assert navigation.open_surroundings()[0].name == "Stations"
    navigation.inputs.hotkey.assert_not_called()


def test_empty_belts_are_tried_with_a_bound(navigation, monkeypatch):
    attempt = Mock(side_effect=sm.MiningTargetsUnavailable("empty"))
    monkeypatch.setattr(navigation, "_travel_one", attempt)
    with pytest.raises(sm.MiningTargetsUnavailable, match="three belts"):
        navigation.travel()
    assert attempt.call_count == 3


def test_second_belt_can_satisfy_priorities(navigation, monkeypatch):
    attempt = Mock(side_effect=[sm.MiningTargetsUnavailable("empty"), None])
    monkeypatch.setattr(navigation, "_travel_one", attempt)
    navigation.travel()
    assert attempt.call_count == 2


def test_hud_alone_is_not_ready_after_undock():
    assert not nav.space_ready(node("ShipUI"))
    ready = node(
        children=[
            node("ShipUI"),
            node("ModuleButton"),
            node("ListSurroundingsBtn"),
            node("OverviewWindow"),
        ]
    )
    assert nav.space_ready(ready)
    ready["children"].append(node("LoadingWnd"))
    assert not nav.space_ready(ready)


def test_readiness_must_remain_stable(navigation, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(nav.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(nav, "space_ready", lambda s: s["ready"])
    results = []

    def wait(predicate, description):
        for timestamp, ready in [
            (0, True),
            (5, True),
            (6, False),
            (7, True),
            (18, True),
            (19, True),
        ]:
            clock[0] = timestamp
            results.append(predicate({"ready": ready}))

    monkeypatch.setattr(navigation, "wait_for", wait)
    navigation.wait_until_ready()
    assert results == [False, False, False, False, False, True]


def test_cascade_moves_horizontally_before_selecting_child(navigation, monkeypatch):
    parent = nav.MenuItem("Asteroid Belts", (10, 30))
    child = nav.MenuItem("Belt 1", (150, 120))
    action = nav.MenuItem("Warp to", (300, 120))
    monkeypatch.setattr(nav, "menu_groups", lambda s: [[parent], [child], [action]])
    monkeypatch.setattr(navigation, "pause", Mock())
    navigation.menu_hover = parent.position
    navigation.expand(child)
    assert navigation.inputs.move.call_args_list[0].args == (150, 30)
    assert navigation.inputs.move.call_args_list[1].args == (150, 120)
