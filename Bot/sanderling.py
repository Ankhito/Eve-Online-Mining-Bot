"""Memory-reader integration and pure UI parsing, independent of mouse input."""

import copy
import ctypes
import html
import json
import math
import platform
import random
import re
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, Iterator

Node = dict[str, Any]
Position = tuple[int, int]


class MemoryReadError(RuntimeError):
    pass


class UIElementError(RuntimeError):
    pass


class MiningTargetsUnavailable(UIElementError):
    pass


def walk(node: Any) -> Iterator[Node]:
    if isinstance(node, dict):
        if node.get("dictEntriesOfInterest", {}).get("_display") is False:
            return
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def text(node: Node) -> str:
    data = node.get("dictEntriesOfInterest", node)
    value = data.get("_text") or data.get("_setText") or ""
    return html.unescape(re.sub(r"<[^>]*>", "", str(value))).strip()


def number(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("int_low32", 0)
    result = float(value)
    if not math.isfinite(result):
        raise UIElementError("Invalid UI geometry")
    return result


def adjust_display_positions(node: Node) -> Node:
    result = copy.deepcopy(node)

    def visit(current: Node, x: float, y: float) -> None:
        data = current.setdefault("dictEntriesOfInterest", {})
        x += number(data.get("_displayX", 0))
        y += number(data.get("_displayY", 0))
        data.update(_displayX=x, _displayY=y)
        for child in current.get("children", []):
            visit(child, x, y)

    visit(result, 0, 0)
    return result


def get_center_position(node: Node) -> Position:
    data = node.get("dictEntriesOfInterest", node)
    width = number(data.get("_displayWidth", 0))
    height = number(data.get("_displayHeight", 0))
    if width <= 0 or height <= 0:
        raise UIElementError("UI element has no clickable display region")
    return (
        round(number(data.get("_displayX", 0)) + min(width, 100) / 2),
        round(number(data.get("_displayY", 0)) + height / 2),
    )


def parse_distance_in_meters(value: str) -> float:
    match = re.fullmatch(r"\s*([\d.,'’\s]+?)\s*(km|m)\s*", value, re.I)
    if not match:
        raise ValueError(f"Invalid distance: {value!r}")
    digits = re.sub(r"[\s'’]", "", match[1])
    # EVE uses three-digit grouping and shorter fractional components.
    if "," in digits and "." in digits:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
        digits = digits.replace("." if decimal == "," else ",", "")
        digits = digits.replace(decimal, ".")
    elif "," in digits or "." in digits:
        separator = "," if "," in digits else "."
        groups = digits.split(separator)
        if all(len(group) == 3 for group in groups[1:]):
            digits = "".join(groups)
        elif len(groups) == 2:
            digits = digits.replace(separator, ".")
        else:
            raise ValueError(f"Invalid distance: {value!r}")
    result = float(digits) * (1000 if match[2].lower() == "km" else 1)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Invalid distance: {value!r}")
    return result


def find_undock_button(snapshot: Node) -> Position:
    candidates = [
        node
        for lobby in walk(snapshot)
        if lobby.get("pythonObjectTypeName") == "LobbyWnd"
        for node in walk(lobby)
        if text(node).casefold() == "undock"
    ]
    if not candidates:
        raise UIElementError("Undock button missing; dock first and use the English UI")
    return get_center_position(candidates[0])


def find_bookmarks(snapshot: Node) -> dict[str, Position]:
    result: dict[str, Position] = {}
    for entry in walk(snapshot):
        if entry.get("pythonObjectTypeName") != "PlaceEntry":
            continue
        labels = [
            node
            for node in walk(entry)
            if node.get("pythonObjectTypeName") == "EveLabelMedium" and text(node)
        ]
        if not labels:
            continue
        name = text(labels[0]).casefold()
        if name in result:
            raise UIElementError(f"Duplicate visible bookmark name: {name}")
        result[name] = get_center_position(labels[0])
    return result


def select_bookmark(
    snapshot: Node,
    home_name: str,
    mining_prefix: str,
    previous: str | None = None,
    home: bool = False,
) -> tuple[str, Position]:
    bookmarks = find_bookmarks(snapshot)
    home_name = home_name.strip().casefold()
    mining_prefix = mining_prefix.strip().casefold()
    if not home_name or home_name not in bookmarks:
        raise UIElementError(f"Home bookmark {home_name!r} is not visible")
    if home:
        return home_name, bookmarks[home_name]
    if not mining_prefix:
        raise UIElementError("Set a nonempty mining_bookmark_prefix")
    choices = [
        name
        for name in bookmarks
        if name != home_name and name.startswith(mining_prefix)
    ]
    if not choices:
        raise UIElementError(
            f"No visible mining bookmarks start with {mining_prefix!r}"
        )
    alternatives = [name for name in choices if name != previous]
    selected = random.choice(alternatives or choices)
    return selected, bookmarks[selected]


def find_asteroids(
    snapshot: Node, max_range: float, name_pattern: str
) -> list[Position]:
    if not math.isfinite(max_range) or max_range <= 0 or not name_pattern.strip():
        raise UIElementError(
            "Set a positive mining_range_m and nonempty asteroid_name_pattern"
        )
    try:
        pattern = re.compile(name_pattern, re.I)
    except re.error as error:
        raise UIElementError(f"Invalid asteroid_name_pattern: {error}") from error
    candidates: list[tuple[float, Position]] = []
    for row in walk(snapshot):
        if row.get("pythonObjectTypeName") != "OverviewScrollEntry":
            continue
        labels = [node for node in walk(row) if text(node)]
        names = [node for node in labels if pattern.search(text(node))]
        if not names:
            continue
        distances = []
        for label in labels:
            try:
                distances.append(parse_distance_in_meters(text(label)))
            except ValueError:
                continue
        # Ambiguous columns are safer to skip than to interpret as a distance.
        if len(distances) != 1 or distances[0] > max_range:
            continue
        candidates.append((distances[0], get_center_position(names[0])))
    candidates.sort(key=lambda item: (item[0], item[1][1]))
    positions = list(dict.fromkeys(position for _, position in candidates))
    if len(positions) < 2:
        raise MiningTargetsUnavailable(
            "Fewer than two matching asteroids in mining range; returning home"
        )
    return positions[:2]


def get_pid_by_hwnd(hwnd: int) -> int:
    if sys.platform != "win32":
        raise MemoryReadError("Sanderling mode requires Windows")
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(
        wintypes.HWND(hwnd), ctypes.byref(pid)
    )
    if not pid.value:
        raise MemoryReadError("Selected EVE window is no longer available")
    return int(pid.value)


def client_origin(hwnd: int) -> Position:
    if sys.platform != "win32":
        raise MemoryReadError("Sanderling mode requires Windows")
    point = wintypes.POINT(0, 0)
    if not ctypes.windll.user32.ClientToScreen(
        wintypes.HWND(hwnd), ctypes.byref(point)
    ):
        raise MemoryReadError("Cannot locate the selected EVE window")
    return point.x, point.y


class MemoryReader:
    def __init__(self, pid: int, timeout: float = 120, executable: Path | None = None):
        if pid <= 0 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Memory reader requires a positive PID and timeout")
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        self.executable = executable or base / "read-memory-64-bit.exe"
        self.pid = pid
        self.timeout = timeout
        self.root_address: str | None = None
        self.lock = threading.Lock()

    def read(self) -> Node:
        if platform.system() != "Windows":
            raise MemoryReadError("Sanderling mode requires Windows")
        if not self.executable.is_file():
            raise MemoryReadError(f"Memory reader not found: {self.executable}")
        with self.lock:
            retry = bool(self.root_address)
            for attempt in range(2 if retry else 1):
                arguments = [
                    str(self.executable),
                    "read-memory-eve-online",
                    "--pid",
                    str(self.pid),
                ]
                if self.root_address:
                    arguments += ["--root-address", self.root_address]
                try:
                    result = subprocess.run(
                        arguments,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if result.returncode:
                        raise MemoryReadError(
                            result.stderr.strip() or "Memory reader failed"
                        )
                    data = json.loads(result.stdout)
                    if not isinstance(data, dict) or not data.get("children"):
                        raise MemoryReadError("Memory reader returned no UI tree")
                    address = data.get("pythonObjectAddress")
                    self.root_address = str(address) if address is not None else None
                    return adjust_display_positions(data)
                except (
                    OSError,
                    subprocess.TimeoutExpired,
                    ValueError,
                    MemoryReadError,
                ) as error:
                    self.root_address = None
                    if not retry or attempt:
                        raise MemoryReadError(
                            f"Unable to read EVE UI: {error}"
                        ) from error
        raise MemoryReadError("Unable to read EVE UI")


class Session:
    """A reader bound to the selected EVE window for one run."""

    def __init__(
        self,
        hwnd: int,
        home_name: str,
        mining_prefix: str,
        mining_range: float,
        asteroid_pattern: str,
        timeout: float,
    ):
        self.hwnd = hwnd
        self.reader = MemoryReader(get_pid_by_hwnd(hwnd), timeout)
        self.home_name = home_name
        self.mining_prefix = mining_prefix
        self.mining_range = mining_range
        self.asteroid_pattern = asteroid_pattern
        self.previous: str | None = None

    def read(self) -> Node:
        if get_pid_by_hwnd(self.hwnd) != self.reader.pid:
            raise MemoryReadError("EVE process changed; select its window and restart")
        return self.reader.read()

    def screen(self, position: Position) -> Position:
        x, y = client_origin(self.hwnd)
        return position[0] + x, position[1] + y

    def validate(self) -> None:
        snapshot = self.read()
        find_undock_button(snapshot)
        select_bookmark(snapshot, self.home_name, self.mining_prefix)
        if not math.isfinite(self.mining_range) or self.mining_range <= 0:
            raise UIElementError("mining_range_m must be positive")
        if not self.asteroid_pattern.strip():
            raise UIElementError("asteroid_name_pattern must not be empty")
        try:
            re.compile(self.asteroid_pattern)
        except re.error as error:
            raise UIElementError(f"Invalid asteroid_name_pattern: {error}") from error

    def undock(self) -> Position:
        return self.screen(find_undock_button(self.read()))

    def bookmark(self, home: bool = False) -> Position:
        name, position = select_bookmark(
            self.read(),
            self.home_name,
            self.mining_prefix,
            self.previous,
            home,
        )
        if not home:
            self.previous = name
        return self.screen(position)

    def targets(self) -> list[Position]:
        return [
            self.screen(position)
            for position in find_asteroids(
                self.read(),
                self.mining_range,
                self.asteroid_pattern,
            )
        ]
