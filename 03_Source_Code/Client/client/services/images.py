"""Safe local image loading, orientation correction and size normalization."""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from shared.constants import MAX_IMAGE_BYTES


class ImageError(Exception):
    pass


ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_DIMENSION = 1800
MAX_DISPLAY_DIMENSION = 520


def load_image(path: str | Path) -> tuple[dict, int, int]:
    path = Path(path)
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ImageError("Only PNG, JPG and JPEG images are supported")
    prefer_png = path.suffix.lower() == ".png"
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            data = _encode_under_limit(image, prefer_png=prefer_png)
            width, height = image.size
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ImageError(f"Could not read image: {exc}") from exc
    encoded = base64.b64encode(data).decode("ascii")
    mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    payload = {
        "mime": mime,
        "data": encoded,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    return payload, width, height


def _encode_under_limit(image: Image.Image, *, prefer_png: bool = False) -> bytes:
    # Preserve PNG for transparent images and PNG sources when it fits.
    buffer = io.BytesIO()
    has_alpha = image.mode == "RGBA" and image.getchannel("A").getextrema()[0] < 255
    if has_alpha or prefer_png:
        image.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        if len(data) <= MAX_IMAGE_BYTES:
            return data
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True, progressive=True)
    data = buffer.getvalue()
    if len(data) <= MAX_IMAGE_BYTES:
        return data
    # Re-encode lossy JPEG at decreasing quality/size until it fits.
    rgb = image.convert("RGB") if image.mode != "RGB" else image
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
        candidate = rgb
        if scale < 1.0:
            candidate = rgb.resize(
                (max(16, int(rgb.width * scale)), max(16, int(rgb.height * scale))),
                Image.Resampling.LANCZOS,
            )
        for quality in (85, 78, 70, 60, 50):
            buffer = io.BytesIO()
            candidate.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
            data = buffer.getvalue()
            if len(data) <= MAX_IMAGE_BYTES:
                return data
    raise ImageError("Image remains too large after compression (limit 1.5 MB)")


def display_size(width: int, height: int) -> tuple[float, float]:
    if width <= 0 or height <= 0:
        return 360.0, 240.0
    scale = min(1.0, MAX_DISPLAY_DIMENSION / float(max(width, height)))
    return max(80.0, width * scale), max(60.0, height * scale)
