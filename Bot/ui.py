"""Tk desktop interface. Workers communicate exclusively through a queue."""

from __future__ import annotations

import copy
import json
import platform
import queue
import threading
import time
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import pyautogui
from loguru import logger

from Bot import navigation as nav
from Bot import sanderling as sm
from Bot.bot import Controller, make_session, validate_config
from Bot.config import ConfigHandler

BG = "#0d1420"
PANEL = "#172131"
FIELD = "#101927"
TEXT = "#e8eff8"
MUTED = "#9aaec5"
ACCENT = "#56dbc5"


class Application(tk.Tk):
    def __init__(self, config_path: str = "config.properties"):
        if platform.system() != "Windows":
            raise OSError("EVE Mining supports Windows only")
        super().__init__()
        self.title("EVE Mining | Flight deck")
        self.geometry("1040x820")
        self.after_idle(self._show_on_startup)
        self.minsize(940, 740)
        self.configure(bg=BG)
        self.config_file = ConfigHandler(config_path)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.controller = Controller(lambda key, value: self.events.put((key, value)))
        self.inspect_busy = False
        self.deadline = 0.0
        self.windows: list[Any] = []
        self.fields: dict[str, tk.StringVar] = {}
        self.position_fields: dict[str, tk.StringVar] = {}
        self.editable: list[Any] = []
        self.status = tk.StringVar(value="Ready to prepare")
        self.detail = tk.StringVar(
            value="Start docked. Your starting station becomes home for this run."
        )
        self.home = tk.StringVar(value="Captured automatically when you start")
        self.belt = tk.StringVar(value="Selected from the current system")
        self.progress_text = tk.StringVar(value="0 / 0 trips")
        self.remaining = tk.StringVar(value="—")
        self.window_var = tk.StringVar()
        self.mode = tk.StringVar(value=self.config_file.get_automation_mode())
        self.automatic = tk.BooleanVar(value=self.config_file.get_auto_navigation())
        self.allow_unlisted = tk.BooleanVar(
            value=self.config_file.get_allow_unlisted_ores()
        )
        self.screenshots = tk.BooleanVar(value=self.config_file.get_take_screenshots())
        self.reset_miners = tk.BooleanVar(
            value=self.config_file.get_auto_reset_miners()
        )
        self._style()
        self._build()
        self.refresh_windows()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._drain)
        logger.add(
            lambda msg: self.events.put(("log", str(msg).strip())),
            level="INFO",
            format="{message}",
        )
        logger.add(
            str(Path(config_path).with_name("client.log")),
            rotation="1 day",
            retention="7 days",
            level="INFO",
        )

    def _show_on_startup(self) -> None:
        """Put a newly launched flight deck in front of other desktop windows."""
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure(".", bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background=PANEL)
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 25))
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 16))
        style.configure(
            "Metric.TLabel",
            background=PANEL,
            foreground=ACCENT,
            font=("Segoe UI Semibold", 24),
        )
        style.configure("TButton", background="#253449", padding=(13, 9), borderwidth=0)
        style.map(
            "TButton",
            background=[("active", "#344a63"), ("disabled", "#182230")],
            foreground=[("disabled", "#62758c")],
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#0c2521",
            font=("Segoe UI Semibold", 10),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#83edda"), ("disabled", "#29413f")],
        )
        style.configure("Danger.TButton", foreground="#ffc2b9", background="#503039")
        style.configure(
            "TEntry", fieldbackground=FIELD, insertcolor=TEXT, padding=7, borderwidth=1
        )
        style.configure("TCombobox", fieldbackground=FIELD, arrowcolor=TEXT, padding=6)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD)],
            foreground=[("readonly", TEXT)],
        )
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab", background=BG, foreground=MUTED, padding=(18, 11)
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL)],
            foreground=[("selected", ACCENT)],
        )
        style.configure("TCheckbutton", background=BG, padding=5)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure(
            "Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=FIELD,
            borderwidth=0,
        )
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", "#31566a")

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(28, 23, 28, 16))
        header.pack(fill="x")
        ttk.Label(header, text="EVE / MINING", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Your route. Your ore priorities. One session at a time.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=28)
        self.run_tab = ttk.Frame(self.tabs, padding=(18, 20))
        self.ore_tab = ttk.Frame(self.tabs, padding=(18, 20))
        self.ship_tab = ttk.Frame(self.tabs, padding=(18, 20))
        advanced_container = ttk.Frame(self.tabs)
        canvas = tk.Canvas(advanced_container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            advanced_container, orient="vertical", command=canvas.yview
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.advanced_tab = ttk.Frame(canvas, padding=(18, 20))
        content = canvas.create_window((0, 0), window=self.advanced_tab, anchor="nw")
        self.advanced_tab.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(content, width=event.width),
        )
        for frame, name in [
            (self.run_tab, "Flight deck"),
            (self.ore_tab, "Ore priorities"),
            (self.ship_tab, "Ship & timing"),
            (advanced_container, "Advanced"),
        ]:
            self.tabs.add(frame, text=name)
        self._dashboard()
        self._ores()
        self._ship()
        self._advanced()
        self.tabs.pack_forget()
        footer = ttk.Frame(self, padding=(28, 18, 28, 23))
        footer.pack(side="bottom", fill="x")
        self.tabs.pack(fill="both", expand=True, padx=28)
        self.start_button = ttk.Button(
            footer, text="Start session", style="Accent.TButton", command=self.start_run
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            footer,
            text="Stop after cycle",
            command=self.controller.stop,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=9)
        self.panic_button = ttk.Button(
            footer,
            text="Return home",
            style="Danger.TButton",
            command=self.controller.panic,
            state="disabled",
        )
        self.panic_button.pack(side="left")
        self.save_button = ttk.Button(footer, text="Save settings", command=self.save)
        self.save_button.pack(side="right")
        self.editable.extend([self.start_button, self.save_button])

    def _dashboard(self) -> None:
        top = ttk.Frame(self.run_tab)
        top.pack(fill="x", pady=(0, 15))
        ttk.Label(top, text="EVE client", style="Muted.TLabel").pack(
            side="left", padx=(0, 12)
        )
        self.window_picker = ttk.Combobox(
            top, textvariable=self.window_var, state="readonly", width=37
        )
        self.window_picker.pack(side="left", fill="x", expand=True)
        refresh = ttk.Button(top, text="Refresh", command=self.refresh_windows)
        refresh.pack(side="left", padx=8)
        inspect = ttk.Button(top, text="Check EVE", command=self.inspect)
        inspect.pack(side="left")
        self.editable.extend([self.window_picker, refresh, inspect])
        card = ttk.Frame(self.run_tab, style="Card.TFrame", padding=20)
        card.pack(fill="x")
        ttk.Label(card, text="SESSION STATUS", style="CardMuted.TLabel").pack(
            anchor="w"
        )
        ttk.Label(card, textvariable=self.status, style="Metric.TLabel").pack(
            anchor="w", pady=(6, 4)
        )
        ttk.Label(
            card, textvariable=self.detail, style="CardMuted.TLabel", wraplength=840
        ).pack(anchor="w")
        rows = ttk.Frame(card, style="Card.TFrame")
        rows.pack(fill="x", pady=(20, 4))
        rows.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(
            [("HOME STATION", self.home), ("MINING BELT", self.belt)]
        ):
            ttk.Label(rows, text=label, style="CardMuted.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 20), pady=5
            )
            ttk.Label(
                rows, textvariable=variable, style="Card.TLabel", wraplength=640
            ).grid(row=row, column=1, sticky="w")
        metrics = ttk.Frame(self.run_tab)
        metrics.pack(fill="x", pady=15)
        ttk.Label(metrics, textvariable=self.progress_text).pack(side="left")
        ttk.Label(
            metrics, text="Estimated mining time left: ", style="Muted.TLabel"
        ).pack(side="left", padx=(35, 0))
        ttk.Label(metrics, textvariable=self.remaining).pack(side="left")
        self.progress = ttk.Progressbar(self.run_tab, maximum=1)
        self.progress.pack(fill="x", pady=(0, 16))
        ttk.Label(self.run_tab, text="Activity", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 8)
        )
        self.log = tk.Text(
            self.run_tab,
            height=6,
            bg=FIELD,
            fg=MUTED,
            font=("Consolas", 10),
            relief="flat",
            padx=12,
            pady=10,
            state="disabled",
            wrap="word",
        )
        self.log.pack(fill="both", expand=True)
        self._append_log(
            "Automatic navigation remembers your docked station and uses the system menu. No bookmarks needed."
        )

    def _ores(self) -> None:
        ttk.Label(
            self.ore_tab, text="Mine what matters to you", style="Heading.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            self.ore_tab,
            text="Highest priority first. Exact ore names; distance breaks ties within mining range.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 20))
        row = ttk.Frame(self.ore_tab)
        row.pack(fill="x")
        self.ore_entry = ttk.Combobox(
            row,
            values=["Veldspar-II Grade", "Veldspar", "Plagioclase", "Scordite"],
            width=38,
        )
        self.ore_entry.pack(side="left", fill="x", expand=True)
        self.ore_entry.bind("<Return>", lambda event: self.add_ore())
        add = ttk.Button(row, text="Add ore", command=self.add_ore)
        add.pack(side="left", padx=(8, 0))
        self.editable.extend([self.ore_entry, add])
        middle = ttk.Frame(self.ore_tab)
        middle.pack(fill="both", expand=True, pady=14)
        self.ore_list = tk.Listbox(
            middle,
            bg=FIELD,
            fg=TEXT,
            selectbackground="#28566a",
            selectforeground="white",
            font=("Segoe UI", 13),
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            exportselection=False,
            height=8,
        )
        self.ore_list.pack(side="left", fill="both", expand=True)
        self.editable.append(self.ore_list)
        for name in self.config_file.get_ore_priority():
            self.ore_list.insert("end", name)
        actions = ttk.Frame(middle, padding=(12, 0, 0, 0))
        actions.pack(side="left", fill="y")
        for label, action in [
            ("Move up", lambda: self.move_ore(-1)),
            ("Move down", lambda: self.move_ore(1)),
            ("Remove", self.remove_ore),
            ("Read visible ores", self.inspect),
        ]:
            button = ttk.Button(actions, text=label, command=action)
            button.pack(fill="x", pady=(0, 8))
            self.editable.append(button)
        fallback = ttk.Checkbutton(
            self.ore_tab,
            text="Mine unlisted ores after the listed priorities",
            variable=self.allow_unlisted,
        )
        fallback.pack(anchor="w")
        self.editable.append(fallback)
        ttk.Label(
            self.ore_tab,
            text="Leave the list empty to choose the nearest asteroids. “Read visible ores” adds choices from your overview without moving the ship.",
            style="Muted.TLabel",
            wraplength=840,
        ).pack(anchor="w", pady=(10, 0))

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        key: str,
        default: str = "",
        hint: str = "",
    ) -> None:
        value = self.config_file.config.get("SETTINGS", key, fallback=default)
        variable = tk.StringVar(value=value if value.strip() else default)
        self.fields[key] = variable
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", pady=8, padx=(0, 18)
        )
        entry = ttk.Entry(parent, textvariable=variable, width=19)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.editable.append(entry)
        ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=450).grid(
            row=row, column=2, sticky="w", padx=(16, 0)
        )

    def _ship(self) -> None:
        ttk.Label(
            self.ship_tab, text="Ship & mining cycle", style="Heading.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            self.ship_tab,
            text="Set these once for your fit. Cargo fullness is estimated from your yield.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 14))
        form = ttk.Frame(self.ship_tab)
        form.pack(fill="x")
        for row, args in enumerate(
            [
                (
                    "Mining trips",
                    "mining_runs",
                    "5",
                    "Return, unload, and repeat this many times.",
                ),
                ("Mining hold · m³", "mining_hold", "", "Your ore hold capacity."),
                (
                    "Total yield · m³/s",
                    "mining_yield",
                    "",
                    "Combined yield of both lasers.",
                ),
                (
                    "Laser range · m",
                    "mining_range_m",
                    "15000",
                    "Use the fitted mining laser range.",
                ),
                (
                    "Laser cycle · seconds",
                    "mining_reset_timer",
                    "120",
                    "The existing 2-second reset allowance is added.",
                ),
                (
                    "Hardener keys",
                    "hardener_keys",
                    "",
                    "Optional, comma-separated: Alt-F2, F3",
                ),
                (
                    "Unlock all targets",
                    "unlock_all_targets_key",
                    "",
                    "Optional shortcut configured in EVE.",
                ),
            ]
        ):
            self._field(form, row, *args)
        for label, variable in [
            ("Reset miners before starting the next cycle", self.reset_miners),
            ("Save a screenshot after unloading", self.screenshots),
        ]:
            check = ttk.Checkbutton(self.ship_tab, text=label, variable=variable)
            check.pack(anchor="w", pady=(12, 0))
            self.editable.append(check)

    def _advanced(self) -> None:
        ttk.Label(
            self.advanced_tab, text="Navigation & calibration", style="Heading.TLabel"
        ).pack(anchor="w")
        top = ttk.Frame(self.advanced_tab)
        top.pack(fill="x", pady=10)
        ttk.Label(top, text="Control mode").pack(side="left", padx=(0, 14))
        mode = ttk.Combobox(
            top,
            textvariable=self.mode,
            values=["sanderling", "coordinates"],
            state="readonly",
            width=15,
        )
        mode.pack(side="left")
        automatic = ttk.Checkbutton(
            top, text="Automatic station / belt navigation", variable=self.automatic
        )
        automatic.pack(side="left", padx=12)
        self.editable.extend([mode, automatic])
        ttk.Label(
            self.advanced_tab,
            text="Cargo unload and mouse reset still need calibration. Capture waits 3 seconds for you to place the pointer in EVE.",
            style="Muted.TLabel",
            wraplength=840,
        ).pack(anchor="w", pady=(0, 8))
        positions = ttk.Frame(self.advanced_tab)
        positions.pack(fill="x")
        for row, (label, key) in enumerate(
            [
                ("Cargo unload", "clear_cargo_coo"),
                ("Mouse reset", "mouse_reset_coo"),
                ("Undock · legacy", "undock_coo"),
                ("Home · legacy", "warp_to_coo"),
                ("Target 1 · legacy", "target_one_coo"),
                ("Target 2 · legacy", "target_two_coo"),
            ]
        ):
            ttk.Label(positions, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 15), pady=4
            )
            variable = tk.StringVar(
                value=self.config_file.config.get("POSITIONS", key, fallback="")
            )
            self.position_fields[key] = variable
            entry = ttk.Entry(positions, textvariable=variable, width=19)
            entry.grid(row=row, column=1, pady=3)
            button = ttk.Button(
                positions,
                text="Capture in 3 s",
                command=partial(self.capture, variable),
            )
            button.grid(row=row, column=2, padx=10, pady=3)
            self.editable.extend([entry, button])
        ttk.Label(
            self.advanced_tab,
            text="Legacy belt coordinates · one x, y pair per line",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 3))
        self.belt_text = tk.Text(
            self.advanced_tab,
            height=2,
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
        )
        self.belt_text.insert(
            "1.0", self.config_file.config.get("POSITIONS", "mining_coo", fallback="")
        )
        self.belt_text.pack(fill="x")
        self.editable.append(self.belt_text)
        extra = ttk.Frame(self.advanced_tab)
        extra.pack(fill="x", pady=(8, 0))
        for row, args in enumerate(
            [
                (
                    "Memory timeout · s",
                    "memory_read_timeout",
                    "120",
                    "First read can be slow.",
                ),
                (
                    "Legacy warp wait · s",
                    "warping_time",
                    "70",
                    "Automatic navigation observes arrival instead.",
                ),
                (
                    "Asteroid label filter",
                    "asteroid_name_pattern",
                    r"^Asteroid\b",
                    "Matches asteroid name or type labels.",
                ),
            ]
        ):
            self._field(extra, row, *args)

    def refresh_windows(self) -> None:
        previous = self.window_var.get()
        import pygetwindow as gw  # type: ignore

        self.windows = list(gw.getWindowsWithTitle("EVE -"))
        labels = [f"{window.title}  [{window._hWnd}]" for window in self.windows]
        self.window_picker.configure(values=labels)
        self.window_var.set(
            previous
            if previous in labels
            else (labels[0] if labels else "No EVE client found")
        )

    def selected_window(self) -> Any:
        index = self.window_picker.current()
        return self.windows[index] if 0 <= index < len(self.windows) else None

    def read_settings(self) -> ConfigHandler:
        config = copy.deepcopy(self.config_file)
        for section in ("SETTINGS", "POSITIONS"):
            if not config.config.has_section(section):
                config.config.add_section(section)
        for key, variable in self.fields.items():
            config.config.set("SETTINGS", key, variable.get().strip())
        for key, variable in self.position_fields.items():
            config.config.set("POSITIONS", key, variable.get().strip())
        config.config.set(
            "POSITIONS", "mining_coo", self.belt_text.get("1.0", "end").strip()
        )
        config.config.set("SETTINGS", "automation_mode", self.mode.get())
        for key, boolean in [
            ("auto_navigation", self.automatic),
            ("allow_unlisted_ores", self.allow_unlisted),
            ("take_screenshots", self.screenshots),
            ("auto_reset_miners", self.reset_miners),
        ]:
            config.config.set("SETTINGS", key, str(boolean.get()))
        config.set_ore_priority(list(self.ore_list.get(0, "end")))
        return config

    def save(self) -> None:
        try:
            self.config_file = self.read_settings()
            self.config_file.save()
            self._append_log("Settings saved.")
        except OSError as error:
            messagebox.showerror("Could not save settings", str(error), parent=self)

    def start_run(self) -> None:
        try:
            config = self.read_settings()
            validate_config(config)
            config.save()
            self.controller.start(config, self.selected_window())
            self.config_file = config
            self.set_busy(True)
            self.tabs.select(self.run_tab)  # type: ignore[no-untyped-call]
        except (ValueError, OSError, sm.MemoryReadError) as error:
            messagebox.showerror("Setup needed", str(error), parent=self)

    def inspect(self) -> None:
        if self.controller.busy or self.inspect_busy:
            return
        window = self.selected_window()
        if window is None:
            messagebox.showerror(
                "Select EVE", "Select an EVE client first.", parent=self
            )
            return
        config = self.read_settings()
        config.config.set("SETTINGS", "automation_mode", "sanderling")
        self.inspect_busy = True
        self.set_busy(True)
        self.status.set("Reading EVE…")
        self.detail.set("Read-only check. Your ship will not move.")

        def read() -> None:
            try:
                session = make_session(config, window)
                assert session is not None
                snapshot = session.read()
                ores = sm.asteroid_candidates(
                    snapshot, 1e12, config.get_asteroid_name_pattern()
                )
                self.events.put(
                    ("ores", json.dumps(sorted({ore.name for ore in ores})))
                )
                try:
                    name = nav.station_name(snapshot)
                    self.events.put(("home", name))
                    self.events.put(
                        ("status", "Docked station detected. Ready for setup.")
                    )
                except sm.UIElementError:
                    self.events.put(
                        (
                            "status",
                            "EVE read successfully. Start docked with the location panel expanded.",
                        )
                    )
                self.events.put(
                    (
                        "log",
                        f"Read EVE successfully; found {len(ores)} visible asteroid rows.",
                    )
                )
            except Exception as error:
                self.events.put(("error", str(error)))
            finally:
                self.events.put(("inspect_done", ""))

        threading.Thread(target=read, daemon=True).start()

    def set_busy(self, busy: bool) -> None:
        for widget in self.editable:
            if busy:
                widget.configure(state="disabled")
            elif isinstance(widget, ttk.Combobox) and widget is not self.ore_entry:
                widget.configure(state="readonly")
            else:
                widget.configure(state="normal")
        active = self.controller.busy
        self.stop_button.configure(state="normal" if active else "disabled")
        self.panic_button.configure(state="normal" if active else "disabled")

    def add_ore(self) -> None:
        value = self.ore_entry.get().strip()
        existing = {name.casefold() for name in self.ore_list.get(0, "end")}
        if value and value.casefold() not in existing:
            self.ore_list.insert("end", value)
            self.ore_entry.set("")

    def remove_ore(self) -> None:
        for index in reversed(self.ore_list.curselection()):  # type: ignore[no-untyped-call]
            self.ore_list.delete(index)

    def move_ore(self, offset: int) -> None:
        selected = self.ore_list.curselection()  # type: ignore[no-untyped-call]
        if not selected:
            return
        index = selected[0]
        destination = index + offset
        if 0 <= destination < self.ore_list.size():
            value = self.ore_list.get(index)
            self.ore_list.delete(index)
            self.ore_list.insert(destination, value)
            self.ore_list.selection_set(destination)

    def capture(self, variable: tk.StringVar) -> None:
        self.detail.set(
            "Place the mouse over the EVE position. Capturing in 3 seconds…"
        )

        def finish() -> None:
            if self.controller.busy or self.inspect_busy:
                return
            x, y = pyautogui.position()
            variable.set(f"{x}, {y}")
            self.detail.set(f"Captured {x}, {y}. Save settings to keep it.")

        self.after(3000, finish)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 300:
            self.log.delete("1.0", "50.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self) -> None:
        for _ in range(100):
            try:
                key, value = self.events.get_nowait()
            except queue.Empty:
                break
            if key == "phase":
                self.status.set(value)
            elif key == "status":
                self.detail.set(value)
            elif key == "home":
                self.home.set(value)
            elif key == "belt":
                self.belt.set(value)
            elif key == "deadline":
                self.deadline = float(value)
            elif key == "progress":
                done, total = map(int, value.split("/"))
                self.progress_text.set(f"{done} / {total} trips")
                self.progress.configure(maximum=max(1, total), value=done)
            elif key == "ores":
                self.ore_entry.configure(values=json.loads(value))
            elif key == "error":
                self.status.set("Needs attention")
                self.detail.set(value)
                self._append_log(value)
            elif key in {"done", "inspect_done"}:
                if key == "inspect_done":
                    self.inspect_busy = False
                    if self.status.get() == "Reading EVE…":
                        self.status.set("EVE connected")
                self.set_busy(False)
            elif key == "log":
                self._append_log(value)
        seconds = max(0, int(self.deadline - time.time())) if self.deadline else 0
        self.remaining.set(
            f"{seconds // 60:02d}:{seconds % 60:02d}" if self.deadline else "—"
        )
        self.after(100, self._drain)

    def close(self) -> None:
        if self.controller.busy:
            self.controller.panic()
            self.detail.set("Return requested. Close the app once the run has stopped.")
            return
        if self.inspect_busy:
            self.detail.set("Wait for the read-only check to finish before closing.")
            return
        self.destroy()
