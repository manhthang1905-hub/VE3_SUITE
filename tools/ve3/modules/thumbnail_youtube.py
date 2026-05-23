"""YouTube thumbnail post-processing helpers."""

from __future__ import annotations

import argparse
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageOps


TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
MAX_BYTES = 2 * 1024 * 1024
THUMB_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class ThumbnailOptimizeResult:
    path: Path
    width: int
    height: int
    size_bytes: int
    format: str
    quality: Optional[int]


class ThumbnailOptimizeError(RuntimeError):
    pass


def _center_crop_16x9(image: Image.Image) -> Image.Image:
    width, height = image.size
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT
    current_ratio = width / height

    if abs(current_ratio - target_ratio) < 0.001:
        return image

    if current_ratio > target_ratio:
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))

    new_height = int(width / target_ratio)
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return buffer.getvalue()


def _best_jpeg_under_limit(image: Image.Image, max_bytes: int) -> tuple[bytes, int]:
    best_data = b""
    best_quality = 60
    low, high = 60, 95

    while low <= high:
        quality = (low + high) // 2
        data = _encode_jpeg(image, quality)
        if len(data) <= max_bytes:
            best_data = data
            best_quality = quality
            low = quality + 1
        else:
            high = quality - 1

    if best_data:
        return best_data, best_quality

    # Last-resort fallback for extremely noisy images.
    for quality in (55, 50, 45, 40, 35):
        data = _encode_jpeg(image, quality)
        if len(data) <= max_bytes:
            return data, quality

    raise ThumbnailOptimizeError(f"cannot fit thumbnail under {max_bytes} bytes")


def optimize_youtube_thumbnail(
    image_path: Path,
    *,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
    max_bytes: int = MAX_BYTES,
) -> ThumbnailOptimizeResult:
    """Normalize one thumbnail to a 1280x720 JPG under the YouTube 2MB limit."""
    image_path = Path(image_path)
    if not image_path.exists():
        raise ThumbnailOptimizeError(f"file not found: {image_path}")

    try:
        with Image.open(image_path) as src:
            image = ImageOps.exif_transpose(src)
            image = image.convert("RGB")
    except Exception as exc:
        raise ThumbnailOptimizeError(f"cannot open image: {image_path}: {exc}") from exc

    image = _center_crop_16x9(image)
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    jpg_path = image_path.with_suffix(".jpg")

    data, quality = _best_jpeg_under_limit(image, max_bytes)
    jpg_path.write_bytes(data)
    if jpg_path != image_path and image_path.exists():
        image_path.unlink()

    return ThumbnailOptimizeResult(
        path=jpg_path,
        width=width,
        height=height,
        size_bytes=len(data),
        format="JPEG",
        quality=quality,
    )


def iter_thumbnail_files(directory: Path) -> Iterable[Path]:
    directory = Path(directory)
    if not directory.exists():
        return []
    return (
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in THUMB_EXTENSIONS
    )


def optimize_thumbnail_dir(directory: Path, *, max_bytes: int = MAX_BYTES) -> list[ThumbnailOptimizeResult]:
    results: list[ThumbnailOptimizeResult] = []
    for path in iter_thumbnail_files(directory):
        results.append(optimize_youtube_thumbnail(path, max_bytes=max_bytes))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize VE3 thumb images for YouTube.")
    parser.add_argument("path", type=Path, help="A thumb directory or a single image file")
    parser.add_argument("--max-mb", type=float, default=2.0, help="Maximum output size in MB")
    args = parser.parse_args()

    max_bytes = int(args.max_mb * 1024 * 1024)
    targets = list(iter_thumbnail_files(args.path)) if args.path.is_dir() else [args.path]
    for target in targets:
        result = optimize_youtube_thumbnail(target, max_bytes=max_bytes)
        print(
            f"{result.path} -> {result.width}x{result.height}, "
            f"{result.size_bytes / 1024:.1f} KB, {result.format} q={result.quality}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
