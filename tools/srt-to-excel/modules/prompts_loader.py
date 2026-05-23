"""
VE3 Tool - Prompts Loader
=========================
Load prompts from config/prompts.yaml
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

from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def _load_prompts_yaml():
    """Load prompts.yaml file."""
    # Find config file
    config_paths = [
        Path(__file__).parent.parent / "config" / "prompts.yaml",
        Path("config/prompts.yaml"),
        Path(os.environ.get("VE3_CONFIG_DIR", "config")) / "prompts.yaml",
    ]

    for path in config_paths:
        if path.exists():
            if yaml:
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f)
            else:
                # Fallback: simple text read
                with open(path, "r", encoding="utf-8") as f:
                    return {"_raw": f.read()}

    return {}


# Cache loaded prompts
_PROMPTS_CACHE = None


def _get_prompts():
    """Get prompts with caching."""
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is None:
        _PROMPTS_CACHE = _load_prompts_yaml()
    return _PROMPTS_CACHE


def get_analyze_story_prompt() -> str:
    """Get the analyze story prompt template."""
    prompts = _get_prompts()
    return prompts.get("analyze_story", "")


def get_generate_scenes_prompt() -> str:
    """Get the generate scenes prompt template."""
    prompts = _get_prompts()
    return prompts.get("generate_scenes", "")


def get_smart_divide_scenes_prompt() -> str:
    """Get the smart divide scenes prompt template."""
    prompts = _get_prompts()
    return prompts.get("smart_divide_scenes", prompts.get("divide_scenes", ""))


def get_global_style() -> str:
    """Get the global style string."""
    prompts = _get_prompts()
    return prompts.get("global_style_string", "Cinematic, 4K photorealistic, soft film grain")


def get_negative_prompt() -> str:
    """Get the negative prompt string."""
    prompts = _get_prompts()
    return prompts.get("negative_prompt_string", "cartoon, 3d render, text, watermark")


def get_visual_clarity() -> str:
    """Get the visual clarity string."""
    prompts = _get_prompts()
    return prompts.get("visual_clarity_string", "Face illuminated by soft volumetric light")
