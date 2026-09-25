"""Typed application configuration for autofocus experiments."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from math import isfinite
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when an application configuration is invalid."""


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")


def _require_bool(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")


def _require_number(value: object, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    if not isfinite(value):
        raise ConfigError(f"{name} must be finite")
    if positive and value <= 0:
        raise ConfigError(f"{name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class HardwareConfig:
    device_adapter_path: Path
    system_config_path: Path
    camera_label: str = "Kinetix_red"
    xy_stage_label: str = "XYStage"
    focus_device_label: str = "ZDrive"

    def __post_init__(self) -> None:
        for name in ("camera_label", "xy_stage_label", "focus_device_label"):
            _require_text(getattr(self, name), f"hardware.{name}")


@dataclass(frozen=True, slots=True)
class FocusConfig:
    pfs_device_label: str = "PFS"
    pfs_offset_device_label: str = "PFSOffset"
    pfs_policy: str = "require_off"
    pfs_status_property: str | None = None
    pfs_safe_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.pfs_policy not in {"require_off", "ignore"}:
            raise ConfigError("focus.pfs_policy must be 'require_off' or 'ignore'")
        for name in ("pfs_device_label", "pfs_offset_device_label"):
            _require_text(getattr(self, name), f"focus.{name}")
        if self.pfs_status_property is not None:
            _require_text(self.pfs_status_property, "focus.pfs_status_property")
        if any(not isinstance(value, str) or not value for value in self.pfs_safe_values):
            raise ConfigError("focus.pfs_safe_values must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class CameraConfig:
    roi_xyxy: tuple[int, int, int, int] = (0, 2400, 0, 2400)
    exposure_ms: float = 30.0
    acquisition_period_s: float = 0.1
    sequence_interval_ms: float = 0.05
    pixel_size_um_override: float | None = 0.65
    shutter_mode: str = "Never"
    port: str = "Dynamic Range"

    def __post_init__(self) -> None:
        if len(self.roi_xyxy) != 4 or any(type(value) is not int for value in self.roi_xyxy):
            raise ConfigError("camera.roi_xyxy must contain four integers")
        x0, x1, y0, y1 = self.roi_xyxy
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise ConfigError("camera.roi_xyxy must satisfy 0 <= x0 < x1 and 0 <= y0 < y1")
        for name in ("exposure_ms", "acquisition_period_s", "sequence_interval_ms"):
            _require_number(getattr(self, name), f"camera.{name}", positive=True)
        if self.pixel_size_um_override is not None:
            _require_number(
                self.pixel_size_um_override,
                "camera.pixel_size_um_override",
                positive=True,
            )
        _require_text(self.shutter_mode, "camera.shutter_mode")
        _require_text(self.port, "camera.port")


@dataclass(frozen=True, slots=True)
class IlluminationConfig:
    dia_lamp_on: bool = False
    auto_shutter: bool = False
    pattern_engine: str = "LightEngine"
    pattern_channel: str = "GREEN"
    pattern_intensity: int = 500
    pattern_on: bool = False
    focus_engine: str = "Aura"
    focus_channel: str = "GREEN"
    focus_intensity: int = 50
    focus_on: bool = False

    def __post_init__(self) -> None:
        _require_bool(self.dia_lamp_on, "illumination.dia_lamp_on")
        _require_bool(self.auto_shutter, "illumination.auto_shutter")
        _require_bool(self.pattern_on, "illumination.pattern_on")
        _require_bool(self.focus_on, "illumination.focus_on")
        for name in ("pattern_engine", "pattern_channel", "focus_engine", "focus_channel"):
            _require_text(getattr(self, name), f"illumination.{name}")
        for name in ("pattern_intensity", "focus_intensity"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ConfigError(f"illumination.{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class OpticalPathConfig:
    nosepiece_state: str | None = "1"
    condenser_turret_state: str | None = "2"
    lapp_main_branch_state: str | None = "1"
    light_path_state: str | None = "3"
    csu_dichroic_state: str | None = "0"
    csu_port_state: str | None = "2"
    csu_bright_field_port: str | None = "Bright Field"
    csu_shutter_state: str | None = "Closed"
    turret1_shutter_on: bool | None = True
    turret2_shutter_on: bool | None = True

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name in {"turret1_shutter_on", "turret2_shutter_on"}:
                if value is not None:
                    _require_bool(value, f"optical_path.{item.name}")
            elif value is not None:
                _require_text(value, f"optical_path.{item.name}")


@dataclass(frozen=True, slots=True)
class DmdConfig:
    enabled: bool = False
    require_device: bool = True
    calibration_path: Path | None = None

    def __post_init__(self) -> None:
        _require_bool(self.enabled, "dmd.enabled")
        _require_bool(self.require_device, "dmd.require_device")


@dataclass(frozen=True, slots=True)
class AutofocusConfig:
    history_window_s: float = 3.0
    probe_step_um: float = 0.2
    max_probes: int = 20
    min_z_um: float = -10.0
    max_z_um: float = 10.0

    def __post_init__(self) -> None:
        _require_number(self.history_window_s, "autofocus.history_window_s", positive=True)
        _require_number(self.probe_step_um, "autofocus.probe_step_um", positive=True)
        if type(self.max_probes) is not int or self.max_probes <= 0:
            raise ConfigError("autofocus.max_probes must be a positive integer")
        _require_number(self.min_z_um, "autofocus.min_z_um")
        _require_number(self.max_z_um, "autofocus.max_z_um")
        if self.min_z_um >= self.max_z_um:
            raise ConfigError("autofocus.min_z_um must be smaller than autofocus.max_z_um")


@dataclass(frozen=True, slots=True)
class JevConfig:
    model: str = "jev-1.13.0"
    request_timeout_s: float = 10.0

    def __post_init__(self) -> None:
        _require_text(self.model, "jev.model")
        _require_number(self.request_timeout_s, "jev.request_timeout_s", positive=True)


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    hardware: HardwareConfig
    focus: FocusConfig
    camera: CameraConfig
    illumination: IlluminationConfig = field(default_factory=IlluminationConfig)
    optical_path: OpticalPathConfig = field(default_factory=OpticalPathConfig)
    dmd: DmdConfig = field(default_factory=DmdConfig)
    autofocus: AutofocusConfig = field(default_factory=AutofocusConfig)
    jev: JevConfig = field(default_factory=JevConfig)

    def __post_init__(self) -> None:
        if self.illumination.pattern_on and not self.dmd.enabled:
            raise ConfigError("dmd.enabled must be true when illumination.pattern_on is true")

    def validate_host_paths(self) -> None:
        """Validate paths that are expected to exist on the microscope host."""
        if not self.hardware.device_adapter_path.is_dir():
            raise ConfigError(
                "hardware.device_adapter_path is not a directory: "
                f"{self.hardware.device_adapter_path}"
            )
        if not self.hardware.system_config_path.is_file():
            raise ConfigError(
                f"hardware.system_config_path is not a file: {self.hardware.system_config_path}"
            )
        if self.dmd.enabled:
            if self.dmd.calibration_path is None:
                raise ConfigError("dmd.calibration_path is required when dmd.enabled is true")
            if not self.dmd.calibration_path.is_file():
                raise ConfigError(
                    f"dmd.calibration_path does not exist: {self.dmd.calibration_path}"
                )

    def resolved_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot without secrets."""

        def normalize(value: object) -> object:
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(item) for item in value]
            return value

        return normalize(asdict(self))  # type: ignore[return-value]


_SECTION_TYPES = {
    "hardware": HardwareConfig,
    "focus": FocusConfig,
    "camera": CameraConfig,
    "illumination": IlluminationConfig,
    "optical_path": OpticalPathConfig,
    "dmd": DmdConfig,
    "autofocus": AutofocusConfig,
    "jev": JevConfig,
}
_REQUIRED_SECTIONS = {"hardware", "focus", "camera"}
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or _WINDOWS_ABSOLUTE.match(value):
        return path
    return (base_dir / path).resolve()


def _section_values(
    name: str,
    raw: Mapping[str, object],
    base_dir: Path,
) -> dict[str, object]:
    cls = _SECTION_TYPES[name]
    allowed = {item.name for item in fields(cls)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unknown keys in [{name}]: {', '.join(unknown)}")

    values = dict(raw)
    path_fields = {
        ("hardware", "device_adapter_path"),
        ("hardware", "system_config_path"),
        ("dmd", "calibration_path"),
    }
    tuple_fields = {("camera", "roi_xyxy"), ("focus", "pfs_safe_values")}
    for key, value in tuple(values.items()):
        if (name, key) in path_fields and value is not None:
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{name}.{key} must be a non-empty path string")
            values[key] = _resolve_path(value, base_dir)
        elif (name, key) in tuple_fields:
            if not isinstance(value, list):
                raise ConfigError(f"{name}.{key} must be an array")
            values[key] = tuple(value)
    return values


def load_config(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> ApplicationConfig:
    """Load, override, and strictly validate an application TOML file."""
    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read configuration {config_path}: {exc}") from exc

    unknown_sections = sorted(set(raw) - set(_SECTION_TYPES))
    if unknown_sections:
        raise ConfigError(f"unknown configuration sections: {', '.join(unknown_sections)}")
    missing = sorted(_REQUIRED_SECTIONS - set(raw))
    if missing:
        raise ConfigError(f"missing required configuration sections: {', '.join(missing)}")
    if any(not isinstance(value, dict) for value in raw.values()):
        raise ConfigError("every top-level configuration value must be a TOML table")

    raw = {name: dict(value) for name, value in raw.items()}
    environment = os.environ if environ is None else environ
    hardware = raw.setdefault("hardware", {})
    if adapter_path := environment.get("MM_DEVICE_ADAPTER_PATH"):
        hardware["device_adapter_path"] = adapter_path
    if config_override := environment.get("MM_CONFIG_PATH"):
        hardware["system_config_path"] = config_override

    sections: dict[str, object] = {}
    for name, cls in _SECTION_TYPES.items():
        values = _section_values(name, raw.get(name, {}), config_path.parent)
        try:
            sections[name] = cls(**values)
        except TypeError as exc:
            raise ConfigError(f"invalid [{name}] configuration: {exc}") from exc
    return ApplicationConfig(**sections)  # type: ignore[arg-type]
