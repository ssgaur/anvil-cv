"""Centralized geometry validation for annotation payloads.

Each GeometryType has a strict expected JSON shape; reject malformed payloads
at the API boundary rather than letting bad data corrupt the canvas later.
"""
from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException

from anvil_api.models import GeometryType


def _is_finite_num(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(float(x))


def validate_geometry(
    gtype: GeometryType,
    geom: dict[str, Any],
    *,
    keypoint_count: int | None = None,
) -> None:
    """Raise HTTPException(400, ...) if the geometry payload is malformed.

    keypoint_count: required for KEYPOINTS; the class's expected #keypoints.
    """
    if not isinstance(geom, dict):
        raise HTTPException(400, "geometry must be an object")

    if gtype in (GeometryType.POLYGON, GeometryType.BRUSH):
        pts = geom.get("points")
        if not isinstance(pts, list) or len(pts) < 3:
            raise HTTPException(400, "polygon needs ≥3 points")
        for p in pts:
            if not (isinstance(p, list | tuple) and len(p) == 2 and _is_finite_num(p[0]) and _is_finite_num(p[1])):
                raise HTTPException(400, "polygon point must be [x,y] of finite numbers")
        return

    if gtype == GeometryType.POLYLINE:
        pts = geom.get("points")
        if not isinstance(pts, list) or len(pts) < 2:
            raise HTTPException(400, "polyline needs ≥2 points")
        for p in pts:
            if not (isinstance(p, list | tuple) and len(p) == 2 and _is_finite_num(p[0]) and _is_finite_num(p[1])):
                raise HTTPException(400, "polyline point must be [x,y] of finite numbers")
        return

    if gtype == GeometryType.BBOX:
        for k in ("x", "y", "w", "h"):
            if not _is_finite_num(geom.get(k)):
                raise HTTPException(400, f"bbox missing/invalid {k}")
        if float(geom["w"]) <= 0 or float(geom["h"]) <= 0:
            raise HTTPException(400, "bbox w,h must be > 0")
        return

    if gtype == GeometryType.OBB:
        for k in ("cx", "cy", "w", "h", "angle_rad"):
            if not _is_finite_num(geom.get(k)):
                raise HTTPException(400, f"obb missing/invalid {k}")
        if float(geom["w"]) <= 0 or float(geom["h"]) <= 0:
            raise HTTPException(400, "obb w,h must be > 0")
        return

    if gtype == GeometryType.KEYPOINTS:
        if keypoint_count is None or keypoint_count <= 0:
            raise HTTPException(
                400,
                "class has no keypoint_schema; cannot create KEYPOINTS annotation",
            )
        pts = geom.get("points")
        if not isinstance(pts, list) or len(pts) != keypoint_count:
            raise HTTPException(
                400,
                f"keypoints expects exactly {keypoint_count} points (got {len(pts) if isinstance(pts, list) else 'none'})",
            )
        for p in pts:
            if not (isinstance(p, list | tuple) and len(p) == 3):
                raise HTTPException(400, "keypoint point must be [x,y,v]")
            x, y, v = p
            if not isinstance(v, int) or v not in (0, 1, 2):
                raise HTTPException(400, "keypoint visibility must be 0|1|2")
            if v != 0 and not (_is_finite_num(x) and _is_finite_num(y)):
                raise HTTPException(400, "labeled keypoint needs finite x,y")
        return

    raise HTTPException(400, f"unsupported geometry_type {gtype}")


def normalize_obb_angle(angle_rad: float) -> float:
    """Normalize OBB angle to [-π, π)."""
    a = math.fmod(angle_rad + math.pi, 2 * math.pi)
    if a < 0:
        a += 2 * math.pi
    return a - math.pi
