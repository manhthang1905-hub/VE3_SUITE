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

TIMEOUTS = CONFIG.get("timeouts", {})
IMAGE_TIMEOUT = TIMEOUTS.get("image_generation", 120)
VIDEO_SUBMIT_TIMEOUT = TIMEOUTS.get("video_submit", 60)
VIDEO_POLL_TIMEOUT = TIMEOUTS.get("video_poll", 420)
VIDEO_POLL_INTERVAL = TIMEOUTS.get("video_poll_interval", 10)


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
        self.processing_count = 0
        self.total_completed = 0
        self.total_failed = 0
        self.last_request_time: float = 0
        self.last_health_check: float = 0

    @property
    def available(self) -> bool:
        """Can this instance accept new work?"""
        if not self.enabled or not self.healthy:
            return False
        if not self.extension_connected:
            return False
        if self.cooling_until > time.time():
            return False
        if self.processing_count >= MAX_CONCURRENT:
            return False
        return True

    @property
    def is_cooling(self) -> bool:
        return self.cooling_until > time.time()

    def mark_403(self):
        self.consecutive_403 += 1
        if self.consecutive_403 >= MAX_CONSECUTIVE_403:
            self.cooling_until = time.time() + COOLDOWN_SECONDS
            logger.warning("[%s] Marked as COOLING for %ds (consecutive 403: %d)",
                           self.name, COOLDOWN_SECONDS, self.consecutive_403)

    def mark_success(self):
        self.consecutive_403 = 0
        self.total_completed += 1

    def mark_failed(self):
        self.total_failed += 1

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
            "consecutive_403": self.consecutive_403,
            "processing": self.processing_count,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
        }


# ─── Gateway State ───────────────────────────────────────────

instances: list[AgentInstance] = []
_round_robin_idx = 0

# Task tracking (same as server/app.py format for VE3 compatibility)
tasks: dict[str, dict] = {}
stats = {
    "total_received": 0,
    "total_completed": 0,
    "total_failed": 0,
    "start_time": time.time(),
}


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
    """Periodically check agent health."""
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
                        # Sync 403 count from agent
                        agent_403 = data.get("consecutive_403", 0)
                        if agent_403 > inst.consecutive_403:
                            inst.consecutive_403 = agent_403
                    else:
                        inst.healthy = False
                except Exception:
                    inst.healthy = False
                inst.last_health_check = time.time()
            await asyncio.sleep(10)


# ─── Task Processing ────────────────────────────────────────

async def _process_image_task(task_id: str, data: dict):
    """Process image generation with retry + rotation."""
    tasks[task_id]["status"] = "processing"

    bearer_token = data["bearer_token"]
    project_id = data["project_id"]
    body_json = data["body_json"]
    flow_url = data.get("flow_url", "")

    tried_instances: list[str] = []

    for attempt in range(MAX_RETRIES):
        # Pick instance
        if attempt == 0:
            inst = _pick_instance()
        else:
            inst = _pick_instance_for_retry(tried_instances)

        if not inst:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = "No available FlowKit instance"
            stats["total_failed"] += 1
            return

        tried_instances.append(inst.name)
        tasks[task_id]["worker"] = inst.name
        inst.processing_count += 1

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

            # Check if 403 → rotate
            error = result.get("error", "")
            status_code = result.get("status", 500)

            if status_code == 403 or "RECAPTCHA_403" in error or "CAPTCHA_FAILED" in error:
                inst.mark_403()
                logger.warning("[Gateway] Image %s: 403 from %s (attempt %d/%d), rotating...",
                               task_id[:8], inst.name, attempt + 1, MAX_RETRIES)
                continue  # Try next instance

            # Non-403 failure → don't retry
            inst.mark_failed()
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = error
            stats["total_failed"] += 1
            logger.warning("[Gateway] Image %s FAILED via %s: %s", task_id[:8], inst.name, error[:100])
            return

        except Exception as e:
            inst.mark_failed()
            logger.exception("[Gateway] Image %s exception via %s", task_id[:8], inst.name)
            # Continue to retry with another instance
            continue
        finally:
            inst.processing_count -= 1

    # All retries exhausted
    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = "All instances failed (403 rotation exhausted)"
    stats["total_failed"] += 1


