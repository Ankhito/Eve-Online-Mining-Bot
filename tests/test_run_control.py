"""Exercise GUI worker functions without constructing Tk or issuing real input."""

import ast
from pathlib import Path
from unittest.mock import Mock

from Bot import sanderling as sm


def worker(monkeypatch):
    tree = ast.parse(Path("Bot/bot.py").read_text())
    function = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "repeat_function"
    )
    session = Mock()
    session.undock.return_value = (10, 20)
    session.bookmark.return_value = (30, 40)
    config = Mock()
    config.get_mining_runs.return_value = 1
    config.get_mouse_reset_coo.return_value = (50, 60)
    config.get_clear_cargo_coo.return_value = (70, 80)
    root = Mock()
    root.after.side_effect = lambda delay, callback, *args: callback(*args)
    namespace = {
        "sm": sm,
        "tk": Mock(),
        "config": config,
        "root": root,
        "logger": Mock(),
        "fe": Mock(),
        "create_memory_session": lambda: session,
        "activate_eve_window": Mock(),
        "enable_fields": Mock(),
        "panic_button": Mock(),
        "update_mining_runs": Mock(),
        "run_active": True,
        "stop_flag": False,
        "panic_requested": False,
        "SMALL_SLEEP": 12,
        "LONG_SLEEP": 100,
        "warping_time": 70,
        "auto_reset_miners": False,
        "take_screenshots": False,
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "Bot/bot.py", "exec"),
        namespace,
    )
    return namespace, session


def test_insufficient_targets_return_home_and_restore_controls(monkeypatch):
    state, session = worker(monkeypatch)
    state["fe"].mining_behaviour.side_effect = sm.MiningTargetsUnavailable("empty belt")
    state["repeat_function"](1000)
    session.bookmark.assert_any_call(home=True)
    state["fe"].auto_dock_to_station.assert_called_once_with([30, 40])
    state["fe"].clear_cargo.assert_called_once_with(x=70, y=80)
    state["enable_fields"].assert_called_once()
    assert not state["run_active"]


def test_preflight_failure_performs_no_input(monkeypatch):
    state, session = worker(monkeypatch)
    session.validate.side_effect = sm.UIElementError("missing home")
    state["repeat_function"](1000)
    assert not state["fe"].mock_calls
    state["enable_fields"].assert_called_once()
    assert not state["run_active"]


def test_failed_docking_does_not_drag_cargo(monkeypatch):
    state, session = worker(monkeypatch)
    session.undock.side_effect = [(10, 20), sm.UIElementError("not docked")]
    state["repeat_function"](1000)
    state["fe"].clear_cargo.assert_not_called()
    state["enable_fields"].assert_called_once()


def test_panic_during_undock_skips_mining_and_returns_home(monkeypatch):
    state, session = worker(monkeypatch)
    state["fe"].undock.side_effect = lambda **kwargs: state.update(panic_requested=True)
    state["repeat_function"](1000)
    state["fe"].mining_behaviour.assert_not_called()
    state["fe"].click_top_left_circle_menu.assert_not_called()
    state["fe"].auto_dock_to_station.assert_called_once()
    session.bookmark.assert_called_once_with(home=True)
