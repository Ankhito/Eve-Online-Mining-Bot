"""Windows widget regression checks, without game input."""


import pytest

from Bot.ui import Application


@pytest.fixture
def app(tmp_path):
    path = tmp_path / "config.properties"
    path.write_text("[SETTINGS]\nore_priority=Veldspar\n    Plagioclase\n[POSITIONS]\n")
    window = Application(str(path))
    window.geometry("940x740")
    window.update()
    yield window
    for timer in window.tk.call("after", "info"):
        window.after_cancel(timer)
    window.destroy()


def test_action_bar_remains_visible_at_minimum_size(app):
    for button in (
        app.start_button,
        app.stop_button,
        app.panic_button,
        app.save_button,
    ):
        assert button.winfo_ismapped()
        bottom = button.winfo_rooty() - app.winfo_rooty() + button.winfo_height()
        assert bottom <= app.winfo_height()


def test_priority_reordering_and_save_round_trip(app):
    app.ore_entry.set("Veldspar-II Grade")
    app.add_ore()
    app.ore_list.selection_set(2)
    app.move_ore(-1)
    app.move_ore(-1)
    app.allow_unlisted.set(False)
    settings = app.read_settings()
    assert settings.get_ore_priority() == [
        "Veldspar-II Grade",
        "Veldspar",
        "Plagioclase",
    ]
    assert not settings.get_allow_unlisted_ores()


def test_worker_events_update_dashboard_and_restore_controls(app):
    app.controller.busy = True
    app.set_busy(True)
    assert str(app.start_button.cget("state")) == "disabled"
    app.events.put(("home", "Remembered station"))
    app.events.put(("progress", "2/5"))
    app.controller.busy = False
    app.events.put(("done", ""))
    app._drain()
    assert app.home.get() == "Remembered station"
    assert app.progress_text.get() == "2 / 5 trips"
    assert str(app.start_button.cget("state")) == "normal"
    assert str(app.panic_button.cget("state")) == "disabled"
