"""
FlowKit Gateway — single API endpoint for VE3 main machine.

Exposes the SAME API as server/app.py so VE3 worker doesn't need changes.
Routes requests to available FlowKit agents with:
- Load balancing (round-robin among healthy agents)
- 403 rotation (mark agent as cooling, switch to next)
- Task queue with async processing
- Video polling management
"""
import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from recovery_manager import RecoveryManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ─── Load Config ─────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config.yaml"
if not CONFIG_PATH.exists():
    print(f"[FATAL] config.yaml not found: {CONFIG_PATH}")
    print("  Copy config.yaml into the flowkit directory and restart.")
    sys.exit(1)
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)

GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", CONFIG.get("gateway_port", 5100)))
GATEWAY_HOST = os.environ.get("GATEWAY_HOST", CONFIG.get("gateway_host", "0.0.0.0"))

ROTATION = CONFIG.get("rotation", {})
MAX_CONSECUTIVE_403 = ROTATION.get("max_consecutive_403", 3)
COOLDOWN_SECONDS = ROTATION.get("cooldown_seconds", 300)
MAX_RETRIES = ROTATION.get("max_retries_per_request", 3)

RATE_LIMIT = CONFIG.get("rate_limit", {})
COOLDOWN_PER_INSTANCE = RATE_LIMIT.get("cooldown_per_instance", 5)
MAX_CONCURRENT = RATE_LIMIT.get("max_concurrent_per_instance", 1)

QUOTA = CONFIG.get("quota", {})
QUOTA_RETRY_COUNT = QUOTA.get("retry_count", 3)
QUOTA_RETRY_DELAY = QUOTA.get("retry_delay", 15)
QUOTA_COOLDOWN_SECONDS = QUOTA.get("cooldown_seconds", 120)

IMAGE_MODEL_FALLBACK = {"GEM_PIX_2": "NARWHAL", "NARWHAL": "GEM_PIX_2"}


def _switch_image_model(body_json: dict) -> str:
    """Switch imageModelName in body_json to fallback. Returns new model name or empty."""
    for req in body_json.get("requests", []):
        if isinstance(req, dict):
            model = req.get("imageModelName", "")
            if model in IMAGE_MODEL_FALLBACK:
                req["imageModelName"] = IMAGE_MODEL_FALLBACK[model]
                return IMAGE_MODEL_FALLBACK[model]
    return ""


TIMEOUTS = CONFIG.get("timeouts", {})
IMAGE_TIMEOUT = TIMEOUTS.get("image_generation", 120)
VIDEO_SUBMIT_TIMEOUT = TIMEOUTS.get("video_submit", 60)
VIDEO_POLL_TIMEOUT = TIMEOUTS.get("video_poll", 420)
VIDEO_POLL_INTERVAL = TIMEOUTS.get("video_poll_interval", 10)
TASK_WATCHDOG_TIMEOUT = TIMEOUTS.get("task_watchdog", 600)


# ─── Instance State ──────────────────────────────────────────

class AgentInstance:
    """Represents one FlowKit agent (1 Chrome copy)."""

    def __init__(self, cfg: dict):
        self.name = cfg["name"]
        self.api_port = cfg["api_port"]
        self.ws_port = cfg["ws_port"]
        self.enabled = cfg.get("enabled", True)
        self.base_url = f"http://127.0.0.1:{self.api_port}"

        # State
        self.healthy = False
        self.extension_connected = False
        self.flow_key_present = False
        self.consecutive_403 = 0
        self.cooling_until: float = 0
        self.quota_exhausted_until: float = 0
        self.active_image_model: str = ""  # if set, override imageModelName (fallback from 429)
        self.processing_count = 0
        self.total_completed = 0
        self.total_failed = 0
        self.last_request_time: float = 0
        self.last_health_check: float = 0

        # Self-heal tracking
        self.self_heal_failures = 0
        self.next_self_heal_at: float = 0
        self.was_healthy_once = False
        self.consecutive_unhealthy = 0

    @property
    def available(self) -> bool:
        """Can this instance accept new work?"""
        if not self.enabled or not self.healthy:
            return False
        if not self.extension_connected:
            return False
        if self.cooling_until > time.time():
            return False
        if self.quota_exhausted_until > time.time():
            return False
        if self.processing_count >= MAX_CONCURRENT:
            return False
        return True

    @property
    def is_cooling(self) -> bool:
        return self.cooling_until > time.time()

    @property
    def is_quota_exhausted(self) -> bool:
        return self.quota_exhausted_until > time.time()

    def mark_quota_exhausted(self):
        self.quota_exhausted_until = time.time() + QUOTA_COOLDOWN_SECONDS
        self.active_image_model = ""
        logger.warning("[%s] 429 RATE LIMITED — recovery + cooldown %ds",
                       self.name, QUOTA_COOLDOWN_SECONDS)
        if _recovery_manager:
            _recovery_manager.trigger_recovery(self.name)

    def apply_model_override(self, body_json: dict):
        """If this instance has a fallback model active, apply it to the request."""
        if not self.active_image_model:
            return
        for req in body_json.get("requests", []):
            if isinstance(req, dict) and req.get("imageModelName"):
                req["imageModelName"] = self.active_image_model

    def mark_403(self):
        self.consecutive_403 += 1
        _track_account(self.name, "403")
        if self.consecutive_403 >= MAX_CONSECUTIVE_403:
            self.cooling_until = time.time() + COOLDOWN_SECONDS
            logger.warning("[%s] Marked as COOLING for %ds (consecutive 403: %d)",
                           self.name, COOLDOWN_SECONDS, self.consecutive_403)
            if _recovery_manager:
                _recovery_manager.trigger_recovery(self.name)

    def mark_success(self):
        self.consecutive_403 = 0
        self.total_completed += 1
        _track_account(self.name, "ok")
        if _recovery_manager:
            _recovery_manager.on_instance_success(self.name)

    def mark_failed(self):
        self.total_failed += 1
        _track_account(self.name, "fail")

    def status_dict(self) -> dict:
        return {
            "name": self.name,
            "api_port": self.api_port,
            "healthy": self.healthy,
            "extension_connected": self.extension_connected,
            "flow_key_present": self.flow_key_present,
            "available": self.available,
            "cooling": self.is_cooling,
            "cooling_remaining": max(0, int(self.cooling_until - time.time())) if self.is_cooling else 0,
            "quota_exhausted": self.is_quota_exhausted,
            "quota_remaining": max(0, int(self.quota_exhausted_until - time.time())) if self.is_quota_exhausted else 0,
            "consecutive_403": self.consecutive_403,
            "processing": self.processing_count,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
        }


