"""Border-line fitting in a rotated frame; no image-processing dependency."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import SamplingError

Point = tuple[float, float]


@dataclass(frozen=True)
class BorderLine:
    a: float
    b: float
    c: float

    def intersect(self, other: BorderLine) -> Point:
        matrix = np.array(((self.a, self.b), (other.a, other.b)))
        if abs(np.linalg.det(matrix)) < 1e-8:
            raise SamplingError("border edges are nearly parallel")
        point = np.linalg.solve(matrix, (-self.c, -other.c))
        return float(point[0]), float(point[1])


@dataclass(frozen=True)
class BorderFrame:
    cosine: float
    sine: float
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @classmethod
    def estimate(cls, points: np.ndarray) -> BorderFrame:
        # Minimum-area enclosing rectangle via an angle sweep. This handles
        # rotation and supplies the frame for fitting the four independent edges.
        best: BorderFrame | None = None
        best_area = float("inf")
        for degree in range(90):
            angle = np.radians(degree)
            cosine, sine = float(np.cos(angle)), float(np.sin(angle))
            xr = points[:, 0] * cosine + points[:, 1] * sine
            yr = -points[:, 0] * sine + points[:, 1] * cosine
            frame = cls(cosine, sine, float(xr.min()), float(xr.max()), float(yr.min()), float(yr.max()))
            area = (frame.xmax - frame.xmin) * (frame.ymax - frame.ymin)
            if area < best_area:
                best, best_area = frame, area
        if best is None:
            raise SamplingError("no border points")
        return best

    def back(self, point: Point) -> Point:
        x, y = point
        return x * self.cosine - y * self.sine, x * self.sine + y * self.cosine

    def rectangle(self) -> tuple[Point, ...]:
        return tuple(self.back(point) for point in (
            (self.xmin, self.ymin), (self.xmax, self.ymin),
            (self.xmax, self.ymax), (self.xmin, self.ymax)))

    def quadrilateral(self, points: np.ndarray) -> tuple[Point, ...]:
        rotated = np.column_stack((points[:, 0] * self.cosine + points[:, 1] * self.sine,
                                   -points[:, 0] * self.sine + points[:, 1] * self.cosine))
        top = fit_border_line(rotated, axis=0, upper=False)
        right = fit_border_line(rotated, axis=1, upper=True)
        bottom = fit_border_line(rotated, axis=0, upper=True)
        left = fit_border_line(rotated, axis=1, upper=False)
        corners = (left.intersect(top), top.intersect(right), right.intersect(bottom), bottom.intersect(left))
        quad = tuple(self.back(point) for point in corners)
        _validate_quad(quad, self.rectangle())
        return quad


def fit_border_line(points: np.ndarray, axis: int, upper: bool) -> BorderLine:
    """Fit the outer envelope, excluding rounded corner arcs and sparse outliers."""
    primary = points[:, axis]
    secondary = points[:, 1 - axis]
    buckets = np.floor(primary).astype(int)
    low, high = int(buckets.min()), int(buckets.max())
    envelope = np.full(high - low + 1, -np.inf if upper else np.inf)
    if upper:
        np.maximum.at(envelope, buckets - low, secondary)
    else:
        np.minimum.at(envelope, buckets - low, secondary)
    coordinates = np.arange(low, high + 1, dtype=float) + 0.5
    span = high - low
    keep = np.isfinite(envelope) & (coordinates > low + 0.2 * span) & (coordinates < high - 0.2 * span)
    slope, intercept = _robust_line(coordinates[keep], envelope[keep])
    if axis == 0:
        return BorderLine(slope, -1.0, intercept)
    return BorderLine(-1.0, slope, intercept)


def _robust_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    if len(x) < 12:
        raise SamplingError("too few border-envelope samples")
    matrix = np.column_stack((x, np.ones(len(x))))
    keep = np.ones(len(x), dtype=bool)
    coefficients = np.zeros(2)
    for _ in range(4):
        if keep.sum() < 8:
            raise SamplingError("border line has too few inliers")
        coefficients = np.linalg.lstsq(matrix[keep], y[keep], rcond=None)[0]
        residual = y - matrix @ coefficients
        deviation = np.median(np.abs(residual[keep] - np.median(residual[keep])))
        keep = np.abs(residual) <= max(1.5, 3 * deviation)
    return float(coefficients[0]), float(coefficients[1])


def _validate_quad(quad: tuple[Point, ...], rectangle: tuple[Point, ...]) -> None:
    points = np.asarray(quad)
    reference = np.asarray(rectangle)
    span = np.ptp(reference, axis=0)
    if not np.isfinite(points).all() or np.any(points < reference.min(axis=0) - span * 0.2):
        raise SamplingError("fitted border corners lie outside the region")
    if np.any(points > reference.max(axis=0) + span * 0.2):
        raise SamplingError("fitted border corners lie outside the region")
    edges = np.roll(points, -1, axis=0) - points
    following = np.roll(edges, -1, axis=0)
    cross = edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0]
    if not (np.all(cross > 0) or np.all(cross < 0)):
        raise SamplingError("fitted border is not a convex quadrilateral")
