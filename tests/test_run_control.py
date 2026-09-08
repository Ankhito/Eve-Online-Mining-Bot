"""Controller tests never create Tk windows or send game input."""
from unittest.mock import Mock

import pytest

from Bot import bot
from Bot import sanderling as sm
from Bot.config import ConfigHandler


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "config.properties"
    path.write_text(
        "[SETTINGS]\nautomation_mode=sanderling\nauto_navigation=False\nmining_runs=1\nmining_hold=5000\nmining_yield=3\n[POSITIONS]\nclear_cargo_coo=70,80\nmouse_reset_coo=50,60\n"
    )
    config = ConfigHandler(path)
    session = Mock()
    session.undock.return_value = (10, 20)
    session.bookmark.return_value = (30, 40)
    monkeypatch.setattr(bot, "make_session", lambda config, window: session)
    actions = Mock()
    monkeypatch.setattr(bot, "fe", actions)
    monkeypatch.setattr(bot, "AutomaticShip", Mock())
    events = []
    controller = bot.Controller(lambda key, value: events.append((key, value)))
    controller.busy = True
    return config, session, actions, controller, events


def test_insufficient_targets_return_home(setup):
    config, session, actions, controller, events = setup
    actions.mining_behaviour.side_effect = sm.MiningTargetsUnavailable("empty belt")
    controller.run(config)
    actions.auto_dock_to_station.assert_called_once_with([30, 40])
    actions.clear_cargo.assert_called_once_with(70, 80)
    assert not controller.busy
    assert events[-1] == ("done", "")


def test_preflight_failure_performs_no_input(setup):
    config, session, actions, controller, events = setup
    session.validate.side_effect = sm.UIElementError("missing home")
    controller.run(config)
    assert not actions.mock_calls
    assert not controller.busy
    assert ("error", "missing home") in events


def test_failed_docking_does_not_unload(setup):
    config, session, actions, controller, events = setup
    session.undock.side_effect = [(10, 20), sm.UIElementError("not docked")]
    controller.run(config)
    actions.clear_cargo.assert_not_called()
    assert not controller.busy


def test_panic_during_undock_skips_mining(setup):
    config, session, actions, controller, events = setup
    actions.undock.side_effect = lambda *args: controller.panic()
    controller.run(config)
    actions.mining_behaviour.assert_not_called()
    actions.click_top_left_circle_menu.assert_not_called()
    actions.auto_dock_to_station.assert_called_once()


def test_automatic_mode_needs_no_bookmarks_and_keeps_home_in_memory(setup, monkeypatch):
    config, session, actions, controller, events = setup
    config.config.set("SETTINGS", "auto_navigation", "True")
    navigation = Mock(home_station="Starting Station", last_belt="Belt 1")
    monkeypatch.setattr(bot.nav, "AutoNavigation", Mock(return_value=navigation))
    controller.run(config)
    session.validate.assert_called_once_with(require_bookmarks=False)
    navigation.prepare_docked.assert_called_once()
    navigation.travel.assert_called_once()
    navigation.dock.assert_called_once()
    session.bookmark.assert_not_called()
    assert ("home", "Starting Station") in events
    assert not config.config.has_option("SETTINGS", "home_station")


def test_automatic_error_attempts_home_without_unloading(setup, monkeypatch):
    config, session, actions, controller, events = setup
    config.config.set("SETTINGS", "auto_navigation", "True")
    navigation = Mock(home_station="Home")
    navigation.travel.side_effect = sm.UIElementError("menu missing")
    monkeypatch.setattr(bot.nav, "AutoNavigation", Mock(return_value=navigation))
    controller.run(config)
    navigation.dock.assert_called_once()
    actions.clear_cargo.assert_not_called()


def test_settings_validate_before_start(setup):
    config, session, actions, controller, events = setup
    config.config.set("SETTINGS", "mining_yield", "0")
    with pytest.raises(ValueError, match="yield"):
        bot.validate_config(config)


def test_empty_whitelist_rejected(setup):
    config, session, actions, controller, events = setup
    config.config.set("SETTINGS", "allow_unlisted_ores", "False")
    with pytest.raises(ValueError, match="priority"):
        bot.validate_config(config)


def test_activation_accepts_pygetwindow_error_when_eve_is_foreground(monkeypatch):
    window = Mock(_hWnd=123)
    window.activate.side_effect = RuntimeError(
        "Error code from Windows: 0 - The operation completed successfully."
    )
    controller = bot.Controller(Mock())
    controller.window = window
    force = Mock()
    monkeypatch.setattr(bot, "_is_foreground_window", lambda hwnd: hwnd == 123)
    monkeypatch.setattr(bot, "_force_foreground_window", force)

    controller.activate()

    force.assert_not_called()


def test_activation_uses_native_fallback_when_eve_is_not_foreground(monkeypatch):
    window = Mock(_hWnd=456)
    window.activate.side_effect = RuntimeError("Windows rejected activation")
    controller = bot.Controller(Mock())
    controller.window = window
    force = Mock()
    monkeypatch.setattr(bot, "_is_foreground_window", lambda hwnd: False)
    monkeypatch.setattr(bot, "_force_foreground_window", force)

    controller.activate()

    force.assert_called_once_with(456)


def test_automatic_mode_does_not_require_legacy_values(setup):
    config, _, _, _, _ = setup
    config.config.set("SETTINGS", "auto_navigation", "True")
    for key in ("mining_hold", "mining_yield", "mining_reset_timer", "mining_range_m"):
        config.config.set("SETTINGS", key, "")
    for key in ("mouse_reset_coo", "clear_cargo_coo"):
        config.config.set("POSITIONS", key, "")
    bot.validate_config(config)


def test_foreground_eve_is_not_reactivated_during_menu_hover(monkeypatch):
    controller = bot.Controller(Mock())
    controller.window = Mock(_hWnd=123)
    monkeypatch.setattr(bot, "_is_foreground_window", lambda hwnd: True)
    controller.activate()
    controller.window.activate.assert_not_called()
