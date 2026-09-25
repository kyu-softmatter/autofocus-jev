from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from autofocus_jev.config import (
    ApplicationConfig,
    AutofocusConfig,
    CameraConfig,
    DmdConfig,
    FocusConfig,
    HardwareConfig,
    IlluminationConfig,
    OpticalPathConfig,
)
from autofocus_jev.hardware.pymmcore_adapter import (
    HardwareConfigurationError,
    PyMMCoreMicroscope,
    UnsafeMovementError,
)


class FakeCore:
    def __init__(self) -> None:
        self.loaded = {
            "Kinetix_red",
            "XYStage",
            "ZDrive",
            "PFS",
            "PFSOffset",
            "LightEngine",
            "Aura",
            "DiaLamp",
            "CSUW1-Shutter",
            "Turret1Shutter",
            "Turret2Shutter",
        }
        self.properties = {
            ("Kinetix_red", "Exposure"): "30.0",
            ("Kinetix_red", "ShutterMode"): "Always",
            ("Kinetix_red", "Port"): "Normal",
            ("Kinetix_red", "Binning"): "1",
            ("LightEngine", "GREEN"): "0",
            ("LightEngine", "GREEN_Intensity"): "0",
            ("LightEngine", "State"): "0",
            ("Aura", "GREEN"): "0",
            ("Aura", "GREEN_Intensity"): "0",
            ("Aura", "State"): "0",
            ("DiaLamp", "State"): "0",
            ("DiaLamp", "Intensity"): "0",
            ("CSUW1-Shutter", "State"): "Closed",
            ("Turret1Shutter", "State"): "0",
            ("Turret2Shutter", "State"): "0",
            ("PFS", "State"): "Off",
        }
        self.allowed = {
            ("Kinetix_red", "ShutterMode"): ["Always", "Never"],
            ("Kinetix_red", "Port"): ["Normal", "Dynamic Range"],
            ("LightEngine", "GREEN"): ["0", "1"],
            ("LightEngine", "GREEN_Intensity"): ["0", "500"],
            ("Aura", "GREEN"): ["0", "1"],
            ("Aura", "GREEN_Intensity"): ["0", "50"],
        }
        self.frames: list[np.ndarray] = []
        self.roi = (0, 0, 2, 2)
        self.exposure = 30.0
        self.camera = ""
        self.focus = ""
        self.xy_stage = ""
        self.z_um = 5.0
        self.sequence_running = False
        self.reset_called = False
        self.adapter_paths: list[str] = []
        self.loaded_config = ""

    def setDeviceAdapterSearchPaths(self, paths: list[str]) -> None:
        self.adapter_paths = paths

    def loadSystemConfiguration(self, path: str) -> None:
        self.loaded_config = path

    def getLoadedDevices(self) -> list[str]:
        return list(self.loaded)

    def getSLMDevice(self) -> str:
        return "MightexPolygon1000"

    def hasProperty(self, device: str, prop: str) -> bool:
        return (device, prop) in self.properties

    def getAllowedPropertyValues(self, device: str, prop: str) -> list[str]:
        return self.allowed.get((device, prop), [])

    def setProperty(self, device: str, prop: str, value: object) -> None:
        self.properties[(device, prop)] = str(value)

    def getProperty(self, device: str, prop: str) -> str:
        return self.properties[(device, prop)]

    def getDevicePropertyNames(self, device: str) -> list[str]:
        return [prop for owner, prop in self.properties if owner == device]

    def setCameraDevice(self, label: str) -> None:
        self.camera = label

    def getCameraDevice(self) -> str:
        return self.camera

    def setXYStageDevice(self, label: str) -> None:
        self.xy_stage = label

    def setFocusDevice(self, label: str) -> None:
        self.focus = label

    def getFocusDevice(self) -> str:
        return self.focus

    def setAutoShutter(self, enabled: bool) -> None:
        self.auto_shutter = enabled

    def setExposure(self, exposure: float) -> None:
        self.exposure = exposure
        self.properties[("Kinetix_red", "Exposure")] = str(exposure)

    def getExposure(self) -> float:
        return self.exposure

    def setROI(self, x: int, y: int, width: int, height: int) -> None:
        self.roi = (x, y, width, height)

    def getROI(self) -> tuple[int, int, int, int]:
        return self.roi

    def getPixelSizeUm(self) -> float:
        return 0.0

    def getVersionInfo(self) -> str:
        return "MMCore fake"

    def getAPIVersionInfo(self) -> str:
        return "Device API fake"

    def getImageWidth(self) -> int:
        return self.roi[2]

    def getImageHeight(self) -> int:
        return self.roi[3]

    def getBinning(self) -> str:
        return self.properties[("Kinetix_red", "Binning")]

    def getBytesPerPixel(self) -> int:
        return 2

    def getImageBitDepth(self) -> int:
        return 16

    def getNumberOfComponents(self) -> int:
        return 1

    def getImageBufferSize(self) -> int:
        return 8

    def startContinuousSequenceAcquisition(self, interval_ms: float) -> None:
        self.sequence_interval_ms = interval_ms
        self.sequence_running = True

    def isSequenceRunning(self) -> bool:
        return self.sequence_running

    def stopSequenceAcquisition(self) -> None:
        self.sequence_running = False

    def getRemainingImageCount(self) -> int:
        return len(self.frames)

    def popNextImage(self) -> np.ndarray:
        return self.frames.pop(0)

    def isBufferOverflowed(self) -> bool:
        return False

    def getPosition(self, _label: str) -> float:
        return self.z_um

    def setRelativePosition(self, _label: str, delta_um: float) -> None:
        self.z_um += delta_um

    def waitForDevice(self, _label: str) -> None:
        return None

    def reset(self) -> None:
        self.reset_called = True


