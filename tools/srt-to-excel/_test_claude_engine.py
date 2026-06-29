# -*- coding: utf-8 -*-
"""Standalone test harness for ClaudeCliEngine.

Usage:
    python _test_claude_engine.py <project_dir> <code> [model]
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        if s and hasattr(s, "reconfigure"):
            try: s.reconfigure(encoding="utf-8", errors="replace")
            except Exception: pass

from pathlib import Path
from modules.claude_cli_engine import ClaudeCliEngine
from modules.excel_manager import PromptWorkbook


def log(msg, level="INFO"):
    print(f"[{level}] {msg}", flush=True)


def main():
    proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\VE3_SUITE\PROJECTS_CCTEST\CCSMALL")
    code = sys.argv[2] if len(sys.argv) > 2 else "CCSMALL"
    model = sys.argv[3] if len(sys.argv) > 3 else ""

    cfg = {
        "claude_cli_model": model,
        "min_scene_duration": 5,
        "max_scene_duration": 8,
    }
    eng = ClaudeCliEngine(cfg)
    ok = eng.run(proj, code, log_callback=log)
    print("\n=== RESULT:", "OK" if ok else "FAILED", "===")
    if not ok:
        sys.exit(1)

    # Validate workbook
    xlsx = proj / f"{code}_prompts.xlsx"
    wb = PromptWorkbook(xlsx).load_or_create()
    scenes = wb.get_scenes()
    chars = wb.get_characters()
    print(f"\nWorkbook: {xlsx}")
    print(f"Characters: {[(c.id, c.image_file) for c in chars]}")
    print(f"Scenes: {len(scenes)}")
    bad = 0
    prev_end = None
    for s in scenes[:6]:
        print(f"  #{s.scene_id} {s.srt_start} -> {s.srt_end} ({s.duration}s) chars={s.characters_used} refs={s.reference_files}")
        print(f"     IMG: {s.img_prompt[:110]}")
        print(f"     VID: {s.video_prompt[:90]}")
    # duration stats
    durs = [s.duration for s in scenes]
    if durs:
        print(f"\nDuration min/max/avg: {min(durs):.1f}/{max(durs):.1f}/{sum(durs)/len(durs):.1f}s")
        out_of_band = [s.scene_id for s in scenes if not (4.0 <= s.duration <= 11.0)]
        print(f"Out-of-band (not 4-11s): {out_of_band}")
    # uniqueness
    imgs = [s.img_prompt for s in scenes]
    print(f"Unique img prompts: {len(set(imgs))}/{len(imgs)}")
    # coverage: durations sum ~ total
    print(f"Total covered: {sum(durs):.1f}s")


if __name__ == "__main__":
    main()