# ─── Gateway State ───────────────────────────────────────────

instances: list[AgentInstance] = []
_round_robin_idx = 0
_recovery_manager: Optional[RecoveryManager] = None

# Task tracking (same as server/app.py format for VE3 compatibility)
tasks: dict[str, dict] = {}
stats = {
    "total_received": 0,
    "total_completed": 0,
    "total_failed": 0,
    "start_time": time.time(),
}
# Per-account stats: email → {ok, fail, errors_403}
_account_stats: dict[str, dict] = {}


def _get_instance_account(instance_name: str) -> str:
    """Get email of account currently assigned to instance."""
    if _recovery_manager:
        acc = _recovery_manager._accounts.get(instance_name, {})
        return acc.get("id", "")
    return ""


def _track_account(instance_name: str, result_type: str):
    """Track OK/fail/403 per account. result_type: 'ok', 'fail', '403'."""
    email = _get_instance_account(instance_name)
    if not email:
        return
    if email not in _account_stats:
        _account_stats[email] = {"ok": 0, "fail": 0, "errors_403": 0}
    if result_type == "ok":
        _account_stats[email]["ok"] += 1
    elif result_type == "fail":
        _account_stats[email]["fail"] += 1
    elif result_type == "403":
        _account_stats[email]["errors_403"] += 1


def _clear_cooldown(instance_name: str):
    """Callback from recovery_manager when recovery succeeds."""
    for inst in instances:
        if inst.name == instance_name:
            inst.cooling_until = 0
            inst.consecutive_403 = 0
            inst.quota_exhausted_until = 0
            logger.info("[Gateway] %s: cooldown + quota CLEARED by recovery", instance_name)
            break


def _pick_instance() -> Optional[AgentInstance]:
    """Pick next available instance (round-robin)."""
    global _round_robin_idx
    available = [i for i in instances if i.available]
    if not available:
        return None

    # Sort by processing count (prefer idle instances)
    available.sort(key=lambda i: (i.processing_count, i.last_request_time))
    chosen = available[0]
    chosen.last_request_time = time.time()
    return chosen


def _pick_instance_for_retry(exclude: list[str]) -> Optional[AgentInstance]:
    """Pick instance excluding already-tried ones."""
    available = [i for i in instances if i.available and i.name not in exclude]
    if not available:
        # If all excluded, try any that's not cooling
        available = [i for i in instances if i.enabled and i.healthy and not i.is_cooling
                     and i.name not in exclude]
    if not available:
        return None
    available.sort(key=lambda i: (i.processing_count, i.consecutive_403))
    return available[0]


# ─── Health Checker ──────────────────────────────────────────

