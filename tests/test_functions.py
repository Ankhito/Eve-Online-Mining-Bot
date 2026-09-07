import random
from typing import List
from unittest.mock import Mock

import pytest

from Bot import functions as fe


def generate_dummy_coords(num_coords) -> List[List[int]]:
    dummy_coords = []
    for _ in range(num_coords):
        x = random.randint(0, 100)
        y = random.randint(0, 100)
        coord = [x, y]
        dummy_coords.append(coord)
    return dummy_coords


def test_that_get_random_coord_always_return_a_new():
    coords = generate_dummy_coords(10)
    last_coord = None
    for _ in range(1000):
        coord = fe.get_random_coord(coords)
        assert coord != last_coord
        last_coord = coord


def test_single_coordinate_can_repeat():
    assert fe.get_random_coord([[1, 2]]) == [1, 2]
    assert fe.get_random_coord([[1, 2]]) == [1, 2]


def test_empty_coordinates_are_rejected():
    with pytest.raises(ValueError):
        fe.get_random_coord([])


def test_mining_refreshes_before_clicking_and_between_cycles(monkeypatch):
    mouse = Mock()
    monkeypatch.setattr(fe, "pyautogui", mouse)
    monkeypatch.setattr(fe, "sleep_and_log", lambda seconds: None)
    monkeypatch.setattr(fe.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(fe.random, "uniform", lambda a, b: 0)
    refresh = Mock(
        side_effect=[[(1, 2), (3, 4)], [(11, 12), (13, 14)], [(21, 22), (23, 24)]]
    )
    stopped = Mock(side_effect=[False, True])
    fe.mining_behaviour(
        0,
        0,
        0,
        0,
        0,
        1000,
        50,
        60,
        "",
        lambda: None,
        stopped,
        False,
        refresh_targets=refresh,
    )
    assert refresh.call_count == 3
    for coords in [(11, 12), (13, 14), (21, 22), (23, 24)]:
        mouse.moveTo.assert_any_call(*coords)
    assert all(call.args != (0, 0) for call in mouse.moveTo.call_args_list)


def test_panic_before_mining_sends_no_input(monkeypatch):
    mouse = Mock()
    monkeypatch.setattr(fe, "pyautogui", mouse)
    refresh = Mock()
    fe.mining_behaviour(
        0,
        0,
        0,
        0,
        0,
        1000,
        50,
        60,
        "",
        lambda: None,
        lambda: False,
        False,
        refresh_targets=refresh,
        should_abort=lambda: True,
    )
    assert not mouse.mock_calls
    refresh.assert_not_called()
