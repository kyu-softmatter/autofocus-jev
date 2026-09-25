from __future__ import annotations

from pathlib import Path

import pytest

from autofocus_jev.config import ConfigError, load_config


def _write_config(path: Path, extra: str = "") -> None:
    path.write_text(
        """
[hardware]
device_adapter_path = "./mm"
system_config_path = "./scope.cfg"
camera_label = "Kinetix_red"
xy_stage_label = "XYStage"
focus_device_label = "ZDrive"

[focus]
pfs_device_label = "PFS"
pfs_offset_device_label = "PFSOffset"
pfs_policy = "require_off"

[camera]
roi_xyxy = [0, 16, 0, 8]
exposure_ms = 30.0
acquisition_period_s = 0.1
sequence_interval_ms = 0.05
pixel_size_um_override = 0.65
shutter_mode = "Never"
port = "Dynamic Range"
"""
        + extra,
        encoding="utf-8",
    )


def test_load_config_resolves_paths_and_applies_environment_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "autofocus.toml"
    _write_config(config_path)

    config = load_config(
        config_path,
        environ={"MM_CONFIG_PATH": "./overridden.cfg"},
    )

    assert config.hardware.device_adapter_path == tmp_path / "mm"
    assert config.hardware.system_config_path == tmp_path / "overridden.cfg"
    assert config.camera.roi_xyxy == (0, 16, 0, 8)
    assert config.hardware.focus_device_label == "ZDrive"
    assert config.resolved_dict()["camera"]["roi_xyxy"] == [0, 16, 0, 8]


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "autofocus.toml"
    _write_config(config_path, "\n[jev]\nmodel = 'jev-1.13.0'\nunknown = true\n")

    with pytest.raises(ConfigError, match=r"unknown keys in \[jev\]: unknown"):
        load_config(config_path, environ={})


def test_load_config_rejects_invalid_roi(tmp_path: Path) -> None:
    config_path = tmp_path / "autofocus.toml"
    _write_config(config_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "roi_xyxy = [0, 16, 0, 8]", "roi_xyxy = [16, 0, 0, 8]"
    )
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="camera.roi_xyxy"):
        load_config(config_path, environ={})


def test_load_config_rejects_non_finite_safety_values(tmp_path: Path) -> None:
    config_path = tmp_path / "autofocus.toml"
    _write_config(config_path)
    text = config_path.read_text(encoding="utf-8").replace(
        "exposure_ms = 30.0", "exposure_ms = inf"
    )
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="camera.exposure_ms must be finite"):
        load_config(config_path, environ={})


def test_validate_host_paths_distinguishes_directory_and_file(tmp_path: Path) -> None:
    config_path = tmp_path / "autofocus.toml"
    _write_config(config_path)
    (tmp_path / "mm").mkdir()
    (tmp_path / "scope.cfg").write_text("# test", encoding="utf-8")

    config = load_config(config_path, environ={})
    config.validate_host_paths()


def test_distributed_example_is_parseable() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "autofocus.example.toml", environ={})

    assert config.hardware.camera_label == "Kinetix_red"
    assert config.hardware.focus_device_label == "ZDrive"
    assert config.dmd.require_device is True