async def health_check_loop():
    """Periodically check agent health + self-heal unhealthy instances."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            for inst in instances:
                if not inst.enabled:
                    continue
                try:
                    resp = await client.get(f"{inst.base_url}/health")
                    if resp.status_code == 200:
                        data = resp.json()
                        inst.healthy = True
                        inst.extension_connected = data.get("extension_connected", False)
                        inst.flow_key_present = data.get("flow_key_present", False)
                        agent_403 = data.get("consecutive_403", 0)
                        inst.consecutive_403 = max(inst.consecutive_403, agent_403)
                    else:
                        inst.healthy = False
                except Exception:
                    inst.healthy = False
                inst.last_health_check = time.time()

            # Self-heal: auto-restart instances that are down
            # Only for instances that WERE healthy before (not during initial startup)
            # Require 3 consecutive unhealthy checks (~30s) before triggering
            UNHEALTHY_THRESHOLD = 3
            if _recovery_manager:
                now = time.time()
                for inst in instances:
                    if not inst.enabled:
                        continue
                    # Instance is working fine — reset everything
                    if inst.healthy and inst.extension_connected and inst.flow_key_present:
                        inst.was_healthy_once = True
                        inst.consecutive_unhealthy = 0
                        if inst.self_heal_failures > 0:
                            inst.self_heal_failures = 0
                            inst.next_self_heal_at = 0
                        continue
                    # Skip instances that never finished startup
                    if not inst.was_healthy_once:
                        continue
                    # Count consecutive unhealthy — don't act on momentary blips
                    inst.consecutive_unhealthy += 1
                    if inst.consecutive_unhealthy < UNHEALTHY_THRESHOLD:
                        continue
                    # Skip if cooling (403 recovery handles that) or quota exhausted
                    if inst.is_cooling or inst.is_quota_exhausted:
                        continue
                    # Skip if already recovering
                    rec_state = _recovery_manager.states.get(inst.name)
                    if rec_state and rec_state.recovering:
                        continue
                    # Backoff check
                    if now < inst.next_self_heal_at:
                        continue
                    # Trigger self-heal — rotate IPv6+account from 2nd attempt
                    inst.self_heal_failures += 1
                    if inst.self_heal_failures > 5:
                        if inst.self_heal_failures == 6:
                            logger.warning(
                                "[SelfHeal] %s: 5 attempts failed — PAUSING 10min "
                                "(possible: no IPv6, all accounts blocked, or Chrome crash)",
                                inst.name)
                        inst.next_self_heal_at = now + 600
                        if inst.self_heal_failures > 8:
                            inst.self_heal_failures = 0
                        continue
                    rotate_ipv6 = inst.self_heal_failures >= 2
                    logger.warning(
                        "[SelfHeal] %s down for %d checks (healthy=%s, ext=%s, flowKey=%s), "
                        "attempt #%d%s",
                        inst.name, inst.consecutive_unhealthy,
                        inst.healthy, inst.extension_connected,
                        inst.flow_key_present, inst.self_heal_failures,
                        " + IPv6/account rotate" if rotate_ipv6 else "",
                    )
                    _recovery_manager.trigger_self_heal(inst.name, rotate_ipv6=rotate_ipv6)
                    delay = min(60 * inst.self_heal_failures, 300)
                    inst.next_self_heal_at = now + delay

            # Proxy health check: detect dead IPv6 (y het chrome_pool.py lines 1017-1025)
            if _recovery_manager:
                for inst in instances:
                    if not inst.enabled:
                        continue
                    cfg = CONFIG.get("instances", [])
                    inst_cfg = next((c for c in cfg if c.get("name") == inst.name), None)
                    if not inst_cfg or not inst_cfg.get("ipv6"):
                        continue
                    proxy_port = 1081 + (inst.api_port - 8100)
                    health_file = Path(__file__).parent / f".proxy_health_{proxy_port}"
                    try:
                        if health_file.exists():
                            cf = int(health_file.read_text().strip())
                            if cf >= 5:
                                rec_state = _recovery_manager.states.get(inst.name)
                                if rec_state and not rec_state.recovering:
                                    logger.warning(
                                        "[ProxyHealth] %s: proxy has %d connect failures → IPv6 DEAD → rotate + restart",
                                        inst.name, cf,
                                    )
                                    _recovery_manager.trigger_self_heal(inst.name, rotate_ipv6=True)
                    except Exception:
                        pass

            # Task watchdog: kill stuck tasks (y het chrome_pool.py lines 906-927)
            now = time.time()
            for task_id, task in list(tasks.items()):
                if task["status"] != "processing":
                    continue
                started_at = task.get("started_at", 0)
                if not started_at:
                    continue
                elapsed = now - started_at
                if elapsed > TASK_WATCHDOG_TIMEOUT:
                    worker_name = task.get("worker", "?")
                    task["status"] = "failed"
                    task["error"] = f"Task watchdog timeout after {int(elapsed)}s"
                    stats["total_failed"] += 1
                    logger.warning(
                        "[Watchdog] %s: task %s timeout (%.0fs > %ds) → mark failed, trigger self-heal",
                        worker_name, task_id[:8], elapsed, TASK_WATCHDOG_TIMEOUT,
                    )
                    for inst in instances:
                        if inst.name == worker_name:
                            inst.total_failed += 1
                            if _recovery_manager:
                                _recovery_manager.trigger_self_heal(inst.name)
                            break

            await asyncio.sleep(10)


# ─── Task Processing ────────────────────────────────────────

async def _process_image_task(task_id: str, data: dict):
    """Process image generation with retry + rotation."""
    tasks[task_id]["status"] = "processing"
    tasks[task_id]["started_at"] = time.time()

    bearer_token = data["bearer_token"]
    project_id = data["project_id"]
    body_json = data["body_json"]
    flow_url = data.get("flow_url", "")

    tried_instances: list[str] = []

    for attempt in range(MAX_RETRIES):
        # Pick instance — wait up to 120s for one to become available
        if attempt == 0:
            inst = _pick_instance()
        else:
            inst = _pick_instance_for_retry(tried_instances)

        if not inst:
            for wait_s in range(24):
                await asyncio.sleep(5)
                inst = _pick_instance() if attempt == 0 else _pick_instance_for_retry(tried_instances)
                if inst:
                    break
            if not inst:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = "No available FlowKit instance (waited 120s)"
                stats["total_failed"] += 1
                return

        tried_instances.append(inst.name)
        tasks[task_id]["worker"] = inst.name
        inst.processing_count += 1
        inst.apply_model_override(body_json)

        try:
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT + 10) as client:
                resp = await client.post(f"{inst.base_url}/api/generate-image", json={
                    "bearer_token": bearer_token,
                    "project_id": project_id,
                    "body_json": body_json,
                    "flow_url": flow_url,
                })
                result = resp.json()

            if result.get("success"):
                inst.mark_success()
                tasks[task_id]["status"] = "completed"
                tasks[task_id]["result"] = result.get("result")
                stats["total_completed"] += 1
                logger.info("[Gateway] Image %s DONE via %s", task_id[:8], inst.name)
                return

            # ─── ERROR HANDLING: 403 / 429 ─────────────────────────
            #
            # 403 = reCAPTCHA / account block / IP block
            #   → Rotate to next instance immediately
            #   → After MAX_CONSECUTIVE_403 (3): cooling + recovery (L1→L2→L3)
            #   → L1: clear cache/cookies + reload Flow
            #   → L2: rotate IPv6 + restart Chrome
            #   → L3: full restart + re-login
            #
            # 429 = rate limit (NOT permanent quota)
            #   → Step 1: Try fallback model (NARWHAL ↔ GEM_PIX_2)
            #   → Step 2: Retry 3x with 15s delay
            #   → Step 3: Recovery (same as 403: clear cache + rotate IPv6 + restart)
            #   → Step 4: Retry once after recovery
            #   → Step 5: Rotate to another instance
            #   → If ALL fail: task fails, VE3 retries later
            #
            # Update 2026-05-29: 429 triggers full recovery chain (L1→L2→L3)
            # vì 429 là rate limit tạm — clear cache + đổi IP + restart ~40s là hết
            # ──────────────────────────────────────────────────────
            error = result.get("error", "")
            status_code = result.get("status", 500)

            if status_code == 403 or "RECAPTCHA_403" in error or "CAPTCHA_FAILED" in error:
                inst.mark_403()
                logger.warning("[Gateway] Image %s: 403 from %s (attempt %d/%d), rotating...",
                               task_id[:8], inst.name, attempt + 1, MAX_RETRIES)
                continue  # Try next instance

            # 429 quota — try fallback model first, then retry, then mark exhausted
            if status_code == 429 or "QUOTA" in error.upper():
                # Step 1: Try fallback model (e.g. GEM_PIX_2 -> NARWHAL)
                import copy
                fallback_body = copy.deepcopy(body_json)
                new_model = _switch_image_model(fallback_body)
                if new_model:
                    logger.info("[Gateway] Image %s: 429 on %s, trying fallback model %s",
                                task_id[:8], inst.name, new_model)
                    try:
                        async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT + 10) as client_fb:
                            resp_fb = await client_fb.post(f"{inst.base_url}/api/generate-image", json={
                                "bearer_token": bearer_token,
                                "project_id": project_id,
                                "body_json": fallback_body,
                                "flow_url": flow_url,
                            })
                            r_fb = resp_fb.json()
                        if r_fb.get("success"):
                            inst.active_image_model = new_model
                            inst.mark_success()
                            tasks[task_id]["status"] = "completed"
                            tasks[task_id]["result"] = r_fb.get("result")
                            stats["total_completed"] += 1
                            logger.info("[Gateway] Image %s DONE via fallback model %s on %s (model persisted)",
                                        task_id[:8], new_model, inst.name)
                            return
                        fb_status = r_fb.get("status", 500)
                        fb_error = r_fb.get("error", "")
                        if fb_status != 429 and "QUOTA" not in fb_error.upper():
                            inst.mark_failed()
                            tasks[task_id]["status"] = "failed"
                            tasks[task_id]["error"] = fb_error
                            stats["total_failed"] += 1
                            return
                        logger.info("[Gateway] Image %s: fallback model %s also 429", task_id[:8], new_model)
                    except Exception:
                        pass

                # Step 2: Retry original model a few times
                quota_confirmed = True
                for q_retry in range(QUOTA_RETRY_COUNT):
                    logger.info("[Gateway] Image %s: 429 from %s, retry %d/%d in %ds...",
                                task_id[:8], inst.name, q_retry + 1, QUOTA_RETRY_COUNT, QUOTA_RETRY_DELAY)
                    await asyncio.sleep(QUOTA_RETRY_DELAY)
                    try:
                        async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT + 10) as client2:
                            resp2 = await client2.post(f"{inst.base_url}/api/generate-image", json={
                                "bearer_token": bearer_token,
                                "project_id": project_id,
                                "body_json": body_json,
                                "flow_url": flow_url,
                            })
                            r2 = resp2.json()
                        if r2.get("success"):
                            inst.mark_success()
                            tasks[task_id]["status"] = "completed"
                            tasks[task_id]["result"] = r2.get("result")
                            stats["total_completed"] += 1
                            logger.info("[Gateway] Image %s DONE after 429 retry via %s", task_id[:8], inst.name)
                            return
                        s2 = r2.get("status", 500)
                        if s2 != 429 and "QUOTA" not in r2.get("error", "").upper():
                            quota_confirmed = False
                            break
                    except Exception:
                        pass

                # Step 3: All retries failed → trigger recovery (clear cache + rotate IPv6)
                if quota_confirmed:
                    inst.mark_quota_exhausted()
                    logger.warning("[Gateway] Image %s: 429 persists on %s → recovery + retry after",
                                   task_id[:8], inst.name)

                    # Step 4: Wait for recovery to complete, then retry once
                    for _rw in range(12):  # 12 × 10s = 120s max
                        await asyncio.sleep(10)
                        if inst.available or (not inst.is_quota_exhausted and not inst.is_cooling):
                            break
                    if inst.available or inst.healthy:
                        logger.info("[Gateway] Image %s: post-recovery retry on %s", task_id[:8], inst.name)
                        try:
                            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT + 10) as client3:
                                resp3 = await client3.post(f"{inst.base_url}/api/generate-image", json={
                                    "bearer_token": bearer_token,
                                    "project_id": project_id,
                                    "body_json": body_json,
                                    "flow_url": flow_url,
                                })
                                r3 = resp3.json()
                            if r3.get("success"):
                                inst.mark_success()
                                tasks[task_id]["status"] = "completed"
                                tasks[task_id]["result"] = r3.get("result")
                                stats["total_completed"] += 1
                                logger.info("[Gateway] Image %s DONE after 429 recovery via %s",
                                            task_id[:8], inst.name)
                                return
                        except Exception:
                            pass

                    # Still failing after recovery → try another instance
                    logger.warning("[Gateway] Image %s: 429 still after recovery on %s, rotating...",
                                   task_id[:8], inst.name)
                    continue  # outer loop tries next instance

            # Non-403/429 failure → don't retry
            inst.mark_failed()
            detail = result.get("detail", {})
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = f"[{status_code}] {error}" if status_code in (401,) else error
            if detail:
                tasks[task_id]["detail"] = detail
            stats["total_failed"] += 1
            logger.warning("[Gateway] Image %s FAILED via %s: [%s] %s", task_id[:8], inst.name, status_code, error[:100])
            return

        except Exception as e:
            inst.mark_failed()
            logger.exception("[Gateway] Image %s exception via %s", task_id[:8], inst.name)
            continue
        finally:
            inst.processing_count -= 1

    # All retries exhausted
    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = "All instances failed (403 rotation exhausted)"
    stats["total_failed"] += 1


def _deep_find_video_url(obj):
    """Recursively find video URL in any JSON structure."""
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")) and any(
            h in obj for h in (".mp4", "video", "fife", "download", "lh3.", "ugc/")
        ):
            return obj
        return None
    if isinstance(obj, dict):
        for key in ("fifeUrl", "signedUrl", "downloadUrl", "videoUrl", "url", "uri"):
            val = obj.get(key)
            found = _deep_find_video_url(val)
            if found:
                return found
        for val in obj.values():
            found = _deep_find_video_url(val)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _deep_find_video_url(item)
            if found:
                return found
    return None


async def _process_video_task(task_id: str, data: dict):
    """Process video generation: submit + poll."""
    tasks[task_id]["status"] = "processing"
    tasks[task_id]["started_at"] = time.time()

    bearer_token = data["bearer_token"]
    body_json = data["body_json"]
    flow_url = data.get("flow_url", "")

    # Step 1: Submit — wait for available instance
    inst = _pick_instance()
    if not inst:
        for wait_s in range(24):
            await asyncio.sleep(5)
            inst = _pick_instance()
            if inst:
                break
        if not inst:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = "No available FlowKit instance (waited 120s)"
            stats["total_failed"] += 1
            return

    tasks[task_id]["worker"] = inst.name

    # Retry on 404 — Google eventual consistency: uploaded media may not
    # be queryable immediately after upload returns success.
    max_404_retries = 2
    retry_delay_404 = 5

    for attempt_404 in range(max_404_retries + 1):
        inst.processing_count += 1

        try:
            async with httpx.AsyncClient(timeout=VIDEO_SUBMIT_TIMEOUT + 10) as client:
                resp = await client.post(f"{inst.base_url}/api/generate-video", json={
                    "bearer_token": bearer_token,
                    "body_json": body_json,
                    "flow_url": flow_url,
                })
                result = resp.json()
        except Exception as e:
            inst.mark_failed()
            inst.processing_count -= 1
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
            stats["total_failed"] += 1
            return

        inst.processing_count -= 1

        if not result.get("success"):
            # ─── VIDEO ERROR HANDLING: 403 / 429 ─────────────────
            # Same strategy as image — see comments in _process_image_task
            # 403: mark_403 → cooling → recovery L1→L2→L3
            # 429: retry → recovery → retry → rotate to other instance
            # 404: retry (Google eventual consistency)
            # ──────────────────────────────────────────────────────
            error = result.get("error", "")
            detail = result.get("detail", "")
            status_code = result.get("status", 500)

            if status_code == 404 and attempt_404 < max_404_retries:
                logger.info("[Gateway] Video %s got 404 (attempt %d/%d), retrying in %ds...",
                            task_id[:8], attempt_404 + 1, max_404_retries + 1, retry_delay_404)
                await asyncio.sleep(retry_delay_404)
                continue

            if status_code == 403:
                inst.mark_403()
                # Try another instance (like image does)
                other = _pick_instance_for_retry([inst.name])
                if other:
                    logger.warning("[Gateway] Video %s: 403 from %s, trying %s",
                                   task_id[:8], inst.name, other.name)
                    other.processing_count += 1
                    try:
                        async with httpx.AsyncClient(timeout=VIDEO_SUBMIT_TIMEOUT + 10) as client_r:
                            resp_r = await client_r.post(f"{other.base_url}/api/generate-video", json={
                                "bearer_token": bearer_token,
                                "body_json": body_json,
                                "flow_url": flow_url,
                            })
                            r_r = resp_r.json()
                        other.processing_count -= 1
                        if r_r.get("success"):
                            result = r_r
                            other.mark_success()
                            tasks[task_id]["worker"] = other.name
                            break  # → polling
                    except Exception:
                        other.processing_count -= 1
                # 403 rotation failed → fail task (fall through to failure section)
            elif status_code == 429 or "QUOTA" in error.upper():
                quota_confirmed = True
                for q_retry in range(QUOTA_RETRY_COUNT):
                    logger.info("[Gateway] Video %s: 429 from %s, retry %d/%d in %ds...",
                                task_id[:8], inst.name, q_retry + 1, QUOTA_RETRY_COUNT, QUOTA_RETRY_DELAY)
                    await asyncio.sleep(QUOTA_RETRY_DELAY)
                    try:
                        inst.processing_count += 1
                        async with httpx.AsyncClient(timeout=VIDEO_SUBMIT_TIMEOUT + 10) as client2:
                            resp2 = await client2.post(f"{inst.base_url}/api/generate-video", json={
                                "bearer_token": bearer_token,
                                "body_json": body_json,
                                "flow_url": flow_url,
                            })
                            r2 = resp2.json()
                        inst.processing_count -= 1
                        if r2.get("success"):
                            result = r2
                            quota_confirmed = False
                            break
                        s2 = r2.get("status", 500)
                        if s2 != 429 and "QUOTA" not in r2.get("error", "").upper():
                            quota_confirmed = False
                            break
                    except Exception:
                        inst.processing_count -= 1
                if quota_confirmed:
                    inst.mark_quota_exhausted()
                    logger.warning("[Gateway] Video %s: 429 persists on %s → recovery + retry",
                                   task_id[:8], inst.name)

                    # Wait for recovery, then retry once
                    for _rw in range(12):
                        await asyncio.sleep(10)
                        if inst.available or (not inst.is_quota_exhausted and not inst.is_cooling):
                            break
                    if inst.available or inst.healthy:
                        logger.info("[Gateway] Video %s: post-recovery retry on %s", task_id[:8], inst.name)
                        try:
                            async with httpx.AsyncClient(timeout=VIDEO_SUBMIT_TIMEOUT + 10) as client3:
                                resp3 = await client3.post(f"{inst.base_url}/api/generate-video", json={
                                    "bearer_token": bearer_token,
                                    "body_json": body_json,
                                    "flow_url": flow_url,
                                })
                                r3 = resp3.json()
                            if r3.get("success"):
                                result = r3
                                inst.mark_success()
                                break
                        except Exception:
                            pass

                    # Try another instance after recovery
                    other = _pick_instance_for_retry([inst.name])
                    if other:
                        logger.info("[Gateway] Video %s: 429 recovery done, trying %s",
                                    task_id[:8], other.name)
                        other.processing_count += 1
                        try:
                            async with httpx.AsyncClient(timeout=VIDEO_SUBMIT_TIMEOUT + 10) as client4:
                                resp4 = await client4.post(f"{other.base_url}/api/generate-video", json={
                                    "bearer_token": bearer_token,
                                    "body_json": body_json,
                                    "flow_url": flow_url,
                                })
                                r4 = resp4.json()
                            other.processing_count -= 1
                            if r4.get("success"):
                                result = r4
                                other.mark_success()
                                tasks[task_id]["worker"] = other.name
                                break
                        except Exception:
                            other.processing_count -= 1

                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = f"[429_QUOTA] {error}"
                    stats["total_failed"] += 1
                    return
                if not quota_confirmed and result.get("success"):
                    inst.mark_success()
                    break
            else:
                inst.mark_failed()
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = f"[{status_code}] {error}" if status_code in (401,) else error
            if detail:
                tasks[task_id]["detail"] = detail
            stats["total_failed"] += 1
            logger.warning("[Gateway] Video %s FAILED: [%s] %s", task_id[:8], status_code, error[:100])
            return

        break

    # Step 2: Extract operations and poll
    response_data = result.get("result", {})
    operations = response_data.get("operations", [])
    logger.info("[Gateway] Video %s: ops=%d, workflows=%s",
                task_id[:8], len(operations),
                bool(response_data.get("workflows")) if isinstance(response_data, dict) else False)

    # Check if media already has video URL (fast completion)
    def _has_video_url(data):
        return bool(_deep_find_video_url(data))

    # Check for I2V workflow response (Google returns workflows instead of operations)
    workflows = response_data.get("workflows", [])
    primary_media_id = ""
    if workflows:
        for wf in workflows:
            pm = wf.get("metadata", {}).get("primaryMediaId", "")
            if pm:
                primary_media_id = pm
                break

    if not operations:
        # I2V workflow: poll by media ID (must check BEFORE media URL check,
        # because I2V responses include input image media with fifeUrl)
        if workflows and primary_media_id:
            logger.info("[Gateway] Video %s: I2V workflow, polling media %s",
                        task_id[:8], primary_media_id)
            await _poll_video_by_media_id(task_id, inst, bearer_token, primary_media_id)
            return

        media = response_data.get("media", [])
        if media and _has_video_url(response_data):
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = response_data
            stats["total_completed"] += 1
            inst.mark_success()
            return

        if not media and not workflows:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = "No operations/workflows in video response"
            stats["total_failed"] += 1
            return

    # Standard poll loop (T2V with operations)
    timeout_at = time.time() + VIDEO_POLL_TIMEOUT
    poll_inst = inst

    while time.time() < timeout_at:
        await asyncio.sleep(VIDEO_POLL_INTERVAL)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{poll_inst.base_url}/api/poll-video", json={
                    "bearer_token": bearer_token,
                    "operations": operations,
                })
                poll_result = resp.json()
        except Exception:
            continue

        if not poll_result.get("success"):
            continue

        poll_data = poll_result.get("result", {})
        ops = poll_data.get("operations", [])
        if not ops:
            continue

        op = ops[0]
        op_status = op.get("status", "")
        is_done = op.get("done", False)

        if "SUCCESSFUL" in op_status or "COMPLETED" in op_status or is_done:
            video_url = _deep_find_video_url(poll_data)
            if video_url:
                poll_data["_video_url"] = video_url
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = poll_data
            stats["total_completed"] += 1
            inst.mark_success()
            logger.info("[Gateway] Video %s DONE via %s", task_id[:8], inst.name)
            return
        elif "FAILED" in op_status or "CANCELLED" in op_status:
            error_msg = op.get("metadata", {}).get("error", {}).get("message", op_status)
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = error_msg
            stats["total_failed"] += 1
            inst.mark_failed()
            return

        operations = ops

    # Timeout
    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = f"Video generation timeout ({VIDEO_POLL_TIMEOUT}s)"
    stats["total_failed"] += 1


async def _poll_video_by_media_id(task_id: str, inst, bearer_token: str, media_id: str):
    """Poll I2V video by media ID — used when Google returns workflows instead of operations.

    Tries direct Google API call first (just a GET, no captcha needed).
    Falls back to extension if direct call fails to connect.
    """
    import base64 as _b64

    GOOGLE_API = "https://aisandbox-pa.googleapis.com"
    API_KEY = "AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY"

    start_time = time.time()
    timeout_at = start_time + VIDEO_POLL_TIMEOUT
    poll_count = 0
    direct_poll_failed = False

    while time.time() < timeout_at:
        await asyncio.sleep(VIDEO_POLL_INTERVAL)
        poll_count += 1

        media_data = None

        # Method 1: Direct Google API — skip if already failed (avoids log spam)
        if not direct_poll_failed:
            try:
                check_url = f"{GOOGLE_API}/v1/media/{media_id}?key={API_KEY}&clientContext.tool=PINHOLE"
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(check_url, headers={
                        "Authorization": f"Bearer {bearer_token}",
                    })
                    if resp.status_code == 200:
                        media_data = resp.json()
                    elif resp.status_code in (403, 401):
                        direct_poll_failed = True
                        logger.info("[Gateway] Video %s: direct poll %d, switching to extension poll",
                                    task_id[:8], resp.status_code)
                    elif poll_count <= 2:
                        logger.info("[Gateway] Video %s: direct poll HTTP %d", task_id[:8], resp.status_code)
            except Exception as e:
                if poll_count <= 2:
                    logger.info("[Gateway] Video %s: direct poll error: %s", task_id[:8], e)

        # Method 2: Via extension (uses extension's own flowKey)
        if media_data is None:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(f"{inst.base_url}/api/check-media", json={
                        "media_id": media_id,
                    })
                    poll_result = resp.json()
                    if poll_count <= 3:
                        logger.info("[Gateway] Video %s: extension poll success=%s status=%s",
                                    task_id[:8], poll_result.get("success"), poll_result.get("status"))
                    if poll_result.get("success"):
                        media_data = poll_result.get("result", {})
            except Exception as e:
                if poll_count <= 2:
                    logger.info("[Gateway] Video %s: extension poll error: %s", task_id[:8], e)

        if not isinstance(media_data, dict):
            if poll_count % 6 == 1:
                elapsed = int(time.time() - start_time)
                logger.info("[Gateway] Video %s: media poll #%d (%ds), waiting...",
                            task_id[:8], poll_count, elapsed)
            continue

        # Check for base64-encoded video (workflow mode response)
        video_block = media_data.get("video", {})
        encoded_video = video_block.get("encodedVideo", "") if isinstance(video_block, dict) else ""

        if encoded_video:
            try:
                binary = _b64.b64decode(encoded_video)
                # Validate MP4 magic: bytes 4-8 should be "ftyp"
                is_mp4 = len(binary) >= 12 and binary[4:8] == b"ftyp"
                if is_mp4:
                    # Save to temp file
                    out_dir = os.path.join(os.path.dirname(__file__), "_workflow_videos")
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"{media_id}.mp4")
                    with open(out_path, "wb") as f:
                        f.write(binary)
                    gw_port = CONFIG.get("gateway_port", 5100)
                    video_url = f"http://127.0.0.1:{gw_port}/api/fix/video-file/{media_id}"
                    result_data = {
                        "operations": [{
                            "done": True,
                            "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                            "operation": {
                                "metadata": {"video": {"fifeUrl": video_url, "mediaId": media_id}}
                            },
                        }],
                        "_video_url": video_url,
                        "_video_path": os.path.abspath(out_path),
                        "_video_size": len(binary),
                    }
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["result"] = result_data
                    stats["total_completed"] += 1
                    inst.mark_success()
                    elapsed = int(time.time() - start_time)
                    logger.info("[Gateway] Video %s DONE (I2V encoded, %d bytes, %ds) via %s",
                                task_id[:8], len(binary), elapsed, inst.name)
                    return
                else:
                    if poll_count % 6 == 1:
                        logger.debug("[Gateway] Video %s: got %d bytes but not MP4 yet", task_id[:8], len(binary))
            except Exception as e:
                logger.debug("[Gateway] Video %s: decode error: %s", task_id[:8], e)

        # Also check for fifeUrl (alternative response format)
        video_url = _deep_find_video_url(media_data)
        if video_url:
            result_data = {
                "operations": [{
                    "done": True,
                    "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                    "operation": {
                        "metadata": {"video": {"fifeUrl": video_url}}
                    },
                }],
                "_video_url": video_url,
            }
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = result_data
            stats["total_completed"] += 1
            inst.mark_success()
            elapsed = int(time.time() - start_time)
            logger.info("[Gateway] Video %s DONE (I2V URL, %ds) via %s", task_id[:8], elapsed, inst.name)
            return

        if poll_count % 6 == 1:
            elapsed = int(time.time() - start_time)
            logger.info("[Gateway] Video %s: media poll #%d (%ds), waiting...", task_id[:8], poll_count, elapsed)

    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = f"I2V video timeout ({VIDEO_POLL_TIMEOUT}s)"
    stats["total_failed"] += 1


# ─── FastAPI App ─────────────────────────────────────────────

app = FastAPI(title="FlowKit Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    global _recovery_manager

    # Initialize instances
    fa_enabled = CONFIG.get("fixed_account", {}).get("enabled", False)
    all_configs = CONFIG.get("instances", [])
    if fa_enabled:
        for cfg in all_configs:
            instances.append(AgentInstance(cfg))
        logger.info("Gateway: Fixed Account mode — %d instances, %d concurrent",
                    len(instances), CONFIG.get("fixed_account", {}).get("concurrent", 2))
    else:
        enabled_configs = [cfg for cfg in all_configs if cfg.get("enabled", True)]
        for cfg in enabled_configs:
            instances.append(AgentInstance(cfg))
    logger.info("Gateway initialized with %d instances", len(instances))

    # Initialize recovery manager
    _recovery_manager = RecoveryManager(
        config=CONFIG,
        instances_config=all_configs if fa_enabled else [cfg for cfg in all_configs if cfg.get("enabled", True)],
        on_cooldown_clear=_clear_cooldown,
    )
    # Load ALL accounts from GUI config (full list), then assign active from .flow_accounts
    import json as _json
    try:
        gui_file = Path(__file__).parent / "config" / "flowkit_gui.json"
        if gui_file.exists():
            gui_data = _json.loads(gui_file.read_text(encoding="utf-8"))
            raw = gui_data.get("accounts", "")
            if raw:
                pool = []
                for line in raw.strip().split("\n"):
                    parts = line.strip().split("|")
                    if len(parts) >= 2:
                        pool.append({"id": parts[0].strip(), "password": parts[1].strip(),
                                     "totp_secret": parts[2].strip() if len(parts) >= 3 else ""})
                if pool:
                    _recovery_manager.set_account_pool(pool)
    except Exception:
        pass
    try:
        accounts_file = Path(__file__).parent / "config" / ".flow_accounts.json"
        if accounts_file.exists():
            acc_data = _json.loads(accounts_file.read_text(encoding="utf-8"))
            for inst_name, acc in acc_data.items():
                _recovery_manager._accounts[inst_name] = acc
    except Exception:
        pass
    logger.info("Recovery manager initialized (IPv6: %s, accounts: %d)",
                "yes" if _recovery_manager._ipv6_client else "no",
                len(_recovery_manager._all_accounts))

    # Start health checker
    asyncio.create_task(health_check_loop())


# ─── VE3-Compatible API (same as server/app.py) ────���────────

@app.post("/api/fix/create-image-veo3")
async def create_image(request: Request):
    """Same API as server/app.py — VE3 worker sends here."""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "No JSON body"}

        body_json = data.get("body_json")
        flow_auth_token = data.get("flow_auth_token", "")
        flow_url = data.get("flow_url", "")
        vm_id = data.get("vm_id", "unknown")

        if not body_json:
            return {"success": False, "error": "Missing body_json"}
        if not flow_auth_token:
            return {"success": False, "error": "Missing flow_auth_token"}

        # Extract prompt
        prompt = ""
        if "requests" in body_json and body_json["requests"]:
            prompt = body_json["requests"][0].get("prompt", "")
        if not prompt:
            return {"success": False, "error": "No prompt"}

        # Extract project_id
        project_id = ""
        if body_json.get("clientContext", {}).get("projectId"):
            project_id = body_json["clientContext"]["projectId"]
        elif flow_url:
            parts = flow_url.split("/projects/")
            if len(parts) > 1:
                project_id = parts[1].split("/")[0]
        if not project_id:
            return {"success": False, "error": "No projectId found"}

        # Check if any instance is healthy (even if busy)
        healthy = [i for i in instances if i.enabled and i.healthy and i.extension_connected]
        available = [i for i in instances if i.available]
        if not healthy:
            cooling = [i for i in instances if i.is_cooling]
            if cooling:
                return {"success": False, "error": "All instances cooling (403 rate limit). Retry later."}
            return {"success": False, "error": "No healthy FlowKit instances"}

        queue_pos = sum(1 for t in tasks.values() if t["status"] in ("queued", "processing"))
        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "status": "queued",
            "type": "image",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "prompt": prompt,
            "project_id": project_id,
            "vm_id": vm_id,
            "worker": None,
        }
        stats["total_received"] += 1

        asyncio.create_task(_process_image_task(task_id, {
            "bearer_token": flow_auth_token,
            "project_id": project_id,
            "body_json": body_json,
            "flow_url": flow_url,
        }))

        logger.info("[Gateway] +Image %s | VM: %s | Avail: %d Healthy: %d Queue: %d | %s",
                    task_id[:8], vm_id, len(available), len(healthy), queue_pos, prompt[:50])

        return {"success": True, "taskId": task_id, "queue_position": queue_pos}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/fix/create-video-veo3")
async def create_video(request: Request):
    """Same API as server/app.py for video generation."""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "No JSON body"}

        body_json = data.get("body_json")
        flow_auth_token = data.get("flow_auth_token", "")
        flow_url = data.get("flow_url", "")
        vm_id = data.get("vm_id", "unknown")

        if not body_json:
            return {"success": False, "error": "Missing body_json"}
        if not flow_auth_token:
            return {"success": False, "error": "Missing flow_auth_token"}

        # Check if any instance is healthy (queue even if busy)
        healthy = [i for i in instances if i.enabled and i.healthy and i.extension_connected]
        if not healthy:
            cooling = [i for i in instances if i.is_cooling]
            if cooling:
                return {"success": False, "error": "All instances cooling (403 rate limit). Retry later."}
            return {"success": False, "error": "No healthy FlowKit instances"}

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "status": "queued",
            "type": "video",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "vm_id": vm_id,
            "worker": None,
        }
        stats["total_received"] += 1

        asyncio.create_task(_process_video_task(task_id, {
            "bearer_token": flow_auth_token,
            "body_json": body_json,
            "flow_url": flow_url,
        }))

        return {"success": True, "taskId": task_id, "queue_position": 0}

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/fix/task-status")
async def task_status(taskId: str = ""):
    if not taskId:
        return {"success": False, "error": "Missing taskId"}

    task = tasks.get(taskId)
    if not task:
        return {"success": False, "error": "Task not found"}

    if task["status"] == "completed":
        return {"success": True, "result": task["result"]}
    elif task["status"] == "failed":
        resp = {"success": False, "error": task.get("error", "Unknown")}
        if task.get("detail"):
            resp["detail"] = task["detail"]
        return resp
    else:
        return {"success": True, "status": task["status"], "worker": task.get("worker")}


@app.get("/api/fix/video-file/{media_id}")
async def serve_video_file(media_id: str):
    """Serve a saved workflow video file."""
    from fastapi.responses import JSONResponse
    video_dir = os.path.join(os.path.dirname(__file__), "_workflow_videos")
    video_path = os.path.join(video_dir, f"{media_id}.mp4")
    if not os.path.isfile(video_path):
        return JSONResponse({"success": False, "error": "Video file not found"}, status_code=404)
    return FileResponse(video_path, media_type="video/mp4", filename=f"{media_id}.mp4")


@app.post("/api/fix/reset-captcha")
async def reset_captcha():
    """VE3-compatible: trigger 403 recovery for all cooling instances.

    VE3 worker calls this when it detects repeated reCAPTCHA failures.
    Gateway delegates to recovery_manager which handles autonomously.
    """
    if not _recovery_manager:
        return {"success": False, "error": "Recovery manager not initialized"}

    triggered = []
    for inst in instances:
        if inst.is_cooling or inst.consecutive_403 > 0:
            _recovery_manager.trigger_recovery(inst.name)
            triggered.append(inst.name)

    if triggered:
        logger.info("[Gateway] VE3 reset-captcha: triggered recovery for %s", triggered)
        return {"success": True, "message": f"Recovery triggered for {triggered}"}
    return {"success": True, "message": "No instances need recovery"}


@app.get("/health")
async def health():
    """Agent-compatible health endpoint for VE3 polling."""
    total_available = sum(1 for i in instances if i.available)
    total_connected = sum(1 for i in instances if i.extension_connected)
    flow_key = any(i.flow_key_present for i in instances)
    return {
        "status": "ok",
        "extension_connected": total_connected > 0,
        "flow_key_present": flow_key,
        "flowKeyPresent": flow_key,
        "instances_available": total_available,
    }


@app.post("/api/fix/upload-image")
async def upload_image(request: Request):
    """Upload reference image via first available agent."""
    data = await request.json()
    bearer_token = data.get("flow_auth_token", "")
    image_b64 = data.get("image_base64", "")
    project_id = data.get("project_id", "")

    inst = _pick_instance()
    if not inst:
        return {"success": False, "error": "No available instance"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{inst.base_url}/api/upload-image", json={
                "bearer_token": bearer_token,
                "image_base64": image_b64,
                "mime_type": data.get("mime_type", "image/png"),
                "project_id": project_id,
            })
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── Status & Health ─────────────────────────────��───────────

@app.get("/api/status")
async def server_status():
    """VE3-compatible status endpoint."""
    total_available = sum(1 for i in instances if i.available)
    total_connected = sum(1 for i in instances if i.extension_connected)
    recovering_count = 0
    if _recovery_manager:
        recovering_count = sum(1 for s in _recovery_manager.states.values() if s.recovering)
    return {
        "server_state": "idle" if total_available > 0 else "busy",
        "chrome_ready": total_connected > 0,
        "accepting_tasks": total_available > 0,
        "engine": "flowkit-gateway",
        "instances_available": total_available,
        "instances_total": len(instances),
        "instances_cooling": sum(1 for i in instances if i.is_cooling),
        "instances_recovering": recovering_count,
        "queue_size": sum(1 for t in tasks.values() if t["status"] == "queued"),
        "processing_count": sum(1 for t in tasks.values() if t["status"] == "processing"),
        "total_completed": stats["total_completed"],
        "total_failed": stats["total_failed"],
        "uptime": int(time.time() - stats["start_time"]),
    }


@app.get("/api/ping")
async def ping():
    return {
        "status": "alive",
        "server_state": "idle",
        "engine": "flowkit-gateway",
        "instances_available": sum(1 for i in instances if i.available),
    }


@app.get("/api/instances")
async def list_instances():
    """Detailed status of all instances."""
    return {"instances": [i.status_dict() for i in instances]}


@app.get("/api/accounts")
async def list_accounts():
    """Account pool stats for GUI display."""
    if not _recovery_manager:
        return {"accounts": []}
    result = []
    for acc in _recovery_manager._all_accounts:
        email = acc.get("id", "")
        usage = _recovery_manager._account_usage.get(email, 0)
        assigned_to = ""
        for inst_name, inst_acc in _recovery_manager._accounts.items():
            if inst_acc.get("id") == email:
                assigned_to = inst_name
                break
        acc_st = _account_stats.get(email, {})
        result.append({
            "email": email,
            "usage_count": usage,
            "assigned_to": assigned_to,
            "status": "active" if assigned_to else "pool",
            "ok": acc_st.get("ok", 0),
            "fail": acc_st.get("fail", 0),
            "errors_403": acc_st.get("errors_403", 0),
        })
    return {"accounts": result}


@app.post("/api/reset-instance/{name}")
async def reset_instance(name: str):
    """Manually reset cooling state for an instance."""
    for inst in instances:
        if inst.name == name:
            inst.cooling_until = 0
            inst.consecutive_403 = 0
            if _recovery_manager:
                state = _recovery_manager.states.get(name)
                if state:
                    state.reset()
            return {"success": True, "message": f"{name} reset"}
    return {"success": False, "error": f"Instance {name} not found"}


@app.get("/api/recovery-status")
async def recovery_status():
    """Get recovery manager status for all instances."""
    if not _recovery_manager:
        return {"success": False, "error": "Recovery manager not initialized"}
    return {
        "success": True,
        "recovery": _recovery_manager.get_status(),
    }


@app.post("/api/trigger-recovery/{name}")
async def trigger_recovery(name: str):
    """Manually trigger recovery for an instance."""
    if not _recovery_manager:
        return {"success": False, "error": "Recovery manager not initialized"}
    if name not in _recovery_manager.states:
        return {"success": False, "error": f"Instance {name} not found"}
    _recovery_manager.trigger_recovery(name)
    return {"success": True, "message": f"Recovery triggered for {name}"}


# ─── Cleanup ─────────────────────────────────────────────────

async def cleanup_loop():
    """Periodically clean old tasks to prevent memory leak."""
    while True:
        await asyncio.sleep(300)  # every 5 min
        cutoff = time.time() - 600  # 10 min old (was 1 hour — too long for 24/7)
        stale = [tid for tid, t in tasks.items()
                 if t.get("created_at", 0) < cutoff and t["status"] in ("completed", "failed")]
        # Also clean tasks stuck in processing/queued for > 30 min (watchdog missed)
        stuck_cutoff = time.time() - 1800
        stuck = [tid for tid, t in tasks.items()
                 if t.get("created_at", 0) < stuck_cutoff and t["status"] in ("processing", "queued")]
        for tid in stuck:
            tasks[tid]["status"] = "failed"
            tasks[tid]["error"] = "Cleanup: stuck task (>30min)"
        for tid in stale:
            del tasks[tid]
        if stale or stuck:
            logger.info("Cleanup: %d stale removed, %d stuck marked failed", len(stale), len(stuck))


@app.on_event("startup")
async def start_cleanup():
    asyncio.create_task(cleanup_loop())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("gateway:app", host=GATEWAY_HOST, port=GATEWAY_PORT)
