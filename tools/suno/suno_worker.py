#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
suno_worker.py — Auto generate & download Suno music from Excel music sheet

Usage:
    python suno_worker.py --excel "../../PROJECTS/KA5-0072/KA5-0072_prompts.xlsx" \
                          --output "../../PROJECTS/KA5-0072/music" \
                          --profile "./chrome_profile_suno"

    python suno_worker.py --token "eyJhbGc..." --excel ...   # dùng token trực tiếp

Flow:
    1. Đọc music sheet từ Excel (tracks có status != 'done')
    2. Lấy Bearer token (cache → DrissionPage → network listener)
    3. Với mỗi track:
       a. Gọi Suno API generate (prompt đã ghép mood)
       b. Poll đến khi mp3 ready (~2-3 phút)
       c. Download → music/{music_id}.mp3
       d. Update Excel: status=done, suno_url=...
    4. Report kết quả
"""
import sys, os, argparse, logging, time, json
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("suno_worker")


# ─── Paths ────────────────────────────────────────────────────────────────────
HERE = Path(__file__).parent
SUITE_ROOT = HERE.resolve().parents[1]

# Excel manager path (srt-to-excel project)
SRT_TO_EXCEL = SUITE_ROOT / "tools" / "srt-to-excel"
VE3_TOOL     = SUITE_ROOT / "tools" / "ve3"


def _find_excel_manager():
    """Thêm đúng path vào sys.path để import excel_manager."""
    for p in [SRT_TO_EXCEL, VE3_TOOL]:
        mod = p / "modules" / "excel_manager.py"
        if mod.exists():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            log.info(f"[EXCEL] Using excel_manager from: {p}")
            return True
    log.error("[EXCEL] Cannot find excel_manager.py!")
    return False


# ─── Token refresh ────────────────────────────────────────────────────────────
def refresh_token() -> str:
    """
    Mở Chrome Portable → lấy Clerk JWT mới → lưu suno_token.txt → return.
    Dùng khi token expire (422 Token validation failed).
    """
    log.info("[TOKEN] Refreshing via Chrome Portable...")
    try:
        from capture_token import capture_via_drission
        tok_file = HERE / "suno_token.txt"
        token = capture_via_drission()
        if token:
            tok_file.write_text(token, encoding="utf-8")
            log.info(f"[TOKEN] New token: {token[:30]}...")
            return token
    except Exception as e:
        log.error(f"[TOKEN] Refresh error: {e}")
    return ""


# ─── Main worker ──────────────────────────────────────────────────────────────
def process_excel(
    excel_path: Path,
    output_dir: Path,
    token: str = "",
    model: str = "chirp-v3-5",
    dry_run: bool = False,
    track_ids: list = None,
    pick: str = "best",
    token_manager=None,   # TokenManager instance (preferred)
):
    """
    Đọc Excel music sheet → generate & download từng track.
    token_manager ưu tiên hơn static token.
    """
    from suno_api import SunoAPIClient

    if not _find_excel_manager():
        return

    from modules.excel_manager import PromptWorkbook

    wb = PromptWorkbook(excel_path)
    wb.load_or_create()

    tracks = wb.get_music_tracks()
    if not tracks:
        log.error("[EXCEL] Không có tracks trong music sheet!")
        return

    log.info(f"[EXCEL] {len(tracks)} tracks found")

    # Filter: chỉ lấy tracks chưa done (hoặc theo track_ids)
    pending = []
    for t in tracks:
        mid    = str(t.get("music_id", ""))
        status = str(t.get("status", "")).lower()
        prompt = (t.get("suno_prompt") or "").strip()

        if track_ids and mid not in track_ids:
            continue
        if status == "done":
            mp3 = output_dir / f"{mid}.mp3"
            if mp3.exists():
                log.info(f"[SKIP] Track {mid}: done + mp3 exists")
                continue
            else:
                log.warning(f"[REDO] Track {mid}: status=done nhưng mp3 không có")
        if not prompt:
            log.warning(f"[SKIP] Track {mid}: no suno_prompt")
            continue
        pending.append(t)

    log.info(f"[INFO] {len(pending)} tracks cần generate")

    if dry_run:
        log.info("[DRY RUN] Không gọi API. Tracks sẽ generate:")
        for t in pending:
            log.info(f"  [{t['music_id']}] {t.get('title','?')[:50]}")
            log.info(f"    => {t.get('suno_prompt','')[:100]}")
        return

    # Build API client
    if token_manager:
        # Preferred: live Chrome Portable → fresh token per request
        client = SunoAPIClient(
            token_provider=token_manager.get_token,
            model=model,
        )
        log.info("[CLIENT] Using TokenManager (live Chrome refresh)")
    else:
        # Fallback: static token
        client = SunoAPIClient(token=token, model=model)
        log.info("[CLIENT] Using static token")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Stats
    success = 0
    failed  = 0

    for idx, track in enumerate(pending):
        mid    = str(track["music_id"])
        title  = track.get("title", f"Track {mid}")
        prompt = track.get("suno_prompt", "")
        out_mp3 = output_dir / f"{mid}.mp3"

        log.info(f"\n{'='*60}")
        log.info(f"[{idx+1}/{len(pending)}] Track {mid}: {title}")
        log.info(f"  Prompt: {prompt[:100]}...")

        # Mark as processing
        try:
            wb.update_music_track(mid, status="generating")
            wb.save()
        except Exception:
            pass

        ok, result = client.generate_and_download(
            prompt       = prompt,
            output_path  = out_mp3,
            title        = title,
            pick         = pick,
            poll_interval= 10.0,
            max_wait     = 360,
        )

        # Fallback: if 422 and using static token, try refresh once
        if not ok and "422" in str(result) and not token_manager:
            log.warning("[TOKEN] 422 on static token — trying refresh...")
            new_tok = refresh_token()
            if new_tok:
                client = SunoAPIClient(token=new_tok, model=model)
                ok, result = client.generate_and_download(
                    prompt       = prompt,
                    output_path  = out_mp3,
                    title        = title,
                    pick         = pick,
                    poll_interval= 10.0,
                    max_wait     = 360,
                )

        if ok:
            success += 1
            log.info(f"  [OK] → {out_mp3}")
            try:
                wb.update_music_track(mid, status="done", suno_url=result)
                wb.save()
                log.info(f"  [EXCEL] Updated track {mid}: status=done")
            except Exception as e:
                log.warning(f"  [EXCEL] Cannot update: {e}")
        else:
            failed += 1
            log.error(f"  [FAIL] {result}")
            try:
                wb.update_music_track(mid, status="error")
                wb.save()
            except Exception:
                pass

        # Throttle: Suno rate limit — đợi giữa các tracks
        if idx < len(pending) - 1:
            delay = 8
            log.info(f"  [WAIT] {delay}s throttle before next track...")
            time.sleep(delay)

    log.info(f"\n{'='*60}")
    log.info(f"DONE: {success} success, {failed} failed / {len(pending)} total")
    log.info(f"Output: {output_dir}")


# ─── Token acquisition ────────────────────────────────────────────────────────
def acquire_token(args) -> str:
    """Lấy token theo thứ tự ưu tiên."""
    # 1. --token arg
    if args.token:
        log.info("[TOKEN] Using provided token")
        return args.token

    # 2. env var
    env_token = os.environ.get("SUNO_TOKEN", "")
    if env_token:
        log.info("[TOKEN] Using SUNO_TOKEN env var")
        return env_token

    # 3. Token file
    tok_file = HERE / "suno_token.txt"
    if tok_file.exists():
        t = tok_file.read_text("utf-8").strip()
        if t and len(t) > 20:
            log.info(f"[TOKEN] Using suno_token.txt: {t[:20]}...")
            return t

    # 4. DrissionPage network listener (tốt nhất)
    log.info("[TOKEN] Attempting DrissionPage network listener...")
    from token_extractor import get_token_from_network_listener, get_bearer_token

    profile = args.profile or ""

    # Try network listener first (most reliable)
    token = get_token_from_network_listener(
        chrome_profile=profile,
        timeout=60,
    )
    if token:
        return token

    # Fallback: Clerk JS extraction
    token = get_bearer_token(
        chrome_profile=profile,
        headless=False,
        timeout=60,
    )
    if token:
        return token

    log.error("[TOKEN] Cannot acquire token. Options:")
    log.error("  1. Chạy với --token <your_token>")
    log.error("  2. Lưu token vào suno_token.txt")
    log.error("  3. Set env var SUNO_TOKEN=...")
    log.error("  4. Đảm bảo Chrome profile --profile đã đăng nhập Suno")
    return ""


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Auto-generate Suno music from Excel music sheet"
    )
    parser.add_argument("--excel",   required=True,  help="Path to *_prompts.xlsx")
    parser.add_argument("--output",  default="",     help="Dir to save mp3 (default: excel_dir/music)")
    parser.add_argument("--token",   default="",     help="Suno bearer token (skip browser)")
    parser.add_argument("--profile", default="",     help="Chrome profile dir (logged in to Suno)")
    parser.add_argument("--model",   default="chirp-v3-5", help="Suno model")
    parser.add_argument("--pick",    default="best", choices=["best","first"],
                        help="Which of the 2 generated clips to keep")
    parser.add_argument("--ids",     default="",     help="Comma-separated track IDs to generate")
    parser.add_argument("--dry-run", action="store_true", help="Show tasks without generating")
    parser.add_argument("--force-token", action="store_true", help="Force new token (ignore cache)")
    args = parser.parse_args()

    excel_path = Path(args.excel)
    if not excel_path.exists():
        log.error(f"Excel not found: {excel_path}")
        sys.exit(1)

    output_dir = Path(args.output) if args.output else excel_path.parent / "music"
    track_ids  = [x.strip() for x in args.ids.split(",") if x.strip()] or None

    log.info(f"Excel:  {excel_path}")
    log.info(f"Output: {output_dir}")
    log.info(f"Model:  {args.model}")

    # Get token
    if not args.dry_run:
        if args.force_token:
            for f in [HERE / "suno_token.txt", HERE / ".suno_token.txt"]:
                f.unlink(missing_ok=True)
                log.info(f"[TOKEN] Cleared: {f.name}")

        token = acquire_token(args)
        if not token:
            sys.exit(1)
    else:
        token = "DRY_RUN"

    # ── Token / Chrome Portable ──────────────────────────────────────
    if args.dry_run:
        process_excel(
            excel_path=excel_path, output_dir=output_dir,
            model=args.model, dry_run=True, track_ids=track_ids, pick=args.pick,
        )
        return

    # Ưu tiên: TokenManager (connect Chrome đang mở → fresh token mỗi request)
    try:
        from token_manager import TokenManager
        tm = TokenManager()
        if tm.start():
            log.info("[TOKEN] TokenManager connected — Chrome Portable alive")
            try:
                process_excel(
                    excel_path    = excel_path,
                    output_dir    = output_dir,
                    model         = args.model,
                    dry_run       = False,
                    track_ids     = track_ids,
                    pick          = args.pick,
                    token_manager = tm,
                )
            finally:
                tm.stop()
            return
        else:
            log.error("[TOKEN] TokenManager failed — Chrome Portable chưa mở!")
            log.error("[TOKEN] → Chạy start_chrome_suno.bat trước, đăng nhập Suno")
            log.error("[TOKEN] → Sau đó chạy lại worker")
    except Exception as e:
        log.warning(f"[TOKEN] TokenManager error: {e}")

    # Fallback: static token từ file / args
    token = acquire_token(args)
    if not token:
        log.error("[TOKEN] Không có token. Hướng dẫn:")
        log.error("  1. Chạy: start_chrome_suno.bat")
        log.error("  2. Đăng nhập Suno")
        log.error("  3. Chạy lại: python suno_worker.py ...")
        sys.exit(1)

    process_excel(
        excel_path = excel_path,
        output_dir = output_dir,
        token      = token,
        model      = args.model,
        dry_run    = False,
        track_ids  = track_ids,
        pick       = args.pick,
    )


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
