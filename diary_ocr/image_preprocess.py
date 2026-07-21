"""Image orientation and light preprocessing (v1.2)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps


def apply_exif_orientation(image: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(image) or image


def rotate_image(image: Image.Image, degrees: int) -> Image.Image:
    degrees = int(degrees) % 360
    if degrees == 0:
        return image
    return image.rotate(-degrees, expand=True)


def enhance_image(
    image: Image.Image,
    *,
    grayscale: bool = False,
    contrast: float = 1.0,
) -> Image.Image:
    result = image
    if grayscale:
        result = ImageOps.grayscale(result).convert("RGB")
    if abs(contrast - 1.0) > 1e-3:
        result = ImageEnhance.Contrast(result).enhance(max(0.1, float(contrast)))
    return result


def process_page_file(
    source: Path,
    destination: Path,
    *,
    rotation: int = 0,
    grayscale: bool = False,
    contrast: float = 1.0,
    quality: int = 92,
) -> dict:
    """
    Rebuild a working page from a source file with preprocessing.
    Always leaves original ``source`` untouched.
    """
    with Image.open(source) as image:
        image = apply_exif_orientation(image)
        image = rotate_image(image, rotation)
        image = enhance_image(image, grayscale=grayscale, contrast=contrast)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        save_kwargs = {}
        suffix = destination.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            save_kwargs = {"quality": quality, "optimize": True}
            image.save(temporary, format="JPEG", **save_kwargs)
        else:
            image.save(temporary)
        temporary.replace(destination)
        return {
            "width": image.width,
            "height": image.height,
            "rotation": rotation,
            "grayscale": grayscale,
            "contrast": contrast,
        }


def heic_to_jpeg_bytes(path: Path) -> bytes | None:
    """Convert HEIC/HEIF to JPEG bytes when pillow-heif is installed."""
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            image = apply_exif_orientation(image)
            if image.mode != "RGB":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue()
    except Exception:
        return None
