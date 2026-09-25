"""Hardware interface consumed by the controller."""

from __future__ import annotations

from typing import Protocol

from .metadata import CameraInfo, CapturedFrame


class Microscope(Protocol):
    """Minimal microscope operations required by autofocus."""

    @property
    def camera_info(self) -> CameraInfo: ...

    def open(self) -> None: ...

    def poll_latest_frame(self) -> CapturedFrame | None: ...

    def current_z_um(self) -> float: ...

    def move_relative_z(self, delta_um: float) -> float: ...

    def set_focus_illumination(self, enabled: bool) -> None: ...

    def close(self) -> None: ...
