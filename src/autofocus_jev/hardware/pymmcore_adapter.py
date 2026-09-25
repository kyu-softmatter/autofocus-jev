"""Micro-Manager adapter for the laboratory autofocus rig.

All methods in this class must be called from the microscope worker's single
hardware event-loop thread. The class deliberately does not create threads.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pymmcore

from autofocus_jev.config import ApplicationConfig

from .metadata import CameraInfo, CapturedFrame, FrameMetadata


class HardwareError(RuntimeError):
    """Base exception for microscope failures."""


class HardwareConfigurationError(HardwareError):
    """Raised when the loaded rig does not match the configured profile."""


class UnsafeMovementError(HardwareError):
    """Raised when a requested Z move fails a safety precondition."""


_OPTICAL_PROPERTIES = (
    ("Nosepiece", "State", "nosepiece_state"),
    ("CondenserTurret", "State", "condenser_turret_state"),
    ("LappMainBranch1", "State", "lapp_main_branch_state"),
    ("LightPath", "State", "light_path_state"),
    ("CSUW1-Dichroic", "State", "csu_dichroic_state"),
    ("CSUW1-Port", "State", "csu_port_state"),
    ("CSUW1-Bright", "BrightFieldPort", "csu_bright_field_port"),
    ("CSUW1-Shutter", "State", "csu_shutter_state"),
)


class PyMMCoreMicroscope:
    """Own one CMMCore instance and expose the autofocus hardware contract."""

    def __init__(
        self,
        config: ApplicationConfig,
        *,
        core_factory: Callable[[], Any] = pymmcore.CMMCore,
        monotonic: Callable[[], float] = time.monotonic,
        validate_paths: bool = True,
    ) -> None:
        self._config = config
        self._core_factory = core_factory
        self._monotonic = monotonic
        self._validate_paths = validate_paths
        self._core: Any | None = None
        self._dll_directory_handle: Any | None = None
        self._camera_info: CameraInfo | None = None
        self._frame_id = 0
        self._dropped_frames = 0
        self._hardware_state_version = 0

    @property
    def camera_info(self) -> CameraInfo:
        if self._camera_info is None:
            raise HardwareError("microscope is not open")
        return self._camera_info

    @property
    def hardware_state_version(self) -> int:
        return self._hardware_state_version

    def open(self) -> None:
        if self._core is not None:
            return
        if self._validate_paths:
            self._config.validate_host_paths()
        self._prepare_windows_dll_search_path()
        try:
            core = self._core_factory()
        except Exception:
            self._close_dll_directory_handle()
            raise
        self._core = core
        try:
            hardware = self._config.hardware
            core.setDeviceAdapterSearchPaths([str(hardware.device_adapter_path)])
            core.loadSystemConfiguration(str(hardware.system_config_path))
            self._preflight()
            self._darken_sources()
            core.setCameraDevice(hardware.camera_label)
            core.setXYStageDevice(hardware.xy_stage_label)
            core.setFocusDevice(hardware.focus_device_label)
            core.setAutoShutter(self._config.illumination.auto_shutter)
            self._configure_camera()
            self._configure_optical_path()
            self._configure_illumination()
            self._camera_info = self._read_camera_info()
            core.startContinuousSequenceAcquisition(self._config.camera.sequence_interval_ms)
        except Exception:
            self.close()
            raise

    def _prepare_windows_dll_search_path(self) -> None:
        if os.name != "nt":
            return
        adapter_path = str(self._config.hardware.device_adapter_path)
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if adapter_path not in path_parts:
            os.environ["PATH"] = os.pathsep.join([*path_parts, adapter_path])
        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is not None:
            self._dll_directory_handle = add_dll_directory(adapter_path)

    def _require_core(self) -> Any:
        if self._core is None:
            raise HardwareError("microscope is not open")
        return self._core

    def _preflight(self) -> None:
        core = self._require_core()
        cfg = self._config
        loaded = {str(label) for label in core.getLoadedDevices()}
        required = {
            cfg.hardware.camera_label,
            cfg.hardware.xy_stage_label,
            cfg.hardware.focus_device_label,
            cfg.focus.pfs_device_label,
            cfg.focus.pfs_offset_device_label,
            cfg.illumination.pattern_engine,
            cfg.illumination.focus_engine,
            "DiaLamp",
            "Turret1Shutter",
            "Turret2Shutter",
        }
        required.update(device for device, _, _ in self._configured_optical_properties())
        missing = sorted(required - loaded)
        if missing:
            raise HardwareConfigurationError(
                f"required devices are not loaded: {', '.join(missing)}"
            )
        if cfg.dmd.require_device and not str(core.getSLMDevice()):
            raise HardwareConfigurationError(
                "the Micro-Manager configuration has no SLM/DMD device"
            )
        if cfg.dmd.enabled:
            raise HardwareConfigurationError(
                "DMD operation is not implemented yet; set dmd.enabled to false"
            )

        properties = [
            (cfg.hardware.camera_label, "ShutterMode"),
            (cfg.hardware.camera_label, "Port"),
            (cfg.illumination.pattern_engine, cfg.illumination.pattern_channel),
            (cfg.illumination.pattern_engine, "State"),
            (
                cfg.illumination.pattern_engine,
                f"{cfg.illumination.pattern_channel}_Intensity",
            ),
            (cfg.illumination.focus_engine, cfg.illumination.focus_channel),
            (cfg.illumination.focus_engine, "State"),
            (cfg.illumination.focus_engine, f"{cfg.illumination.focus_channel}_Intensity"),
            ("DiaLamp", "State"),
        ]
        properties.extend(
            (device, prop) for device, prop, _ in self._configured_optical_properties()
        )
        if cfg.focus.pfs_status_property:
            properties.append((cfg.focus.pfs_device_label, cfg.focus.pfs_status_property))
        missing_properties = [
            f"{device}.{prop}" for device, prop in properties if not core.hasProperty(device, prop)
        ]
        if missing_properties:
            raise HardwareConfigurationError(
                f"required properties are not available: {', '.join(missing_properties)}"
            )
        desired_properties = [
            (cfg.hardware.camera_label, "ShutterMode", cfg.camera.shutter_mode),
            (cfg.hardware.camera_label, "Port", cfg.camera.port),
        ]
        desired_properties.extend(self._configured_optical_properties())
        for engine, channel, intensity, enabled in (
            (
                cfg.illumination.pattern_engine,
                cfg.illumination.pattern_channel,
                cfg.illumination.pattern_intensity,
                cfg.illumination.pattern_on,
            ),
            (
                cfg.illumination.focus_engine,
                cfg.illumination.focus_channel,
                cfg.illumination.focus_intensity,
                cfg.illumination.focus_on,
            ),
        ):
            desired_properties.extend(
                (
                    (engine, f"{channel}_Intensity", str(intensity if enabled else 0)),
                    (engine, channel, "1" if enabled else "0"),
                    (engine, "State", "1" if enabled else "0"),
                )
            )
        desired_properties.append(
            ("DiaLamp", "State", "1" if cfg.illumination.dia_lamp_on else "0")
        )
        for device, prop, value in desired_properties:
            self._ensure_property_accepts(device, prop, value)

    def _configured_optical_properties(self) -> list[tuple[str, str, str]]:
        optics = self._config.optical_path
        configured = []
        for device, prop, field_name in _OPTICAL_PROPERTIES:
            value = getattr(optics, field_name)
            if value is not None:
                configured.append((device, prop, value))
        if optics.turret2_shutter_on is not None:
            value = "1" if optics.turret2_shutter_on else "0"
            configured.append(("Turret2Shutter", "State", value))
        if optics.turret1_shutter_on is not None:
            value = "1" if optics.turret1_shutter_on else "0"
            configured.insert(0, ("Turret1Shutter", "State", value))
        return configured

    def _ensure_property_accepts(self, device: str, prop: str, value: str) -> None:
        core = self._require_core()
        if not core.hasProperty(device, prop):
            raise HardwareConfigurationError(f"property is not available: {device}.{prop}")
        allowed = {str(item) for item in core.getAllowedPropertyValues(device, prop)}
        if allowed and value not in allowed:
            raise HardwareConfigurationError(
                f"invalid value for {device}.{prop}: {value!r}; allowed={sorted(allowed)!r}"
            )

    def _set_property_checked(self, device: str, prop: str, value: str) -> None:
        core = self._require_core()
        self._ensure_property_accepts(device, prop, value)
        core.setProperty(device, prop, value)
        actual = str(core.getProperty(device, prop))
        if actual != value:
            raise HardwareConfigurationError(
                f"property readback mismatch for {device}.{prop}: "
                f"expected {value!r}, got {actual!r}"
            )

    def _set_property_if_available(self, device: str, prop: str, value: str) -> None:
        core = self._require_core()
        try:
            if core.hasProperty(device, prop):
                core.setProperty(device, prop, value)
        except Exception:
            # Shutdown is best effort and must continue to the remaining sources.
            pass

    def _darken_sources(self) -> None:
        cfg = self._config.illumination
        for engine, channel in (
            (cfg.pattern_engine, cfg.pattern_channel),
            (cfg.focus_engine, cfg.focus_channel),
        ):
            channels = {channel}
            core = self._require_core()
            try:
                for prop in core.getDevicePropertyNames(engine):
                    prop = str(prop)
                    if prop.endswith("_Intensity"):
                        channels.add(prop.removesuffix("_Intensity"))
            except Exception:
                pass
            for detected_channel in channels:
                self._set_property_if_available(engine, detected_channel, "0")
                self._set_property_if_available(engine, f"{detected_channel}_Intensity", "0")
            self._set_property_if_available(engine, "State", "0")
        self._set_property_if_available("DiaLamp", "State", "0")
        self._set_property_if_available("DiaLamp", "Intensity", "0")
        self._set_property_if_available("CSUW1-Shutter", "State", "Closed")
        self._set_property_if_available("Turret1Shutter", "State", "0")
        self._set_property_if_available("Turret2Shutter", "State", "0")

    def _configure_camera(self) -> None:
        core = self._require_core()
        cfg = self._config.camera
        camera = self._config.hardware.camera_label
        self._set_property_checked(camera, "ShutterMode", cfg.shutter_mode)
        self._set_property_checked(camera, "Port", cfg.port)
        core.setExposure(float(cfg.exposure_ms))
        actual_exposure = float(core.getExposure())
        if not math.isclose(actual_exposure, cfg.exposure_ms, rel_tol=0.0, abs_tol=1e-6):
            raise HardwareConfigurationError(
                f"exposure readback mismatch: expected {cfg.exposure_ms}, got {actual_exposure}"
            )
        x0, x1, y0, y1 = cfg.roi_xyxy
        core.setROI(x0, y0, x1 - x0, y1 - y0)

    def _configure_optical_path(self) -> None:
        for device, prop, value in self._configured_optical_properties():
            self._set_property_checked(device, prop, value)

    def _configure_illumination(self) -> None:
        cfg = self._config.illumination
        for engine, channel, intensity, enabled in (
            (cfg.pattern_engine, cfg.pattern_channel, cfg.pattern_intensity, cfg.pattern_on),
            (cfg.focus_engine, cfg.focus_channel, cfg.focus_intensity, cfg.focus_on),
        ):
            self._set_engine_illumination(engine, channel, intensity, enabled)
        self._set_property_checked("DiaLamp", "State", "1" if cfg.dia_lamp_on else "0")

    def _set_engine_illumination(
        self,
        engine: str,
        channel: str,
        intensity: int,
        enabled: bool,
    ) -> None:
        applied_intensity = intensity if enabled else 0
        self._set_property_checked(engine, f"{channel}_Intensity", str(applied_intensity))
        self._set_property_checked(engine, channel, "1" if enabled else "0")
        self._set_property_checked(engine, "State", "1" if enabled else "0")

    def set_focus_illumination(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise HardwareConfigurationError("focus illumination state must be a boolean")
        cfg = self._config.illumination
        # Invalidate in-flight decisions even if a property write fails after this point.
        self._hardware_state_version += 1
        self._set_engine_illumination(
            cfg.focus_engine,
            cfg.focus_channel,
            cfg.focus_intensity,
            enabled,
        )

    def _read_property(self, device: str, prop: str, default: str = "") -> str:
        core = self._require_core()
        try:
            if core.hasProperty(device, prop):
                return str(core.getProperty(device, prop))
        except Exception:
            pass
        return default

    def _read_camera_info(self) -> CameraInfo:
        core = self._require_core()
        cfg = self._config
        raw_pixel_size = float(core.getPixelSizeUm())
        if math.isfinite(raw_pixel_size) and raw_pixel_size > 0:
            pixel_size = raw_pixel_size
            pixel_source = "micro_manager"
        else:
            pixel_size = cfg.camera.pixel_size_um_override
            pixel_source = "config_override" if pixel_size is not None else "unavailable"
        camera = cfg.hardware.camera_label
        readback_items = []
        for name in ("Exposure", "ShutterMode", "Port", "Binning"):
            value = self._read_property(camera, name)
            if value:
                readback_items.append((name, value))
        return CameraInfo(
            mmcore_version=str(core.getVersionInfo()),
            device_api_version=str(core.getAPIVersionInfo()),
            camera_label=str(core.getCameraDevice()),
            focus_device_label=str(core.getFocusDevice()),
            width=int(core.getImageWidth()),
            height=int(core.getImageHeight()),
            roi_xywh=tuple(int(value) for value in core.getROI()),
            binning=str(core.getBinning()),
            bytes_per_pixel=int(core.getBytesPerPixel()),
            image_bit_depth=int(core.getImageBitDepth()),
            component_count=int(core.getNumberOfComponents()),
            image_buffer_size=int(core.getImageBufferSize()),
            exposure_ms=float(core.getExposure()),
            pixel_size_um=pixel_size,
            pixel_size_source=pixel_source,
            property_readback=tuple(readback_items),
        )

    def poll_latest_frame(self) -> CapturedFrame | None:
        core = self._require_core()
        count = int(core.getRemainingImageCount())
        if count <= 0:
            return None
        latest: Any | None = None
        for _ in range(count):
            latest = core.popNextImage()
        if latest is None:
            return None
        self._dropped_frames += max(0, count - 1)
        info = self.camera_info
        image = np.asarray(latest).reshape((info.height, info.width)).copy()
        self._frame_id += 1
        illumination_enabled, illumination_intensity = self._read_focus_illumination()
        illumination = self._config.illumination
        metadata = FrameMetadata(
            frame_id=self._frame_id,
            captured_at_monotonic=self._monotonic(),
            timestamp_source="host_after_buffer_pop",
            logged_at_utc=datetime.now(UTC).isoformat(),
            camera_label=info.camera_label,
            shape=image.shape,
            dtype=str(image.dtype),
            roi_xywh=info.roi_xywh,
            exposure_ms=float(core.getExposure()),
            binning=str(core.getBinning()),
            illumination_engine=illumination.focus_engine,
            illumination_channel=illumination.focus_channel,
            illumination_enabled=illumination_enabled,
            illumination_intensity=illumination_intensity,
            z_um=self.current_z_um(),
            hardware_state_version=self._hardware_state_version,
            dropped_frame_count=self._dropped_frames,
            buffer_overflowed=bool(core.isBufferOverflowed()),
        )
        return CapturedFrame(image=image, metadata=metadata)

    def _read_focus_illumination(self) -> tuple[bool, float | None]:
        cfg = self._config.illumination
        channel = self._read_property(cfg.focus_engine, cfg.focus_channel).strip().lower()
        master = self._read_property(cfg.focus_engine, "State").strip().lower()
        on_values = {"1", "true", "on", "enabled"}
        enabled = channel in on_values and master in on_values
        raw_intensity = self._read_property(cfg.focus_engine, f"{cfg.focus_channel}_Intensity")
        try:
            intensity = float(raw_intensity)
        except ValueError:
            intensity = None
        return enabled, intensity

    def current_z_um(self) -> float:
        core = self._require_core()
        return float(core.getPosition(self._config.hardware.focus_device_label))

    def _verify_pfs_safe_for_motion(self) -> None:
        cfg = self._config.focus
        if cfg.pfs_policy == "ignore":
            return
        if not cfg.pfs_status_property or not cfg.pfs_safe_values:
            raise UnsafeMovementError(
                "Z motion is disabled until focus.pfs_status_property and "
                "focus.pfs_safe_values are configured"
            )
        actual = self._read_property(cfg.pfs_device_label, cfg.pfs_status_property)
        if actual not in cfg.pfs_safe_values:
            raise UnsafeMovementError(
                f"PFS state {actual!r} is not safe for Z motion; "
                f"expected one of {cfg.pfs_safe_values!r}"
            )

    def move_relative_z(self, delta_um: float) -> float:
        if isinstance(delta_um, bool) or not isinstance(delta_um, (int, float)):
            raise UnsafeMovementError("Z movement must be a numeric distance in micrometers")
        if not math.isfinite(delta_um):
            raise UnsafeMovementError("Z movement must be finite")
        cfg = self._config.autofocus
        if abs(delta_um) > cfg.probe_step_um:
            raise UnsafeMovementError(
                f"requested Z step {delta_um} exceeds the configured limit {cfg.probe_step_um}"
            )
        self._verify_pfs_safe_for_motion()
        core = self._require_core()
        current = self.current_z_um()
        target = current + float(delta_um)
        if target < cfg.min_z_um or target > cfg.max_z_um:
            raise UnsafeMovementError(
                f"requested Z target {target} is outside [{cfg.min_z_um}, {cfg.max_z_um}]"
            )
        focus = self._config.hardware.focus_device_label
        # Invalidate in-flight decisions even if the device call or readback fails after this point.
        self._hardware_state_version += 1
        core.setRelativePosition(focus, float(delta_um))
        core.waitForDevice(focus)
        actual = self.current_z_um()
        if not math.isfinite(actual) or actual < cfg.min_z_um or actual > cfg.max_z_um:
            raise HardwareError(f"invalid Z position after movement: {actual}")
        return actual

    def close(self) -> None:
        core, self._core = self._core, None
        self._camera_info = None
        if core is None:
            self._close_dll_directory_handle()
            return
        try:
            if core.isSequenceRunning():
                core.stopSequenceAcquisition()
        except Exception:
            pass
        self._core = core
        try:
            self._darken_sources()
        finally:
            self._core = None
            with suppress(Exception):
                core.reset()
            self._close_dll_directory_handle()

    def _close_dll_directory_handle(self) -> None:
        handle, self._dll_directory_handle = self._dll_directory_handle, None
        if handle is not None:
            with suppress(Exception):
                handle.close()
