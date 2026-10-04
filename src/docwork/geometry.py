"""Map raw-page pixel rectangles to normalized displayed-page coordinates."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Box


@dataclass(frozen=True, slots=True)
class PixelBox:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if not self.left < self.right or not self.top < self.bottom:
            raise ValueError("pixel rectangle must be nonempty")


@dataclass(frozen=True, slots=True)
class DisplayTransform:
    """Crop first, then rotate clockwise by a right angle.

    The crop is in raw-page pixels and uses an exclusive right/bottom edge.
    OCR coordinates on the resulting display image need no second transform.
    """

    raw_width: int
    raw_height: int
    crop: PixelBox
    rotation_clockwise: int = 0

    def __post_init__(self) -> None:
        if self.raw_width <= 0 or self.raw_height <= 0:
            raise ValueError("raw page dimensions must be positive")
        if self.crop.left < 0 or self.crop.top < 0 or self.crop.right > self.raw_width or self.crop.bottom > self.raw_height:
            raise ValueError("crop must fit inside the raw page")
        if self.rotation_clockwise not in (0, 90, 180, 270):
            raise ValueError("rotation must be a clockwise right angle")

    @property
    def display_size(self) -> tuple[float, float]:
        width = self.crop.right - self.crop.left
        height = self.crop.bottom - self.crop.top
        return (height, width) if self.rotation_clockwise in (90, 270) else (width, height)

    def point(self, x: float, y: float) -> tuple[float, float]:
        if not (self.crop.left <= x <= self.crop.right and self.crop.top <= y <= self.crop.bottom):
            raise ValueError("point is outside the displayed crop")
        x -= self.crop.left
        y -= self.crop.top
        width = self.crop.right - self.crop.left
        height = self.crop.bottom - self.crop.top
        if self.rotation_clockwise == 0:
            return x, y
        if self.rotation_clockwise == 90:
            return height - y, x
        if self.rotation_clockwise == 180:
            return width - x, height - y
        return y, width - x

    def box(self, source: PixelBox) -> Box:
        corners = (
            self.point(source.left, source.top),
            self.point(source.right, source.top),
            self.point(source.left, source.bottom),
            self.point(source.right, source.bottom),
        )
        display_width, display_height = self.display_size
        return Box(
            min(x for x, _ in corners) / display_width,
            min(y for _, y in corners) / display_height,
            max(x for x, _ in corners) / display_width,
            max(y for _, y in corners) / display_height,
        )
