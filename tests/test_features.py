from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from autofocus_jev.features import ImageValidationError, extract_focus_features


def _checkerboard(size: int = 128, block: int = 8) -> np.ndarray:
    y, x = np.indices((size, size))
    return (((x // block + y // block) % 2) * 65535).astype(np.uint16)


def test_sharp_image_has_stronger_focus_features_than_blurred_image() -> None:
    sharp = _checkerboard()
    blurred = ndimage.gaussian_filter(sharp.astype(np.float64), sigma=3.0).astype(np.uint16)

    sharp_features = extract_focus_features(sharp)
    blurred_features = extract_focus_features(blurred)

    assert sharp_features.laplacian_variance > blurred_features.laplacian_variance
    assert sharp_features.tenengrad > blurred_features.tenengrad
    assert sharp_features.high_frequency_power_ratio > blurred_features.high_frequency_power_ratio


def test_uniform_image_returns_zero_focus_energy() -> None:
    features = extract_focus_features(np.full((32, 32), 100, dtype=np.uint16))

    assert features.laplacian_variance == pytest.approx(0.0)
    assert features.tenengrad == pytest.approx(0.0)
    assert features.high_frequency_power_ratio == pytest.approx(0.0)
    assert features.gradient_entropy == pytest.approx(0.0)


@pytest.mark.parametrize(
    "image, message",
    [
        (np.zeros((2, 4), dtype=np.uint16), "at least 3"),
        (np.zeros((4, 4, 3), dtype=np.uint8), "2D image"),
        (np.full((4, 4), np.nan), "NaN or infinite"),
    ],
)
def test_invalid_images_are_rejected(image: np.ndarray, message: str) -> None:
    with pytest.raises(ImageValidationError, match=message):
        extract_focus_features(image)


def test_invalid_frequency_cutoff_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 0 and 0.5"):
        extract_focus_features(np.zeros((4, 4), dtype=np.uint8), high_frequency_cutoff=0.5)
