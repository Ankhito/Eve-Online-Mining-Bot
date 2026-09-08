"""Automatic route preparation using observed UI elements and bounded transitions.

Selectors follow Sanderling's InfoPanelLocationInfo, ListSurroundingsBtn,
MenuEntry structures. No game API or fixed coordinates.
"""

import html
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from Bot import sanderling as sm


class Input(Protocol):
    def click(self, x: int, y: int, button: str = "left") -> None:
        ...

    def move(self, x: int, y: int) -> None:
        ...

    def hotkey(self, *keys: str) -> None:
        ...


class Cancelled(sm.UIElementError):
    pass


@dataclass(frozen=True)
class MenuItem:
    name: str
    position: sm.Position


def normalized(name: str) -> str:
    return (
        re.sub(r"\s+", " ", name.casefold().replace("moon ", "m"))
        .strip()
        .rstrip(".\u2026")
    )


def named_nodes(snapshot: sm.Node, kind: str) -> list[sm.Node]:
    return [n for n in sm.walk(snapshot) if n.get("pythonObjectTypeName") == kind]


def station_name(snapshot: sm.Node) -> str:
    sm.find_undock_button(snapshot)
    names = set()
    for panel in named_nodes(snapshot, "InfoPanelLocationInfo"):
        for node in sm.walk(panel):
            data = node.get("dictEntriesOfInterest", {})
            raw = data.get("_text") or data.get("_setText")
            if not isinstance(raw, str):
                continue
            # Capture the actual station link, not another location in the panel.
            match = re.search(
                r"<(?:url|a)\b[^>]*alt=['\"]Current Station['\"][^>]*>(.*?)</(?:url|a)>",
                raw,
                re.I,
            )
            if match:
                names.add(html.unescape(re.sub(r"<[^>]+>", "", match[1])).strip())
    if len(names) != 1 or not next(iter(names), ""):
        raise sm.UIElementError(
            "Cannot identify the docked station. Expand the location panel; automatic setup will not guess a home station."
        )
    return names.pop()


def menu_groups(snapshot: sm.Node) -> list[list[MenuItem]]:
    layers = [
        n
        for n in sm.walk(snapshot)
        if n.get("dictEntriesOfInterest", {}).get("_name") == "l_menu"
    ]
    groups = []
    for layer in layers:
        for menu in layer.get("children") or []:
            if "menu" not in menu.get("pythonObjectTypeName", "").lower():
                continue
            entries = []
            for entry in sm.walk(menu):
                if "menuentry" not in entry.get("pythonObjectTypeName", "").lower():
                    continue
                labels = [sm.text(n) for n in sm.walk(entry) if sm.text(n)]
                if labels:
                    entries.append(
                        MenuItem(max(labels, key=len), sm.get_center_position(entry))
                    )
            if entries:
                groups.append(sorted(entries, key=lambda item: item.position[1]))
    return groups


def exact_item(items: list[MenuItem], name: str) -> MenuItem:
    matches = [item for item in items if normalized(item.name) == normalized(name)]
    if len(matches) != 1:
        raise sm.UIElementError(
            f"Expected one menu entry {name!r}, found {len(matches)}"
        )
    return matches[0]


def nearest_location(snapshot: sm.Node) -> str:
    labels = [
        sm.text(n)
        for panel in named_nodes(snapshot, "InfoPanelLocationInfo")
        for n in sm.walk(panel)
        if n.get("dictEntriesOfInterest", {}).get("_name") == "nearestLocationInfo"
    ]
    return labels[0] if len(labels) == 1 else ""


def is_warping(snapshot: sm.Node) -> bool:
    return any(
        "warp" in sm.text(n).casefold()
        for panel in named_nodes(snapshot, "ShipUI")
        for n in sm.walk(panel)
        if n.get("dictEntriesOfInterest", {}).get("_name") == "indication_caption"
    )


