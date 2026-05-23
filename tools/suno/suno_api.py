"""
suno_api.py — Suno internal API client
Auth: Bearer token captured from browser (via DrissionPage interceptor).
Endpoints: studio-api.prod.suno.com
"""
import time
import requests
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

log = logging.getLogger("suno_api")

# Suno internal endpoints — confirmed via network intercept 2025
BASE_URL     = "https://studio-api-prod.suno.com"
GEN_URL      = f"{BASE_URL}/api/generate/v2-web/"   # was v2/, now v2-web/
FEED_URL     = f"{BASE_URL}/api/feed/v3"             # POST with {ids:[...]}
CONCAT_URL   = f"{BASE_URL}/api/generate/concat/v2/"

# Default model (chirp-v4 if available, else v3-5)
DEFAULT_MODEL = "chirp-v3-5"

HEADERS_TEMPLATE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://suno.com",
    "Referer": "https://suno.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


class SunoAPIClient:
    """Calls Suno internal API using a captured bearer token."""

    def __init__(self, token: str = "", model: str = DEFAULT_MODEL,
                 token_provider=None):
        """
        token:          Static bearer token (for one-shot use)
        token_provider: Callable () -> str, called before each request for fresh token
        """
        self._static_token = token
        self._token_provider = token_provider
        self.model  = model
        self.session = requests.Session()
        self.session.headers.update(HEADERS_TEMPLATE)
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _refresh_auth(self):
        """Update Authorization header with fresh token."""
        if self._token_provider:
            tok = self._token_provider()
            if tok:
                self.session.headers["Authorization"] = f"Bearer {tok}"
                return tok
        return self._static_token

    # ─────────────────────────────────────────────────────────────────
    def generate(
        self,
        prompt: str,
        title: str = "",
        make_instrumental: bool = True,
        tags: str = "",
        model: Optional[str] = None,
    ) -> List[str]:
        """
        Submit a generation request.
        Returns list of clip IDs (usually 2 per request).
        Raises on HTTP/network error.
        """
        payload = {
            "prompt":              prompt,
            "tags":                tags,
            "title":               title or "",
            "make_instrumental":   make_instrumental,
            "mv":                  model or self.model,
            "generation_type":     "TEXT",
        }
        # Refresh auth token before every request
        self._refresh_auth()
        log.info(f"[GEN] Submitting: {title!r} ({len(prompt)} chars)")
        resp = self.session.post(GEN_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Response: {"clips": [{id, ...}, ...]}
        clips = data.get("clips") or []
        ids = [c["id"] for c in clips if c.get("id")]
        log.info(f"[GEN] Got {len(ids)} clip IDs: {ids}")
        return ids

    # ─────────────────────────────────────────────────────────────────
    def get_clips(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch clip metadata (status, audio_url, etc.) via POST /api/feed/v3."""
        self._refresh_auth()
        # Suno 2025: feed endpoint is POST with JSON body
        payload = {"ids": ids}
        resp = self.session.post(FEED_URL, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # Response: list of clips OR {"clips": [...]}
        if isinstance(data, list):
            return data
        return data.get("clips") or data.get("items") or []

    # ─────────────────────────────────────────────────────────────────
    def wait_for_audio(
        self,
        ids: List[str],
        poll_interval: float = 10.0,
        max_wait: int = 300,
    ) -> List[Dict[str, Any]]:
        """
        Poll Suno feed until our submitted clips have audio_url.
        feed/v3 may return ALL user clips — we strictly filter to submitted IDs only.
        """
        target_ids = set(ids)      # O(1) lookup
        done: Dict[str, Dict] = {} # only contains clips from target_ids
        start = time.time()

        while time.time() - start < max_wait:
            remaining = [i for i in ids if i not in done]
            if not remaining:
                break

            try:
                all_clips = self.get_clips(remaining)
            except Exception as e:
                log.warning(f"[POLL] Feed error: {e} — retrying in {poll_interval}s")
                time.sleep(poll_interval)
                continue

            for clip in all_clips:
                cid    = clip.get("id", "")
                status = clip.get("status", "")
                aurl   = clip.get("audio_url", "")

                if cid not in target_ids:
                    continue   # skip old clips not in this request

                log.debug(f"  [{cid[:8]}] status={status}")
                if status in ("streaming", "complete", "error"):
                    if aurl or status == "error":
                        done[cid] = clip
                        if status == "error":
                            log.warning(f"[POLL] Clip {cid[:8]} errored")

            elapsed = int(time.time() - start)
            ready = len(done)
            log.info(f"[POLL] {ready}/{len(target_ids)} ready — {elapsed}s elapsed")

            if ready < len(target_ids):
                time.sleep(poll_interval)

        if len(done) < len(target_ids):
            log.warning(f"[POLL] Timeout: {len(done)}/{len(target_ids)} clips ready")

        # Return ONLY clips from our submitted IDs
        return [v for k, v in done.items() if k in target_ids]

    # ─────────────────────────────────────────────────────────────────
    def download_clip(
        self,
        clip: Dict[str, Any],
        output_path: Path,
    ) -> bool:
        """
        Download a clip's audio to output_path.
        Returns True on success.
        """
        url  = clip.get("audio_url", "")
        if not url:
            log.warning(f"[DL] No audio_url for clip {clip.get('id','?')[:8]}")
            return False

        try:
            log.info(f"[DL] {output_path.name} ← {url[:60]}...")
            r = self.session.get(url, timeout=120, stream=True)
            r.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            size_kb = output_path.stat().st_size // 1024
            log.info(f"[DL] OK → {output_path} ({size_kb} KB)")
            return True
        except Exception as e:
            log.error(f"[DL] FAIL {output_path.name}: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────
    def generate_and_download(
        self,
        prompt: str,
        output_path: Path,
        title: str = "",
        tags: str = "",
        pick: str = "best",   # "best" = pick longer clip, "first" = first clip
        poll_interval: float = 5.0,
        max_wait: int = 300,
    ) -> Tuple[bool, str]:
        """
        Full pipeline: generate → poll → pick best clip → download.
        Returns (success, audio_url_or_error).
        """
        try:
            ids   = self.generate(prompt=prompt, title=title, tags=tags)
            if not ids:
                return False, "No clip IDs returned by API"

            clips = self.wait_for_audio(ids, poll_interval, max_wait)
            if not clips:
                return False, "No clips completed within timeout"

            # Pick best: longest duration, or first available
            valid = [c for c in clips if c.get("audio_url")]
            if not valid:
                return False, "All clips errored"

            if pick == "best":
                target = max(
                    valid,
                    key=lambda c: c.get("metadata", {}).get("duration") or 0
                )
            else:
                target = valid[0]

            ok = self.download_clip(target, output_path)
            url = target.get("audio_url", "")
            return ok, url

        except requests.HTTPError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            log.error(f"[API] {msg}")
            return False, msg
        except Exception as e:
            log.error(f"[API] Exception: {e}")
            return False, str(e)