def _config(*, pfs_safe_values: tuple[str, ...] = ("Off",)) -> ApplicationConfig:
    return ApplicationConfig(
        hardware=HardwareConfig(
            device_adapter_path=Path("C:/Micro-Manager"),
            system_config_path=Path("C:/camera_red_only.cfg"),
        ),
        focus=FocusConfig(
            pfs_status_property="State",
            pfs_safe_values=pfs_safe_values,
        ),
        camera=CameraConfig(
            roi_xyxy=(0, 2, 0, 2),
            pixel_size_um_override=0.65,
        ),
        illumination=IlluminationConfig(),
        optical_path=OpticalPathConfig(
            nosepiece_state=None,
            condenser_turret_state=None,
            lapp_main_branch_state=None,
            light_path_state=None,
            csu_dichroic_state=None,
            csu_port_state=None,
            csu_bright_field_port=None,
            csu_shutter_state=None,
            turret1_shutter_on=None,
            turret2_shutter_on=None,
        ),
        dmd=DmdConfig(require_device=False),
        autofocus=AutofocusConfig(min_z_um=0.0, max_z_um=10.0),
    )


def test_adapter_opens_reads_latest_frame_and_moves_safely() -> None:
    core = FakeCore()
    microscope = PyMMCoreMicroscope(
        _config(),
        core_factory=lambda: core,
        monotonic=lambda: 12.5,
        validate_paths=False,
    )

    microscope.open()
    core.frames = [np.zeros(4, dtype=np.uint16), np.full(4, 7, dtype=np.uint16)]
    frame = microscope.poll_latest_frame()

    assert frame is not None
    assert np.all(frame.image == 7)
    assert frame.metadata.frame_id == 1
    assert frame.metadata.dropped_frame_count == 1
    assert frame.metadata.captured_at_monotonic == 12.5
    assert frame.metadata.timestamp_source == "host_after_buffer_pop"
    assert frame.metadata.z_um == 5.0
    assert frame.metadata.illumination_engine == "Aura"
    assert frame.metadata.illumination_channel == "GREEN"
    assert frame.metadata.illumination_enabled is False
    assert frame.metadata.illumination_intensity == 0.0
    assert microscope.camera_info.pixel_size_um == 0.65
    assert microscope.camera_info.pixel_size_source == "config_override"
    assert microscope.move_relative_z(0.2) == pytest.approx(5.2)
    assert microscope.hardware_state_version == 1

    microscope.close()

    assert core.reset_called is True
    assert core.properties[("LightEngine", "GREEN_Intensity")] == "0"
    assert core.properties[("Aura", "GREEN_Intensity")] == "0"
    assert core.properties[("Turret1Shutter", "State")] == "0"


def test_adapter_blocks_motion_when_pfs_state_is_not_safe() -> None:
    core = FakeCore()
    microscope = PyMMCoreMicroscope(_config(), core_factory=lambda: core, validate_paths=False)
    microscope.open()
    core.properties[("PFS", "State")] = "On"

    with pytest.raises(UnsafeMovementError, match="PFS state"):
        microscope.move_relative_z(0.1)

    microscope.close()


def test_adapter_controls_aura_green_as_focus_illumination() -> None:
    core = FakeCore()
    microscope = PyMMCoreMicroscope(_config(), core_factory=lambda: core, validate_paths=False)
    microscope.open()

    microscope.set_focus_illumination(True)
    assert core.properties[("Aura", "GREEN_Intensity")] == "50"
    assert core.properties[("Aura", "GREEN")] == "1"
    assert core.properties[("Aura", "State")] == "1"
    assert microscope.hardware_state_version == 1

    microscope.set_focus_illumination(False)
    assert core.properties[("Aura", "GREEN_Intensity")] == "0"
    assert core.properties[("Aura", "GREEN")] == "0"
    assert core.properties[("Aura", "State")] == "0"
    assert microscope.hardware_state_version == 2

    microscope.close()


def test_adapter_blocks_motion_without_verified_pfs_contract() -> None:
    core = FakeCore()
    microscope = PyMMCoreMicroscope(
        _config(pfs_safe_values=()),
        core_factory=lambda: core,
        validate_paths=False,
    )
    microscope.open()

    with pytest.raises(UnsafeMovementError, match="Z motion is disabled"):
        microscope.move_relative_z(0.1)

    microscope.close()


def test_adapter_blocks_non_finite_and_oversized_motion() -> None:
    core = FakeCore()
    microscope = PyMMCoreMicroscope(_config(), core_factory=lambda: core, validate_paths=False)
    microscope.open()

    with pytest.raises(UnsafeMovementError, match="finite"):
        microscope.move_relative_z(float("nan"))
    with pytest.raises(UnsafeMovementError, match="exceeds"):
        microscope.move_relative_z(0.21)

    assert core.z_um == 5.0
    microscope.close()


def test_adapter_fails_closed_when_required_device_is_missing() -> None:
    core = FakeCore()
    core.loaded.remove("ZDrive")
    microscope = PyMMCoreMicroscope(_config(), core_factory=lambda: core, validate_paths=False)

    with pytest.raises(HardwareConfigurationError, match="ZDrive"):
        microscope.open()

    assert core.reset_called is True
