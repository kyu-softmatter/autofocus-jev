"""Deterministic focus features computed before a Jev request."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import ndimage


class ImageValidationError(ValueError):
    """Raised when an image cannot be used for focus analysis."""


@dataclass(frozen=True, slots=True)
class FocusFeatures:
    """A compact, JSON-compatible set of focus and exposure features."""

    laplacian_variance: float
    tenengrad: float
    high_frequency_power_ratio: float
    gradient_entropy: float
    intensity_mean: float
    intensity_std: float
    saturation_fraction: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _normalized_image(image: ArrayLike) -> tuple[NDArray[np.float64], float]:
    raw = np.asarray(image)
    if raw.ndim != 2:
        raise ImageValidationError(f"focus analysis requires a 2D image, got shape {raw.shape}")
    if min(raw.shape) < 3:
        raise ImageValidationError("focus analysis requires both image dimensions to be at least 3")
    if raw.dtype.kind not in "biuf":
        raise ImageValidationError(f"unsupported image dtype: {raw.dtype}")

    array = raw.astype(np.float64, copy=False)
    if not np.isfinite(array).all():
        raise ImageValidationError("image contains NaN or infinite values")

    if raw.dtype.kind in "bui":
        maximum = float(np.iinfo(raw.dtype).max) if raw.dtype.kind != "b" else 1.0
        normalized = array / maximum
        saturation_threshold = 1.0
    else:
        normalized = array
        saturation_threshold = 1.0 if array.max(initial=0.0) <= 1.0 else math.inf
    return normalized, saturation_threshold


def _gradient_entropy(magnitude: NDArray[np.float64], bins: int = 64) -> float:
    maximum = float(magnitude.max(initial=0.0))
    if maximum <= 0:
        return 0.0
    histogram, _ = np.histogram(magnitude, bins=bins, range=(0.0, maximum))
    probabilities = histogram[histogram > 0].astype(np.float64)
    probabilities /= probabilities.sum()
    entropy = -float(np.sum(probabilities * np.log2(probabilities)))
    return entropy / math.log2(bins)


def _high_frequency_power_ratio(
    image: NDArray[np.float64],
    cutoff_cycles_per_pixel: float,
) -> float:
    centered = image - image.mean()
    spectrum = np.fft.rfft2(centered)
    power = np.abs(spectrum) ** 2
    total = float(power.sum())
    if total <= 0:
        return 0.0

    fy = np.fft.fftfreq(image.shape[0])[:, None]
    fx = np.fft.rfftfreq(image.shape[1])[None, :]
    radius = np.sqrt(fx * fx + fy * fy)
    high_frequency_power = float(power[radius >= cutoff_cycles_per_pixel].sum())
    return high_frequency_power / total


def extract_focus_features(
    image: ArrayLike,
    *,
    high_frequency_cutoff: float = 0.15,
) -> FocusFeatures:
    """Compute complementary focus features from one grayscale image.

    Integer images are scaled by their dtype maximum. Floating-point images
    are expected to use a consistent scale across an experiment. The returned
    values should still be normalized against a stack or reference before
    being sent to Jev.
    """
    if not 0.0 < high_frequency_cutoff < 0.5:
        raise ValueError("high_frequency_cutoff must be between 0 and 0.5")
    normalized, saturation_threshold = _normalized_image(image)
    laplacian = ndimage.laplace(normalized, mode="reflect")
    gradient_x = ndimage.sobel(normalized, axis=1, mode="reflect")
    gradient_y = ndimage.sobel(normalized, axis=0, mode="reflect")
    magnitude_squared = gradient_x * gradient_x + gradient_y * gradient_y
    magnitude = np.sqrt(magnitude_squared)
    saturation_fraction = (
        0.0
        if math.isinf(saturation_threshold)
        else float(np.mean(normalized >= saturation_threshold))
    )

    return FocusFeatures(
        laplacian_variance=float(np.var(laplacian)),
        tenengrad=float(np.mean(magnitude_squared)),
        high_frequency_power_ratio=_high_frequency_power_ratio(normalized, high_frequency_cutoff),
        gradient_entropy=_gradient_entropy(magnitude),
        intensity_mean=float(np.mean(normalized)),
        intensity_std=float(np.std(normalized)),
        saturation_fraction=saturation_fraction,
    )
