"""Immutable camera and frame metadata records."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class CameraInfo:
    mmcore_version: str
    device_api_version: str
    camera_label: str
    focus_device_label: str
    width: int
    height: int
    roi_xywh: tuple[int, int, int, int]
    binning: str
    bytes_per_pixel: int
    image_bit_depth: int
    component_count: int
    image_buffer_size: int
    exposure_ms: float
    pixel_size_um: float | None
    pixel_size_source: str
    property_readback: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class FrameMetadata:
    frame_id: int
    captured_at_monotonic: float
    timestamp_source: str
    logged_at_utc: str
    camera_label: str
    shape: tuple[int, int]
    dtype: str
    roi_xywh: tuple[int, int, int, int]
    exposure_ms: float
    binning: str
    illumination_engine: str
    illumination_channel: str
    illumination_enabled: bool
    illumination_intensity: float | None
    z_um: float
    hardware_state_version: int
    dropped_frame_count: int
    buffer_overflowed: bool


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    image: NDArray[np.generic]
    metadata: FrameMetadata
