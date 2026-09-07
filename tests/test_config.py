import pytest

from Bot import config


def test_warping_time_is_added_to_default_cargo_loading_time_adjustment():
    cfg = config.ConfigHandler("tests/test_config_with_warping_time.properties")
    actual = cfg.get_cargo_loading_time_adjustment()
    expected = config._DEFAULT_CARGO_LOADING_TIME_ADJUSTMENT + cfg.get_warping_time()
    assert actual == expected
    assert actual == 490


def test_default_warping_time():
    cfg = config.ConfigHandler("tests/empty.properties")
    actual = cfg.get_warping_time()
    expected = config._DEFAULT_WARPING_TIME
    assert actual == expected
    assert actual == 70


def test_auto_reset_miners():
    cfg = config.ConfigHandler("tests/empty.properties")
    actual = cfg.get_auto_reset_miners()
    expected = True
    assert actual == expected


def test_sanderling_defaults_for_existing_config():
    cfg = config.ConfigHandler("tests/empty.properties")
    assert cfg.get_automation_mode() == "sanderling"
    assert cfg.get_home_bookmark_name() == "Home"
    assert cfg.get_mining_bookmark_prefix() == "Mining"
    assert cfg.get_mining_range() == 15000
    assert cfg.get_asteroid_name_pattern() == r"^Asteroid\b"
    assert cfg.get_memory_read_timeout() == 120


def test_coordinate_mode_is_explicit(tmp_path):
    path = tmp_path / "config.properties"
    path.write_text("[SETTINGS]\nautomation_mode = coordinates\n")
    assert config.ConfigHandler(path).get_automation_mode() == "coordinates"


def test_invalid_mode_does_not_silently_fallback(tmp_path):
    path = tmp_path / "config.properties"
    path.write_text("[SETTINGS]\nautomation_mode = typo\n")
    with pytest.raises(ValueError):
        config.ConfigHandler(path).get_automation_mode()
