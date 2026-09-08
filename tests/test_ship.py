from unittest.mock import Mock

import pytest

from Bot import sanderling as sm
from Bot import ship
from tests.test_sanderling import node


def hold(value="236,0/5.000,0 m³", kind="ShipGeneralMiningHold"):
    return node(
        "InventoryPrimary",
        children=[node(kind), node("CapacityGauge", children=[node(value=value)])],
    )


@pytest.mark.parametrize(
    "value", ["236,0/5.000,0 m³", "236.0/5,000.0 m³", "(20.0) 236.0 / 5,000.0 m³"]
)
def test_capacity_locales_and_selected_stack(value):
    assert ship.capacity(hold(value)) == (236, 5000)


def test_cargo_bay_is_never_mistaken_for_mining_hold():
    with pytest.raises(sm.UIElementError):
        ship.capacity(hold(kind="ShipCargo"))


def test_duplicate_holds_are_rejected():
    with pytest.raises(sm.UIElementError):
        ship.capacity(node(children=[hold(), hold()]))


def test_mining_tooltip_range_and_identity():
    tip = node(
        "ModuleButtonTooltip",
        children=[
            node(value="Modulated Strip Miner II"),
            node(value="Optimal range: 15.0 km"),
        ],
    )
    module = ship.tooltip_module((1, 2), tip)
    assert module.mining and not module.hardener
    assert module.range_m == 15000


def test_full_hold_returns_without_sending_mining_input():
    navigation = Mock()
    navigation.session.read.return_value = hold("4.900,0/5.000,0 m³")
    inputs = Mock()
    automatic = ship.AutomaticShip(navigation, inputs, Mock())
    automatic.mine(lambda: False)
    inputs.assert_not_called()
    navigation.click.assert_not_called()


def test_missing_capacity_aborts_without_mining_input():
    navigation = Mock()
    navigation.session.read.return_value = hold("unreadable")
    automatic = ship.AutomaticShip(navigation, Mock(), Mock())
    with pytest.raises(sm.UIElementError):
        automatic.mine(lambda: False)


def test_unload_empty_hold_does_not_drag():
    navigation = Mock()
    navigation.session.read.return_value = hold("0.0/5,000.0 m³")
    inputs = Mock()
    ship.AutomaticShip(navigation, inputs, Mock()).unload()
    inputs.drag.assert_not_called()


def test_target_bar_uses_ore_name_without_asteroid_prefix():
    target = node("TargetInBar", children=[node(value="Veldspar"), node(value="14 km")])
    snapshot = node(
        children=[target, node("TargetInBar", children=[node(value="Station")])]
    )
    assert ship.locked_ore(snapshot, {"veldspar"}) == [target]


def test_active_miners_are_not_toggled_or_reset():
    snapshot = hold()
    snapshot["children"].append(node("ModuleButton", ramp_active=True))
    navigation = Mock()
    navigation.session.read.return_value = snapshot
    automatic = ship.AutomaticShip(navigation, Mock(), Mock())
    automatic.modules = [ship.Module((40, 10), "Miner I", True, False, 15000)]
    stopped = Mock(side_effect=[False, True])
    automatic.mine(stopped)
    navigation.click.assert_not_called()


def test_unload_uses_observed_stack_and_hangar_then_verifies_empty():
    snapshot = hold()
    snapshot["children"][0]["children"].append(node("InvItem", x=200, y=100))
    snapshot["children"].append(
        node(
            "TreeViewEntryInventory",
            children=[
                node(
                    "Container",
                    x=10,
                    y=50,
                    _name="topCont_hangar",
                    children=[node(value="Item Hangar")],
                )
            ],
        )
    )
    navigation = Mock()
    navigation.session.read.return_value = snapshot
    navigation.session.screen.side_effect = lambda p: (p[0] - 1920, p[1])
    navigation.wait_for.side_effect = lambda predicate, description: predicate(
        hold("0.0/5,000.0 m³")
    ) or pytest.fail("Hold must be empty")
    inputs = Mock()
    ship.AutomaticShip(navigation, inputs, Mock()).unload()
    inputs.hotkey.assert_called_once_with("ctrl", "a")
    inputs.drag.assert_called_once_with((-1680, 110), (-1870, 60))
    navigation.wait_for.assert_called_once()
