"""Observed ship controls: UI memory supplies state; input stays mouse/keyboard."""

import math
import re
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from Bot import navigation as nav
from Bot import sanderling as sm


class ShipInput(nav.Input, Protocol):
    def drag(self, source: sm.Position, destination: sm.Position) -> None:
        ...


def decimal(value: str) -> float:
    value = value.replace("\u00a0", "").replace(" ", "")
    if "," in value and "." in value:
        separator = "," if value.rfind(",") > value.rfind(".") else "."
        value = value.replace("." if separator == "," else ",", "")
        value = value.replace(separator, ".")
    elif "," in value:
        value = value.replace(",", "" if len(value.rsplit(",", 1)[1]) == 3 else ".")
    elif "." in value and len(value.rsplit(".", 1)[1]) == 3:
        value = value.replace(".", "")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid quantity")
    return result


def inventory(snapshot: sm.Node) -> sm.Node:
    windows = nav.named_nodes(snapshot, "InventoryPrimary") + nav.named_nodes(
        snapshot, "ActiveShipCargo"
    )
    holds = [w for w in windows if nav.named_nodes(w, "ShipGeneralMiningHold")]
    if len(holds) != 1:
        raise sm.UIElementError(
            "Select the current ship's Mining Hold in one inventory window"
        )
    return holds[0]


def capacity(snapshot: sm.Node) -> tuple[float, float]:
    window = inventory(snapshot)
    values = set()
    for gauge in sm.walk(window):
        if "CapacityGauge" not in gauge.get("pythonObjectTypeName", ""):
            continue
        for node in sm.walk(gauge):
            match = re.fullmatch(
                r"(?:\([^)]*\)\s*)?([\d.,\s]+)\s*/\s*([\d.,\s]+)\s*m[³3]", sm.text(node)
            )
            if match:
                try:
                    used, maximum = decimal(match[1]), decimal(match[2])
                    if 0 <= used <= maximum and maximum > 0:
                        values.add((used, maximum))
                except ValueError:
                    pass
    if len(values) != 1:
        raise sm.UIElementError(
            "Cannot read the Mining Hold capacity gauge; keep it visible"
        )
    return values.pop()


def tree_entry(snapshot: sm.Node, labels: set[str]) -> sm.Position:
    matches = set()
    for n in sm.walk(snapshot):
        if not n.get("pythonObjectTypeName", "").startswith("TreeViewEntry"):
            continue
        # Scope to its own heading, excluding nested child tree entries.
        for heading in n.get("children") or []:
            if str(
                heading.get("dictEntriesOfInterest", {}).get("_name", "")
            ).startswith("topCont_"):
                if any(
                    sm.text(label).casefold() in labels for label in sm.walk(heading)
                ):
                    matches.add(sm.get_center_position(heading))
    if len(matches) != 1:
        raise sm.UIElementError(
            f"Cannot uniquely identify inventory entry: {', '.join(sorted(labels))}"
        )
    return matches.pop()


@dataclass(frozen=True)
class Module:
    position: sm.Position
    name: str
    mining: bool
    hardener: bool
    range_m: float | None


def tooltip_module(position: sm.Position, snapshot: sm.Node) -> Module:
    tips = nav.named_nodes(snapshot, "ModuleButtonTooltip")
    if len(tips) != 1:
        raise sm.UIElementError(
            "Cannot read module tooltip; keep EVE visible and tooltips enabled"
        )
    labels = list(dict.fromkeys(sm.text(n) for n in sm.walk(tips[0]) if sm.text(n)))
    combined = " ".join(labels)
    mining = bool(re.search(r"\b(miner|strip miner|mining laser)\b", combined, re.I))
    # The tooltip's first label is used for diagnostics; classification requires
    # a module name, rather than a generic resistance bonus.
    name = labels[0] if labels else "Unknown module"
    hardener = bool(re.search(r"\b(hardener)\b", combined, re.I))
    ranges = []
    for label in [combined]:
        match = re.search(
            r"(?:optimal range|range|max range)\s*:?\s*([\d.,\s]+)\s*(km|m)\b",
            label,
            re.I,
        )
        if match:
            ranges.append(decimal(match[1]) * (1000 if match[2].lower() == "km" else 1))
    return Module(position, name, mining, hardener, min(ranges) if ranges else None)


def module_node(snapshot: sm.Node, module: Module) -> sm.Node:
    matches = [
        n
        for n in nav.named_nodes(snapshot, "ModuleButton")
        if sm.get_center_position(n) == module.position
    ]
    if len(matches) != 1:
        raise sm.UIElementError(
            "Ship modules moved or changed; stop and restart to detect the fit again"
        )
    return matches[0]


def active(snapshot: sm.Node, module: Module) -> bool:
    return (
        module_node(snapshot, module)
        .get("dictEntriesOfInterest", {})
        .get("ramp_active")
        is True
    )


def locked_ore(snapshot: sm.Node, names: set[str]) -> list[sm.Node]:
    return [
        target
        for target in nav.named_nodes(snapshot, "TargetInBar")
        if any(sm.text(n).casefold() in names for n in sm.walk(target))
    ]


