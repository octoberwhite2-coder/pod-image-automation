"""User-owned mockup templates; no account or production data is bundled."""
import json
import math
from pathlib import Path, PureWindowsPath
import re

from PIL import Image


def validate_placement(placement, size):
    """Validate photo coordinates independently of optional physical inches."""
    if not isinstance(placement, dict):
        raise ValueError("placement must be an object")
    box = placement.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4 or any(type(v) is not int for v in box):
        raise ValueError("box must contain four integer pixel coordinates")
    left, top, right, bottom = box
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        raise ValueError("print box is outside the template")
    if placement.get("anchor", "center") not in ("center", "top"):
        raise ValueError("anchor must be center or top")
    if "inches" in placement:
        inches = placement["inches"]
        if (not isinstance(inches, (list, tuple)) or len(inches) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in inches)):
            raise ValueError("inches must contain two finite positive numbers")
        if any(int(v * 300) < 1 for v in inches):
            raise ValueError("physical print size is smaller than one pixel at 300 DPI")
    polygons = placement.get("occlusion_polygons", [])
    if not isinstance(polygons, list):
        raise ValueError("occlusion_polygons must be a list")
    for polygon in polygons:
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError("occlusion polygons need at least three points")
        for point in polygon:
            if (not isinstance(point, (list, tuple)) or len(point) != 2
                    or any(type(v) is not int for v in point)
                    or not (0 <= point[0] <= size[0] and 0 <= point[1] <= size[1])):
                raise ValueError("occlusion point is outside the template")


def load(registry_path, names=None):
    """Return detached RGB images keyed by unique template ID, not color.

    Image paths must stay inside the registry directory. Only explicitly
    selected templates (or all enabled entries) are opened.
    """
    registry_path = Path(registry_path).resolve()
    data = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported template schema_version")
    entries = data.get("templates")
    if not isinstance(entries, list):
        raise ValueError("templates must be a list")
    by_id = {}
    reserved = {"CON", "PRN", "AUX", "NUL"} | {prefix + str(i) for prefix in ("COM", "LPT") for i in range(1, 10)}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("template must be an object")
        ident = entry.get("id")
        if (not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", ident, re.I)
                or ident.upper() in reserved):
            raise ValueError("template id must be a portable filename component")
        if ident.casefold() in {key.casefold() for key in by_id}:
            raise ValueError("duplicate template id")
        if type(entry.get("enabled", True)) is not bool:
            raise ValueError("enabled must be a boolean")
        by_id[ident] = entry
    selected = list(dict.fromkeys(names)) if names is not None else [key for key, e in by_id.items() if e.get("enabled", True)]
    result = {}
    try:
        for ident in selected:
            entry = by_id.get(ident)
            if entry is None or not entry.get("enabled", True):
                raise ValueError("unknown or disabled template: " + str(ident))
            size = entry.get("size")
            if (not isinstance(size, list) or len(size) != 2
                    or any(type(v) is not int or v <= 0 for v in size)):
                raise ValueError("size must contain two positive integers")
            for field in ("garment", "color"):
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    raise ValueError(field + " must be a nonempty string")
            filename = entry.get("file")
            if (not isinstance(filename, str) or not filename
                    or Path(filename).is_absolute() or PureWindowsPath(filename).drive
                    or PureWindowsPath(filename).root or "\\" in filename):
                raise ValueError("file must be a relative path using forward slashes")
            path = (registry_path.parent / filename).resolve()
            if not path.is_relative_to(registry_path.parent):
                raise ValueError("template image must remain inside the registry directory")
            placement = dict(entry.get("placement") or {})
            placement.setdefault("anchor", "center")
            validate_placement(placement, size)
            with Image.open(path) as original:
                if list(original.size) != size:
                    raise ValueError("template dimensions changed; recalibration required: " + ident)
                image = original.convert("RGB")
            image.info["pod_template"] = {**entry, "placement": placement}
            result[ident] = image
        return result
    except Exception:
        for image in result.values():
            image.close()
        raise