def space_ready(snapshot: sm.Node) -> bool:
    """The HUD alone appears before the undock transition has finished."""
    if named_nodes(snapshot, "LobbyWnd"):
        return False
    if any(
        n.get("pythonObjectTypeName") in {"LoadingWnd", "Loading", "ProgressWnd"}
        or n.get("dictEntriesOfInterest", {}).get("_name") == "sessionChangeIndicator"
        for n in sm.walk(snapshot)
    ):
        return False
    return bool(
        named_nodes(snapshot, "ShipUI")
        and named_nodes(snapshot, "ModuleButton")
        and named_nodes(snapshot, "ListSurroundingsBtn")
        and any(
            "Overview" in n.get("pythonObjectTypeName", "") for n in sm.walk(snapshot)
        )
    )


class AutoNavigation:
    def __init__(
        self,
        session: sm.Session,
        inputs: Input,
        cancelled: Callable[[], bool],
        timeout: float = 180,
    ):
        self.session = session
        self.inputs = inputs
        self.cancelled = cancelled
        self.timeout = timeout
        self.home_station = ""
        self.returning_home = False
        self.visited: set[str] = set()
        self.last_belt: str | None = None
        self.menu_hover: sm.Position | None = None

    def check(self) -> None:
        if self.cancelled() and not self.returning_home:
            raise Cancelled("Automatic setup interrupted")

    def wait_for(
        self, predicate: Callable[[sm.Node], bool], description: str
    ) -> sm.Node:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.check()
            snapshot = self.session.read()
            self.check()
            if predicate(snapshot):
                return snapshot
            time.sleep(0.5)
        raise sm.UIElementError(f"Timed out waiting for {description}")

    def click(self, position: sm.Position, button: str = "left") -> None:
        self.check()
        self.inputs.click(*self.session.screen(position), button=button)

    def pause(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def wait_until_ready(self) -> None:
        stable_since: float | None = None

        def ready(snapshot: sm.Node) -> bool:
            nonlocal stable_since
            if not space_ready(snapshot):
                stable_since = None
                return False
            if stable_since is None:
                stable_since = time.monotonic()
            return time.monotonic() - stable_since >= 12

        self.wait_for(ready, "space UI to finish loading and remain ready")

    def open_surroundings(self) -> list[MenuItem]:
        self.check()
        snapshot = self.session.read()
        if menu_groups(snapshot):
            self.inputs.hotkey("esc")
            snapshot = self.wait_for(
                lambda s: not menu_groups(s), "closing the previous menu"
            )
        buttons = named_nodes(snapshot, "ListSurroundingsBtn")
        if len(buttons) != 1:
            raise sm.UIElementError(
                "Expand the location panel to show the solar-system menu"
            )
        self.click(sm.get_center_position(buttons[0]), "right")
        self.pause(0.8)
        snapshot = self.wait_for(
            lambda s: any(
                normalized(item.name)
                in {"asteroid belts", "stations", "structures", "planets"}
                for group in menu_groups(s)
                for item in group
            ),
            "solar-system menu categories",
        )
        return menu_groups(snapshot)[0]

    def expand(self, item: MenuItem) -> list[MenuItem]:
        groups = menu_groups(self.session.read())
        parents = [i for i, group in enumerate(groups) if item in group]
        if len(parents) != 1:
            raise sm.UIElementError(
                f"Menu changed before selecting {item.name!r}; retry the run"
            )
        parent = parents[0]
        self.check()
        # Enter a cascading menu horizontally before moving vertically; a diagonal
        # move can cross another parent entry and close the intended submenu.
        if parent and self.menu_hover:
            previous_y = self.menu_hover[1]
            self.inputs.move(*self.session.screen((item.position[0], previous_y)))
        self.inputs.move(*self.session.screen(item.position))
        self.menu_hover = item.position
        self.pause(0.8)
        snapshot = self.wait_for(
            lambda s: len(menu_groups(s)) > parent + 1
            and item in menu_groups(s)[parent],
            f"submenu for {item.name}",
        )
        return menu_groups(snapshot)[parent + 1]

    def station_menu(self) -> list[MenuItem]:
        categories = self.open_surroundings()
        for category in ("Stations", "Structures"):
            matches = [
                item
                for item in categories
                if normalized(item.name) == normalized(category)
            ]
            if not matches:
                continue
            items = self.expand(matches[0])
            matches = [
                item
                for item in items
                if normalized(item.name) == normalized(self.home_station)
            ]
            if len(matches) == 1:
                return self.expand(matches[0])
            categories = self.open_surroundings()
        raise sm.UIElementError(
            f"Home station {self.home_station!r} was not identified. Visible categories: {', '.join(i.name for i in categories)}"
        )

    def prepare_docked(self) -> None:
        # Deliberately session-only: a later Start adopts its actual starting station.
        self.home_station = station_name(self.session.read())

    def warp_zero(self, actions: list[MenuItem]) -> None:
        direct = [
            item
            for item in actions
            if normalized(item.name) in {"warp to within 0 m", "warp to 0 m"}
        ]
        if len(direct) == 1:
            self.click(direct[0].position)
            return
        # Never use a default-distance Warp command.
        warp = [
            item
            for item in actions
            if normalized(item.name) in {"warp to", "warp to within"}
        ]
        if len(warp) != 1:
            raise sm.UIElementError("Cannot identify an explicit warp-to-zero action")
        distances = self.expand(warp[0])
        zero = [
            item for item in distances if normalized(item.name) in {"within 0 m", "0 m"}
        ]
        if len(zero) != 1:
            raise sm.UIElementError("Warp-to-zero distance option is missing")
        self.click(zero[0].position)

    def travel(self) -> None:
        attempted: set[str] = set()
        for _ in range(3):
            try:
                self._travel_one(attempted)
                return
            except sm.MiningTargetsUnavailable:
                if self.last_belt:
                    attempted.add(normalized(self.last_belt))
        raise sm.MiningTargetsUnavailable(
            "No suitable asteroids after checking up to three belts; returning home"
        )

    def _travel_one(self, attempted: set[str]) -> None:
        categories = self.open_surroundings()
        belts = self.expand(exact_item(categories, "Asteroid Belts"))
        choices = [item for item in belts if normalized(item.name) not in self.visited]
        if not choices:
            self.visited.clear()
            choices = [item for item in belts if item.name != self.last_belt] or belts
        if not choices:
            raise sm.MiningTargetsUnavailable(
                "No asteroid belts are listed in this system"
            )
        belt = random.choice(choices)
        self.visited.add(normalized(belt.name))
        self.last_belt = belt.name
        self.warp_zero(self.expand(belt))
        self.wait_for(
            lambda s: not is_warping(s)
            and normalized(nearest_location(s)) == normalized(belt.name),
            "arrival at selected asteroid belt",
        )
        snapshot = self.session.read()
        asteroids = sm.asteroid_candidates(
            snapshot,
            self.session.mining_range,
            self.session.asteroid_pattern,
            self.session.ore_priority,
            self.session.allow_unlisted,
        )
        if not asteroids:
            raise sm.MiningTargetsUnavailable(
                "Selected belt has no preferred asteroids in laser range"
            )

    def dock(self) -> None:
        if not self.home_station:
            raise sm.UIElementError(
                "Home station was not captured; cannot choose a return station"
            )
        # A panic request interrupts outbound travel, never its own return-home path.
        self.returning_home = True
        try:
            snapshot = self.session.read()
            if named_nodes(snapshot, "LobbyWnd"):
                if normalized(station_name(snapshot)) == normalized(self.home_station):
                    return
                raise sm.UIElementError(
                    "Docked station does not match the remembered home; refusing to unload"
                )
            self.wait_for(
                lambda s: not is_warping(s), "current warp to finish before returning"
            )
            actions = self.station_menu()
            self.click(exact_item(actions, "Dock").position)
            snapshot = self.wait_for(
                lambda s: bool(named_nodes(s, "LobbyWnd")), "docking at home"
            )
            if normalized(station_name(snapshot)) != normalized(self.home_station):
                raise sm.UIElementError(
                    "Docked station does not match the remembered home; refusing to unload"
                )
        finally:
            self.returning_home = False