class AutomaticShip:
    def __init__(
        self,
        navigation: nav.AutoNavigation,
        inputs: ShipInput,
        emit: Callable[[str, str], None],
    ):
        self.navigation = navigation
        self.session = navigation.session
        self.inputs = inputs
        self.emit = emit
        self.modules: list[Module] = []

    def hold(self) -> sm.Node:
        snapshot = self.session.read()
        try:
            capacity(snapshot)
            return snapshot
        except sm.UIElementError:
            pass
        if not nav.named_nodes(snapshot, "InventoryPrimary"):
            self.inputs.hotkey("alt", "c")
            snapshot = self.navigation.wait_for(
                lambda s: bool(nav.named_nodes(s, "InventoryPrimary")),
                "inventory window",
            )
        self.navigation.click(tree_entry(snapshot, {"mining hold", "ore hold"}))
        snapshot = self.navigation.wait_for(
            lambda s: bool(nav.named_nodes(s, "ShipGeneralMiningHold")),
            "Mining Hold selection",
        )
        capacity(snapshot)
        return snapshot

    def discover(self) -> None:
        self.emit("phase", "Reading fitted module tooltips")
        snapshot = self.session.read()
        positions = [
            sm.get_center_position(n) for n in nav.named_nodes(snapshot, "ModuleButton")
        ]
        self.modules = []
        for position in positions:
            self.navigation.check()
            # Dismiss the previous tooltip before hovering a new module.
            button = nav.named_nodes(self.session.read(), "ListSurroundingsBtn")[0]
            self.inputs.move(*self.session.screen(sm.get_center_position(button)))
            self.navigation.wait_for(
                lambda s: not nav.named_nodes(s, "ModuleButtonTooltip"),
                "previous tooltip to close",
            )
            self.inputs.move(*self.session.screen(position))
            self.navigation.pause(1)
            tip = self.navigation.wait_for(
                lambda s: bool(nav.named_nodes(s, "ModuleButtonTooltip")),
                "module tooltip",
            )
            module = tooltip_module(position, tip)
            self.modules.append(module)
            self.emit("log", f"Detected {module.name}")
        miners = [m for m in self.modules if m.mining]
        if not miners or any(m.range_m is None for m in miners):
            raise sm.UIElementError(
                "Cannot identify mining modules and their ranges from tooltips; no mining commands sent"
            )
        self.session.mining_range = min(
            m.range_m for m in miners if m.range_m is not None
        )
        self.emit(
            "log",
            f"Detected mining range: {self.session.mining_range:g} m. Hold fullness and module activity drive mining; no yield or cycle timer needed.",
        )
        for module in self.modules:
            snapshot = self.session.read()
            # Only named active hardeners are toggled; passive resistance modules
            # and damage controls are excluded from discovery classification.
            state = (
                module_node(snapshot, module)
                .get("dictEntriesOfInterest", {})
                .get("ramp_active")
            )
            if module.hardener and state is not True:
                self.navigation.click(module.position)
                self.navigation.wait_for(
                    lambda s: active(s, module), f"{module.name} activation"
                )

    def mine(self, stopped: Callable[[], bool]) -> None:
        self.hold()
        miners = [m for m in self.modules if m.mining]
        last_used: float | None = None
        last_progress = time.monotonic()
        while not stopped():
            self.navigation.check()
            snapshot = self.session.read()
            used, maximum = capacity(snapshot)
            self.emit(
                "status",
                f"Mining Hold: {used:g} / {maximum:g} m³ ({used / maximum:.0%})",
            )
            if used >= maximum * 0.95:
                self.emit("log", "Mining Hold reached 95%; returning home")
                return
            if last_used != used:
                last_used, last_progress = used, time.monotonic()
            elif time.monotonic() - last_progress > 600:
                raise sm.UIElementError(
                    "Mining Hold has not increased for ten minutes; returning home"
                )
            inactive = [m for m in miners if not active(snapshot, m)]
            if inactive:
                candidates = sm.asteroid_candidates(
                    snapshot,
                    self.session.mining_range,
                    self.session.asteroid_pattern,
                    self.session.ore_priority,
                    self.session.allow_unlisted,
                )
                if not candidates:
                    raise sm.MiningTargetsUnavailable(
                        "No preferred asteroid in laser range"
                    )
                names = {candidates[0].name.casefold()}
                asteroid_targets = locked_ore(snapshot, names)
                if not asteroid_targets:
                    self.navigation.click(candidates[0].position, "right")
                    menu = self.navigation.wait_for(
                        lambda s: bool(nav.menu_groups(s)), "asteroid menu"
                    )
                    self.navigation.click(
                        nav.exact_item(nav.menu_groups(menu)[0], "Lock Target").position
                    )
                    snapshot = self.navigation.wait_for(
                        lambda s: bool(locked_ore(s, names)),
                        "asteroid lock",
                    )
                    asteroid_targets = locked_ore(snapshot, names)
                if len(asteroid_targets) != 1:
                    raise sm.UIElementError(
                        "Automatic mining needs one unambiguous locked asteroid; clear existing targets and restart"
                    )
                self.navigation.click(sm.get_center_position(asteroid_targets[0]))
                for module in inactive:
                    if not active(self.session.read(), module):
                        self.navigation.click(module.position)
                        self.navigation.wait_for(
                            lambda s: active(s, module),
                            f"{module.name} to start mining",
                        )
            self.navigation.pause(1)

    def unload(self) -> None:
        snapshot = self.hold()
        used, _ = capacity(snapshot)
        if used == 0:
            return
        destination = tree_entry(inventory(snapshot), {"item hangar"})
        hold = nav.named_nodes(inventory(snapshot), "ShipGeneralMiningHold")[0]
        items = [
            n
            for n in sm.walk(hold)
            if n.get("pythonObjectTypeName") == "Item"
            or "InvItem" in n.get("pythonObjectTypeName", "")
        ]
        if not items:
            raise sm.UIElementError("Cannot identify ore stacks in the Mining Hold")
        source = sm.get_center_position(items[0])
        self.navigation.click(source)
        self.inputs.hotkey("ctrl", "a")
        self.inputs.drag(self.session.screen(source), self.session.screen(destination))
        self.navigation.wait_for(
            lambda s: capacity(s)[0] == 0, "ore transfer to Item Hangar"
        )
