#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ken Burns Effect Generator for Video Composition.

Tạo hiệu ứng Ken Burns (zoom + pan) cho ảnh tĩnh trong video.
Hỗ trợ nhiều kiểu hiệu ứng và cường độ khác nhau.
"""

import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass


import random
from enum import Enum
from typing import Optional


class KenBurnsEffect(Enum):
    """Các kiểu hiệu ứng Ken Burns."""
    ZOOM_IN = "zoom_in"           # Zoom vào giữa
    ZOOM_OUT = "zoom_out"         # Zoom ra
    PAN_LEFT = "pan_left"         # Pan từ phải sang trái
    PAN_RIGHT = "pan_right"       # Pan từ trái sang phải
    PAN_UP = "pan_up"             # Pan từ dưới lên
    PAN_DOWN = "pan_down"         # Pan từ trên xuống
    ZOOM_IN_LEFT = "zoom_in_left"     # Zoom vào góc trái
    ZOOM_IN_RIGHT = "zoom_in_right"   # Zoom vào góc phải
    ZOOM_OUT_CENTER = "zoom_out_center"  # Zoom ra từ tâm


class KenBurnsIntensity(Enum):
    """Cường độ hiệu ứng."""
    SUBTLE = "subtle"     # Nhẹ: zoom 5%, pan 3%
    NORMAL = "normal"     # Bình thường: zoom 12%, pan 8%
    STRONG = "strong"     # Mạnh: zoom 20%, pan 15%


# Intensity settings: (zoom_percent, pan_percent)
INTENSITY_SETTINGS = {
    KenBurnsIntensity.SUBTLE: (0.05, 0.03),
    KenBurnsIntensity.NORMAL: (0.12, 0.08),
    KenBurnsIntensity.STRONG: (0.20, 0.15),
}


class KenBurnsGenerator:
    """
    Generator cho Ken Burns effects.

    Sử dụng:
        kb = KenBurnsGenerator(1920, 1080, intensity="normal")
        effect = kb.get_random_effect()
        vf = kb.generate_filter(effect, duration=5, fade_duration=0.5)
    """

    def __init__(self, width: int = 1920, height: int = 1080,
                 intensity: str = "normal", fps: int = 25):
        """
        Khởi tạo generator.

        Args:
            width: Chiều rộng output video
            height: Chiều cao output video
            intensity: Cường độ hiệu ứng (subtle/normal/strong)
            fps: Frame rate
        """
        self.width = width
        self.height = height
        self.fps = fps

        # Parse intensity
        if isinstance(intensity, str):
            intensity = intensity.lower()
            self.intensity = {
                "subtle": KenBurnsIntensity.SUBTLE,
                "normal": KenBurnsIntensity.NORMAL,
                "strong": KenBurnsIntensity.STRONG,
            }.get(intensity, KenBurnsIntensity.NORMAL)
        else:
            self.intensity = intensity

        self.zoom_percent, self.pan_percent = INTENSITY_SETTINGS[self.intensity]

    def get_random_effect(self, exclude_last: Optional[KenBurnsEffect] = None) -> KenBurnsEffect:
        """
        Lấy effect ngẫu nhiên, tránh lặp effect trước đó.

        Args:
            exclude_last: Effect cần tránh (để không lặp liên tiếp)

        Returns:
            KenBurnsEffect ngẫu nhiên
        """
        effects = list(KenBurnsEffect)
        if exclude_last and exclude_last in effects:
            effects.remove(exclude_last)
        return random.choice(effects)

    def generate_filter(self, effect: KenBurnsEffect, duration: float,
                       fade_duration: float = 0.5, simple_mode: bool = False) -> str:
        """
        Tạo FFmpeg filter string cho effect.

        Args:
            effect: Kiểu hiệu ứng
            duration: Thời lượng clip (giây)
            fade_duration: Thời gian fade in/out (giây)
            simple_mode: True = không dùng easing (nhanh hơn)

        Returns:
            FFmpeg filter string
        """
        w, h = self.width, self.height
        total_frames = int(duration * self.fps)

        # Tính zoom range
        zoom_start = 1.0
        zoom_end = 1.0 + self.zoom_percent

        # Tính pan range (pixels)
        pan_x = int(w * self.pan_percent)
        pan_y = int(h * self.pan_percent)

        # Base zoompan expression
        # zoompan: z=zoom, x=pan_x, y=pan_y, d=frames, s=size, fps=fps

        if simple_mode:
            # Simple mode: Linear interpolation (nhanh hơn)
            zoom_expr, x_expr, y_expr = self._get_linear_expressions(
                effect, zoom_start, zoom_end, pan_x, pan_y, total_frames
            )
        else:
            # Quality mode: Easing (mượt hơn nhưng chậm hơn)
            zoom_expr, x_expr, y_expr = self._get_eased_expressions(
                effect, zoom_start, zoom_end, pan_x, pan_y, total_frames
            )

        # Zoompan filter
        zoompan = f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s={w}x{h}:fps={self.fps}"

        # Add fade in/out
        fade_out_start = max(0, duration - fade_duration)
        fade_filter = f"fade=t=in:st=0:d={fade_duration},fade=t=out:st={fade_out_start}:d={fade_duration}"

        return f"{zoompan},{fade_filter}"

    def _get_linear_expressions(self, effect: KenBurnsEffect,
                                 zoom_start: float, zoom_end: float,
                                 pan_x: int, pan_y: int,
                                 total_frames: int) -> tuple:
        """Tạo expressions với linear interpolation."""

        # Progress: on/n (0 → 1)
        progress = "on/{}".format(total_frames)

        if effect == KenBurnsEffect.ZOOM_IN:
            zoom = f"{zoom_start}+{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        elif effect == KenBurnsEffect.ZOOM_OUT:
            zoom = f"{zoom_end}-{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        elif effect == KenBurnsEffect.PAN_LEFT:
            zoom = str(zoom_start)
            x = f"{pan_x}*(1-{progress})"
            y = "0"

        elif effect == KenBurnsEffect.PAN_RIGHT:
            zoom = str(zoom_start)
            x = f"{pan_x}*{progress}"
            y = "0"

        elif effect == KenBurnsEffect.PAN_UP:
            zoom = str(zoom_start)
            x = "0"
            y = f"{pan_y}*(1-{progress})"

        elif effect == KenBurnsEffect.PAN_DOWN:
            zoom = str(zoom_start)
            x = "0"
            y = f"{pan_y}*{progress}"

        elif effect == KenBurnsEffect.ZOOM_IN_LEFT:
            zoom = f"{zoom_start}+{zoom_end - zoom_start}*{progress}"
            x = f"(iw/4)*(1-{progress})"
            y = f"ih/2-(ih/zoom/2)"

        elif effect == KenBurnsEffect.ZOOM_IN_RIGHT:
            zoom = f"{zoom_start}+{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)+{pan_x}*{progress}"
            y = f"ih/2-(ih/zoom/2)"

        else:  # ZOOM_OUT_CENTER
            zoom = f"{zoom_end}-{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        return zoom, x, y

    def _get_eased_expressions(self, effect: KenBurnsEffect,
                                zoom_start: float, zoom_end: float,
                                pan_x: int, pan_y: int,
                                total_frames: int) -> tuple:
        """Tạo expressions với easing (smooth in/out)."""

        # Ease in-out: sin((π * on/n) - π/2) / 2 + 0.5
        # Simplified for FFmpeg: smoother motion at start/end
        progress = f"(1-cos(PI*on/{total_frames}))/2"

        if effect == KenBurnsEffect.ZOOM_IN:
            zoom = f"{zoom_start}+{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        elif effect == KenBurnsEffect.ZOOM_OUT:
            zoom = f"{zoom_end}-{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        elif effect == KenBurnsEffect.PAN_LEFT:
            zoom = str(zoom_start)
            x = f"{pan_x}*(1-{progress})"
            y = "0"

        elif effect == KenBurnsEffect.PAN_RIGHT:
            zoom = str(zoom_start)
            x = f"{pan_x}*{progress}"
            y = "0"

        elif effect == KenBurnsEffect.PAN_UP:
            zoom = str(zoom_start)
            x = "0"
            y = f"{pan_y}*(1-{progress})"

        elif effect == KenBurnsEffect.PAN_DOWN:
            zoom = str(zoom_start)
            x = "0"
            y = f"{pan_y}*{progress}"

        elif effect == KenBurnsEffect.ZOOM_IN_LEFT:
            zoom = f"{zoom_start}+{zoom_end - zoom_start}*{progress}"
            x = f"(iw/4)*(1-{progress})"
            y = f"ih/2-(ih/zoom/2)"

        elif effect == KenBurnsEffect.ZOOM_IN_RIGHT:
            zoom = f"{zoom_start}+{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)+{pan_x}*{progress}"
            y = f"ih/2-(ih/zoom/2)"

        else:  # ZOOM_OUT_CENTER
            zoom = f"{zoom_end}-{zoom_end - zoom_start}*{progress}"
            x = f"iw/2-(iw/zoom/2)"
            y = f"ih/2-(ih/zoom/2)"

        return zoom, x, y


def get_ken_burns_filter(effect_name: str, duration: float,
                         width: int = 1920, height: int = 1080,
                         intensity: str = "normal",
                         fade_duration: float = 0.5,
                         simple_mode: bool = False) -> str:
    """
    Helper function để tạo filter nhanh.

    Args:
        effect_name: Tên effect (zoom_in, pan_left, etc.)
        duration: Thời lượng clip
        width: Chiều rộng output
        height: Chiều cao output
        intensity: Cường độ (subtle/normal/strong)
        fade_duration: Thời gian fade
        simple_mode: Dùng linear thay vì easing

    Returns:
        FFmpeg filter string
    """
    # Parse effect name
    try:
        effect = KenBurnsEffect(effect_name)
    except ValueError:
        effect = KenBurnsEffect.ZOOM_IN  # Default

    generator = KenBurnsGenerator(width, height, intensity)
    return generator.generate_filter(effect, duration, fade_duration, simple_mode)


# Test
if __name__ == "__main__":
    kb = KenBurnsGenerator(1920, 1080, intensity="normal")

    print("Ken Burns Effects Demo:")
    print("=" * 60)

    for effect in KenBurnsEffect:
        vf = kb.generate_filter(effect, duration=5, fade_duration=0.5, simple_mode=True)
        print(f"\n{effect.value}:")
        print(f"  {vf[:100]}...")
