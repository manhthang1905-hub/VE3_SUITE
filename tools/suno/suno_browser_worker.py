#!/usr/bin/env python3
"""
Drive Suno through the live browser UI and capture generated audio from
network responses. This avoids the repeated 422 failures seen from the
reverse-engineered Python client.
"""
import argparse
import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from token_manager import TokenManager


API_HOST = "studio-api-prod.suno.com"
CREATE_URL = "https://suno.com/create"

log = logging.getLogger("suno_browser_worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def load_tracks(path: Path) -> List[Dict[str, str]]:
    ext = path.suffix.lower()
    delimiter = "\t" if ext in {".tsv", ".txt"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows = []
        for row in reader:
            music_id = (row.get("music_id") or "").strip()
            prompt = (row.get("suno_prompt") or "").strip()
            if music_id and prompt:
                rows.append({"music_id": music_id, "suno_prompt": prompt})
        return rows


def normalize_packet_body(body: Any) -> Any:
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        text = body.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return text
    return body


def extract_clips(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("clips", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def download_file(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        with output_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)


class BrowserSunoWorker:
    def __init__(self, page):
        self.page = page

    def prepare_page(self) -> None:
        if "suno.com/create" not in (self.page.url or ""):
            self.page.get(CREATE_URL)
            time.sleep(2)
        self._stabilize_window()
        self._dismiss_overlays()
        self._ensure_advanced_mode()

    def _stabilize_window(self) -> None:
        try:
            self.page.run_js(
                """
                try {
                  window.scrollTo(0, 0);
                  window.dispatchEvent(new Event('resize'));
                } catch (e) {}
                return true;
                """,
                as_expr=False,
            )
        except Exception:
            pass
        time.sleep(1)

    def _dismiss_overlays(self) -> None:
        # Cookie / banner popups change frequently. Ignore misses.
        for xpath in [
            '//button[normalize-space()="Allow All"]',
            '//button[normalize-space()="Dismiss"]',
            '//button[@aria-label="Close banner"]',
        ]:
            try:
                btn = self.page.ele(f"xpath:{xpath}", timeout=0.5)
                if btn:
                    btn.click(by_js=True)
                    time.sleep(0.5)
            except Exception:
                pass

    def _click_button_text(self, text: str) -> bool:
        try:
            btn = self.page.ele(f'xpath://button[normalize-space()="{text}"]', timeout=1)
            if btn:
                btn.click(by_js=True)
                return True
        except Exception:
            pass
        return False

    def _ensure_advanced_mode(self) -> None:
        # Match the user's manual flow first: click the span text "Advanced",
        # then fall back to the button text selector if the DOM changes.
        try:
            self.page.run_js("window.scrollTo(0, 0);", as_expr=False)
        except Exception:
            pass
        advanced_selectors = [
            'xpath://span[normalize-space()="Advanced"]',
            'xpath://button[.//span[normalize-space()="Advanced"]]',
            'xpath://button[normalize-space()="Advanced"]',
        ]
        for selector in advanced_selectors:
            try:
                ele = self.page.ele(selector, timeout=1)
                if ele:
                    ele.click(by_js=True)
                    time.sleep(1)
                    return
            except Exception:
                pass
        self._click_button_text("Advanced")
        time.sleep(1)

    def _set_prompt(self, prompt: str) -> None:
        js = r"""
        const btn = Array.from(document.querySelectorAll('button'))
          .find(e => (e.getAttribute('aria-label') || '') === 'Create song');
        if (!btn) return -1;
        const btnRect = btn.getBoundingClientRect();
        const areas = Array.from(document.querySelectorAll('textarea'));
        const candidates = areas.map((e, i) => {
          const r = e.getBoundingClientRect();
          return {
            i,
            ph: (e.placeholder || '').toLowerCase(),
            visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length),
            width: r.width,
            height: r.height,
            distance: btnRect.y - (r.y + r.height),
          };
        }).filter(x =>
          x.visible &&
          x.width > 120 &&
          x.height > 30 &&
          x.distance >= -10 &&
          !x.ph.includes('lyrics')
        );
        candidates.sort((a, b) => a.distance - b.distance);
        return candidates.length ? candidates[0].i : -1;
        """
        idx = self.page.run_js(js, as_expr=False)
        if not isinstance(idx, int) or idx < 0:
            raise RuntimeError("Could not locate active Advanced prompt textarea")
        areas = self.page.eles("tag:textarea")
        if idx >= len(areas):
            raise RuntimeError("Advanced prompt textarea index is out of range")
        area = areas[idx]
        area.clear()
        area.input(prompt)
        time.sleep(0.8)

    def _create_button(self):
        btn = self.page.ele(
            'xpath://button[@aria-label="Create song"]',
            timeout=2,
        )
        if not btn:
            raise RuntimeError("Create button not found")
        return btn

    def _click_create(self) -> None:
        btn = self._create_button()
        btn.click(by_js=True)

    def _wait_for_generation(
        self,
        timeout: int,
        poll_log_every: int = 30,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        target_ids: List[str] = []
        ready: Dict[str, Dict[str, Any]] = {}
        start = time.time()
        last_log_at = 0

        while time.time() - start < timeout:
            packet = self.page.listen.wait(timeout=3)
            elapsed = int(time.time() - start)

            if not packet:
                if elapsed - last_log_at >= poll_log_every:
                    log.info("[WAIT] %ss elapsed, %s/%s clips ready", elapsed, len(ready), len(target_ids) or "?")
                    last_log_at = elapsed
                continue

            url = getattr(packet, "url", "") or ""
            if API_HOST not in url:
                continue

            response = getattr(packet, "response", None)
            body = normalize_packet_body(getattr(response, "body", None) if response else None)

            if "/api/generate/v2-web/" in url:
                status = getattr(response, "status", None)
                if status and int(status) >= 400:
                    raise RuntimeError(f"Browser generate failed with HTTP {status}: {body}")
                clips = extract_clips(body)
                target_ids = [clip.get("id", "") for clip in clips if clip.get("id")]
                log.info("[GEN] Browser returned %s clip ids", len(target_ids))
                for clip in clips:
                    cid = clip.get("id", "")
                    if cid and clip.get("audio_url"):
                        ready[cid] = clip
                continue

            if target_ids and "/api/feed/v3" in url:
                for clip in extract_clips(body):
                    cid = clip.get("id", "")
                    if cid not in target_ids:
                        continue
                    status = clip.get("status", "")
                    if clip.get("audio_url") or status == "error":
                        ready[cid] = clip
                if len(ready) >= len(target_ids):
                    return [ready[cid] for cid in target_ids if cid in ready], target_ids

        raise TimeoutError(f"Timed out waiting for Suno clips after {timeout}s")

    def generate_and_download(
        self,
        prompt: str,
        output_path: Path,
        timeout: int = 420,
        pick: str = "best",
    ) -> Tuple[bool, str]:
        self.prepare_page()
        self.page.listen.start(API_HOST)
        try:
            self._set_prompt(prompt)
            self._click_create()
            clips, clip_ids = self._wait_for_generation(timeout=timeout)
        finally:
            try:
                self.page.listen.stop()
            except Exception:
                pass

        valid = [clip for clip in clips if clip.get("audio_url")]
        if not valid:
            return False, f"No audio_url returned for clip ids: {clip_ids}"

        if pick == "best":
            chosen = max(valid, key=lambda c: c.get("metadata", {}).get("duration") or 0)
        else:
            chosen = valid[0]

        audio_url = chosen.get("audio_url", "")
        if not audio_url:
            return False, "Chosen clip has no audio_url"

        download_file(audio_url, output_path)
        return True, audio_url


def write_summary(path: Path, rows: Iterable[Dict[str, str]]) -> None:
    lines = ["music_id\tstatus\tsize\tresult\tpath"]
    for row in rows:
        lines.append(
            "\t".join(
                [
                    row["music_id"],
                    row["status"],
                    row["size"],
                    row["result"],
                    row["path"],
                ]
            )
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Suno tracks through the browser UI")
    parser.add_argument("--input", required=True, help="TSV/CSV with columns: music_id, suno_prompt")
    parser.add_argument("--output", required=True, help="Directory to save mp3 files")
    parser.add_argument("--timeout", type=int, default=420, help="Max seconds to wait per track")
    parser.add_argument("--pick", choices=["best", "first"], default="best")
    parser.add_argument("--delay", type=float, default=8.0, help="Seconds between tracks")
    parser.add_argument("--skip-existing", action="store_true", help="Skip tracks whose mp3 already exists")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.tsv"

    tracks = load_tracks(input_path)
    if not tracks:
        raise SystemExit("No valid tracks found in input file")

    results: List[Dict[str, str]] = []

    with TokenManager() as tm:
        if not tm.start():
            raise SystemExit("Could not connect to Suno browser session on port 9333")

        worker = BrowserSunoWorker(tm._page)

        for idx, track in enumerate(tracks, start=1):
            music_id = track["music_id"]
            prompt = track["suno_prompt"]
            out_file = output_dir / f"{music_id}.mp3"

            if args.skip_existing and out_file.exists():
                size = str(out_file.stat().st_size)
                log.info("[%s/%s] Track %s skipped (existing file)", idx, len(tracks), music_id)
                results.append(
                    {
                        "music_id": music_id,
                        "status": "skipped",
                        "size": size,
                        "result": "existing file",
                        "path": str(out_file),
                    }
                )
                write_summary(summary_path, results)
                continue

            log.info("[%s/%s] Track %s", idx, len(tracks), music_id)
            ok = False
            result = ""
            try:
                ok, result = worker.generate_and_download(
                    prompt=prompt,
                    output_path=out_file,
                    timeout=args.timeout,
                    pick=args.pick,
                )
            except Exception as e:
                result = str(e)

            size = str(out_file.stat().st_size if out_file.exists() else 0)
            status = "ok" if ok else "fail"
            results.append(
                {
                    "music_id": music_id,
                    "status": status,
                    "size": size,
                    "result": result,
                    "path": str(out_file),
                }
            )
            write_summary(summary_path, results)

            if ok:
                log.info("[OK] %s -> %s", music_id, out_file)
            else:
                log.error("[FAIL] %s -> %s", music_id, result)
                # Continue with next track instead of breaking

            if idx < len(tracks):
                time.sleep(args.delay)

    log.info("Summary: %s", summary_path)


if __name__ == "__main__":
    main()
