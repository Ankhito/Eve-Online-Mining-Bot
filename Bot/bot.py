"""Mining controller. Importing this module never creates a GUI or starts input."""

from __future__ import annotations

import copy
import ctypes
import math
import threading
import time
from datetime import datetime
from typing import Any, Callable

import pyautogui
from loguru import logger

from Bot import functions as fe
from Bot import navigation as nav
from Bot import sanderling as sm
from Bot.config import ConfigHandler
from Bot.ship import AutomaticShip

EventSink = Callable[[str, str], None]


def _is_foreground_window(hwnd: int) -> bool:
    return bool(ctypes.windll.user32.GetForegroundWindow() == hwnd)


def _force_foreground_window(hwnd: int) -> None:
    """Activate a Windows window even when SetForegroundWindow is restricted."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.IsWindow(hwnd):
        raise RuntimeError("The selected EVE window is no longer available")
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground = user32.GetForegroundWindow()
    foreground_thread = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    attached_target = bool(
        target_thread
        and target_thread != current_thread
        and user32.AttachThreadInput(current_thread, target_thread, True)
    )
    attached_foreground = bool(
        foreground_thread
        and foreground_thread not in (current_thread, target_thread)
        and user32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)
    if not _is_foreground_window(hwnd):
        raise RuntimeError(
            "Windows would not focus EVE. Click the EVE client once, then press Start again."
        )


def validate_config(config: ConfigHandler) -> None:
    automatic = (
        config.get_automation_mode() == "sanderling" and config.get_auto_navigation()
    )
    if automatic:
        for label, value in (
            ("Mining runs", config.get_mining_runs()),
            ("Memory read timeout", config.get_memory_read_timeout()),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be a positive number")
        if not config.get_allow_unlisted_ores() and not config.get_ore_priority():
            raise ValueError("Add at least one ore priority or allow unlisted ores")
        return
    for label, value in (
        ("Mining runs", config.get_mining_runs()),
        ("Mining hold", config.get_mining_hold()),
        ("Mining yield", config.get_mining_yield()),
        ("Mining range", config.get_mining_range()),
        ("Memory read timeout", config.get_memory_read_timeout()),
        ("Laser cycle", config.get_mining_reset_timer()),
        ("Warp wait", config.get_warping_time()),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be a positive number")
    if not config.get_allow_unlisted_ores() and not config.get_ore_priority():
        raise ValueError("Add at least one ore priority or allow unlisted ores")
    positions = [
        ("Cargo unload", config.get_clear_cargo_coo()),
        ("Mouse reset", config.get_mouse_reset_coo()),
    ]
    if config.get_automation_mode() == "coordinates":
        positions += [
            ("Undock", config.get_undock_coo()),
            ("Home", config.get_home_coo()),
            ("Target one", config.get_target_one_coo()),
            ("Target two", config.get_target_two_coo()),
        ]
        belts = config.get_mining_coo()
        if not belts:
            raise ValueError("Add at least one belt coordinate")
        positions += [("Belt", value) for value in belts]
    for label, coordinates in positions:
        if len(coordinates) != 2:
            raise ValueError(f"{label} needs two coordinates: x, y")


def make_session(config: ConfigHandler, window: Any) -> sm.Session | None:
    if config.get_automation_mode() == "coordinates":
        return None
    if window is None:
        raise sm.MemoryReadError("Select an EVE window first")
    return sm.Session(
        window._hWnd,
        config.get_home_bookmark_name(),
        config.get_mining_bookmark_prefix(),
        15000 if config.get_auto_navigation() else config.get_mining_range(),
        config.get_asteroid_name_pattern(),
        config.get_memory_read_timeout(),
        config.get_ore_priority(),
        config.get_allow_unlisted_ores(),
    )


class NavigationInput:
    def __init__(self, activate: Callable[[], None]):
        self.activate = activate

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.activate()
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.mouseDown(button=button)
        try:
            time.sleep(0.15)
        finally:
            pyautogui.mouseUp(button=button)

    def move(self, x: int, y: int) -> None:
        self.activate()
        pyautogui.moveTo(x, y, duration=0.15)

    def hotkey(self, *keys: str) -> None:
        self.activate()
        pyautogui.hotkey(*keys)

    def drag(self, source: sm.Position, destination: sm.Position) -> None:
        self.activate()
        pyautogui.moveTo(*source, duration=0.3)
        pyautogui.dragTo(*destination, duration=1, button="left")


class Controller:
    def __init__(self, emit: EventSink):
        self.emit = emit
        self.stop_requested = threading.Event()
        self.panic_requested = threading.Event()
        self.busy = False
        self.lock = threading.Lock()
        self.window: Any = None

    def activate(self) -> None:
        if self.window is not None:
            hwnd = int(self.window._hWnd)
            if _is_foreground_window(hwnd):
                return
            try:
                self.window.activate()
            except Exception as error:
                # PyGetWindow can raise with Windows error code 0 even when the
                # requested window was successfully activated.
                logger.debug("PyGetWindow activation fallback: {}", error)
            if not _is_foreground_window(hwnd):
                _force_foreground_window(hwnd)

    def start(self, config: ConfigHandler, window: Any) -> None:
        validate_config(config)
        if config.get_automation_mode() == "sanderling" and window is None:
            raise ValueError("Select an EVE window first")
        with self.lock:
            if self.busy:
                raise ValueError("A run is already active")
            self.busy = True
            self.window = window
            self.stop_requested.clear()
            self.panic_requested.clear()
        # Settings cannot change underneath the worker.
        threading.Thread(
            target=self.run, args=(copy.deepcopy(config),), daemon=True
        ).start()

    def stop(self) -> None:
        self.stop_requested.set()
        self.emit(
            "status",
            "Stop requested; returning after the current action (legacy mode finishes its cycle)",
        )

    def panic(self) -> None:
        self.stop_requested.set()
        self.panic_requested.set()
        self.emit("status", "Return requested; waiting for the current action")

    def phase(self, value: str) -> None:
        self.emit("phase", value)
        self.emit("log", value)

    def run(self, config: ConfigHandler) -> None:
        navigation: nav.AutoNavigation | None = None
        ship: AutomaticShip | None = None
        session: sm.Session | None = None
        in_space = False
        completed = 0
        runs = config.get_mining_runs()
        automatic = (
            config.get_automation_mode() == "sanderling"
            and config.get_auto_navigation()
        )
        loading_time = (
            0.0 if automatic else config.get_mining_hold() / config.get_mining_yield()
        )
        self.emit("progress", f"0/{runs}")
        self.emit("home", "Reading starting station?")
        try:
            self.phase("Checking setup")
            session = make_session(config, self.window)
            if session:
                self.activate()
                session.validate(require_bookmarks=not config.get_auto_navigation())
                if config.get_auto_navigation():
                    navigation = nav.AutoNavigation(
                        session,
                        NavigationInput(self.activate),
                        self.panic_requested.is_set,
                    )
                    navigation.prepare_docked()
                    ship = AutomaticShip(
                        navigation, NavigationInput(self.activate), self.emit
                    )
                    ship.hold()
                    self.emit("home", navigation.home_station)
                else:
                    self.emit("home", config.get_home_bookmark_name())
            else:
                self.emit("home", "Configured home coordinates")
            while not self.stop_requested.is_set() and completed < runs:
                self.phase("Undocking")
                self.activate()
                x, y = session.undock() if session else config.get_undock_coo()
                if self.panic_requested.is_set():
                    break
                fe.undock(x, y)
                in_space = True
                if navigation:
                    self.phase("Waiting for space to finish loading")
                    navigation.wait_until_ready()
                    if ship:
                        ship.discover()
                else:
                    fe.sleep_and_log(12)
                end_after_return = False
                try:
                    if not self.panic_requested.is_set():
                        if not ship:
                            fe.set_hardener_online(config.get_hardener_keys())
                        self.phase("Travelling to asteroid belt")
                        if navigation:
                            navigation.travel()
                            self.emit("belt", navigation.last_belt or "Asteroid belt")
                        else:
                            target = (
                                session.bookmark()
                                if session
                                else fe.get_random_coord(config.get_mining_coo())
                            )
                            if not self.panic_requested.is_set():
                                fe.click_top_left_circle_menu(*target)
                                fe.sleep_and_log(config.get_warping_time())
                    if not self.panic_requested.is_set():
                        self.phase("Mining")
                        if ship:
                            ship.mine(self.stop_requested.is_set)
                        else:
                            self.mine_legacy(config, session, loading_time)
                except (sm.MiningTargetsUnavailable, nav.Cancelled) as error:
                    self.emit("log", str(error))
                    end_after_return = True
                self.phase("Returning home")
                self.emit("deadline", "0")
                self.activate()
                if not ship:
                    fe.drone_in()
                    fe.sleep_and_log(12)
                if navigation:
                    navigation.dock()
                else:
                    home = (
                        list(session.bookmark(home=True))
                        if session
                        else config.get_home_coo()
                    )
                    fe.auto_dock_to_station(home)
                    fe.sleep_and_log(100)
                self.activate()
                if session:
                    session.undock()  # Verify docking before unloading.
                in_space = False
                self.phase("Unloading ore")
                if ship:
                    ship.unload()
                else:
                    fe.clear_cargo(*config.get_clear_cargo_coo())
                completed += 1
                self.emit("progress", f"{completed}/{runs}")
                if config.get_take_screenshots():
                    pyautogui.screenshot().save(
                        f"eve_screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
                    )
                if end_after_return or self.panic_requested.is_set():
                    break
            self.phase("Run complete")
        except Exception as error:
            logger.exception("Mining run failed")
            self.emit("error", str(error))
            if navigation and navigation.home_station and in_space:
                try:
                    self.phase("Recovering: returning home")
                    self.activate()
                    if not ship:
                        fe.drone_in()
                    navigation.dock()
                    self.phase("Stopped at home after an error")
                except Exception as return_error:
                    self.emit(
                        "error",
                        f"Return failed: {return_error}. Manual control required.",
                    )
        finally:
            self.emit("deadline", "0")
            with self.lock:
                self.busy = False
            self.emit("done", "")

    def mine_legacy(
        self, config: ConfigHandler, session: sm.Session | None, loading_time: float
    ) -> None:
        self.emit("deadline", str(time.time() + loading_time))
        self.activate()
        rm_x, rm_y = config.get_mouse_reset_coo()
        fe.drone_out(rm_x, rm_y)
        tx1, ty1 = (0, 0) if session else config.get_target_one_coo()
        tx2, ty2 = (0, 0) if session else config.get_target_two_coo()
        fe.mining_behaviour(
            tx1,
            ty1,
            tx2,
            ty2,
            config.get_mining_reset_timer(),
            loading_time,
            rm_x,
            rm_y,
            config.get_unlock_all_targets_key(),
            self.activate,
            self.stop_requested.is_set,
            config.get_auto_reset_miners(),
            refresh_targets=session.targets if session else None,
            should_abort=self.panic_requested.is_set,
        )


def start() -> None:
    from Bot.ui import Application

    Application().mainloop()