async def _process_video_task(task_id: str, data: dict):
    """Process video generation: submit + poll."""
    tasks[task_id]["status"] = "processing"

    bearer_token = data["bearer_token"]
    body_json = data["body_json"]
    flow_url = data.get("flow_url", "")

    # Step 1: Submit
    inst = _pick_instance()
    if not inst:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = "No available FlowKit instance"
        stats["total_failed"] += 1
        return

    tasks[task_id]["worker"] = inst.name
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
        error = result.get("error", "")
        detail = result.get("detail", "")
        if result.get("status") == 403:
            inst.mark_403()
        else:
            inst.mark_failed()
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = error
        if detail:
            tasks[task_id]["detail"] = detail
        stats["total_failed"] += 1
        logger.warning("[Gateway] Video %s FAILED: %s (status=%s)", task_id[:8], error, result.get("status"))
        return

    # Step 2: Extract operations and poll
    response_data = result.get("result", {})
    operations = response_data.get("operations", [])

    if not operations:
        media = response_data.get("media", [])
        workflows = response_data.get("workflows", [])
        if media or workflows:
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = response_data
            stats["total_completed"] += 1
            inst.mark_success()
            return
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = "No operations in video response"
        stats["total_failed"] += 1
        return

    # Poll loop
    timeout_at = time.time() + VIDEO_POLL_TIMEOUT
    poll_inst = inst  # Use same instance for polling (no captcha needed)

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

        if op_status in ("SUCCESSFUL", "COMPLETED"):
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["result"] = poll_data
            stats["total_completed"] += 1
            inst.mark_success()
            logger.info("[Gateway] Video %s DONE via %s", task_id[:8], inst.name)
            return
        elif op_status in ("FAILED", "CANCELLED"):
            error_msg = op.get("metadata", {}).get("error", {}).get("message", op_status)
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = error_msg
            stats["total_failed"] += 1
            inst.mark_failed()
            return

        operations = ops  # Update with latest operation state

    # Timeout
    tasks[task_id]["status"] = "failed"
    tasks[task_id]["error"] = f"Video generation timeout ({VIDEO_POLL_TIMEOUT}s)"
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
    # Initialize instances
    for cfg in CONFIG.get("instances", []):
        if cfg.get("enabled", True):
            instances.append(AgentInstance(cfg))
    logger.info("Gateway initialized with %d instances", len(instances))

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

        # Check availability
        available = [i for i in instances if i.available]
        if not available:
            cooling = [i for i in instances if i.is_cooling]
            if cooling:
                return {"success": False, "error": "All instances cooling (403 rate limit). Retry later."}
            return {"success": False, "error": "No healthy FlowKit instances"}

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

        logger.info("[Gateway] +Image %s | VM: %s | Avail: %d | %s",
                    task_id[:8], vm_id, len(available), prompt[:50])

        return {"success": True, "taskId": task_id, "queue_position": 0}

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

        # Check availability
        available = [i for i in instances if i.available]
        if not available:
            return {"success": False, "error": "No available FlowKit instances"}

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
        return {"success": False, "error": task.get("error", "Unknown")}
    else:
        return {"success": True, "status": task["status"], "worker": task.get("worker")}


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
    return {
        "server_state": "idle" if total_available > 0 else "busy",
        "chrome_ready": total_connected > 0,
        "accepting_tasks": total_available > 0,
        "engine": "flowkit-gateway",
        "instances_available": total_available,
        "instances_total": len(instances),
        "instances_cooling": sum(1 for i in instances if i.is_cooling),
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


@app.post("/api/reset-instance/{name}")
async def reset_instance(name: str):
    """Manually reset cooling state for an instance."""
    for inst in instances:
        if inst.name == name:
            inst.cooling_until = 0
            inst.consecutive_403 = 0
            return {"success": True, "message": f"{name} reset"}
    return {"success": False, "error": f"Instance {name} not found"}


# ─── Cleanup ─────────────────────────────────────────────────

async def cleanup_loop():
    """Periodically clean old tasks."""
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 7200  # 2 hours
        stale = [tid for tid, t in tasks.items()
                 if t.get("created_at", 0) < cutoff and t["status"] in ("completed", "failed")]
        for tid in stale:
            del tasks[tid]
        if stale:
            logger.info("Cleaned %d stale tasks", len(stale))


@app.on_event("startup")
async def start_cleanup():
    asyncio.create_task(cleanup_loop())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("gateway:app", host=GATEWAY_HOST, port=GATEWAY_PORT)
