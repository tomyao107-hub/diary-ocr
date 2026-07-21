"""Geometry helpers for OCR box overlay on previews."""

from __future__ import annotations

from typing import Any, Iterable


def normalize_box(box: Any) -> list[tuple[float, float]] | None:
    """Accept [[x,y]*4] or flat list; return four (x,y) points."""
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None
    points: list[tuple[float, float]] = []
    # Nested: [[x,y], ...]
    if isinstance(box[0], (list, tuple)) and len(box[0]) >= 2:
        for item in box[:4]:
            try:
                points.append((float(item[0]), float(item[1])))
            except (TypeError, ValueError, IndexError):
                return None
        return points
    # Flat: [x1,y1,x2,y2,...]
    if len(box) >= 8:
        try:
            return [
                (float(box[0]), float(box[1])),
                (float(box[2]), float(box[3])),
                (float(box[4]), float(box[5])),
                (float(box[6]), float(box[7])),
            ]
        except (TypeError, ValueError):
            return None
    return None


def map_boxes(
    boxes: Iterable[dict[str, Any]],
    ocr_size: tuple[int, int] | None,
    preview_size: tuple[int, int] | None,
) -> list[dict[str, Any]]:
    """
    Map OCR boxes from OCR image coordinates to preview pixmap coordinates.

    Each input box dict may contain: text, score, box ([[x,y]*4]).
    Returns new dicts with ``box`` as list of (x,y) floats in preview space.
    """
    if not boxes:
        return []
    ocr_w, ocr_h = (0, 0)
    prev_w, prev_h = (0, 0)
    if ocr_size and len(ocr_size) >= 2:
        ocr_w, ocr_h = int(ocr_size[0]), int(ocr_size[1])
    if preview_size and len(preview_size) >= 2:
        prev_w, prev_h = int(preview_size[0]), int(preview_size[1])

    if ocr_w > 0 and ocr_h > 0 and prev_w > 0 and prev_h > 0:
        sx = prev_w / ocr_w
        sy = prev_h / ocr_h
    else:
        sx = sy = 1.0

    mapped: list[dict[str, Any]] = []
    for item in boxes:
        if not isinstance(item, dict):
            continue
        points = normalize_box(item.get("box"))
        if not points:
            continue
        try:
            score = float(item.get("score", 1.0))
        except (TypeError, ValueError):
            score = 1.0
        text = item.get("text")
        mapped.append(
            {
                "text": text if isinstance(text, str) else "",
                "score": score,
                "box": [(x * sx, y * sy) for x, y in points],
            }
        )
    return mapped
