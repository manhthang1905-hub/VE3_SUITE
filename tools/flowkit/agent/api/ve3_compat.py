"""
VE3 Compatibility Adapter — exposes VE3 server API format on FlowKit.

Key fix: VE3 sends fake project_id. Adapter auto-creates a real Flow project
and replaces project_id in URL + body before forwarding to extension.
"""
import asyncio
import logging
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Request

from agent.services.flow_client import FlowClient
from agent.services.headers import random_headers
from agent.config import GOOGLE_FLOW_API, GOOGLE_API_KEY

logger = logging.getLogger(__name__)

router = APIRouter()

_client: Optional[FlowClient] = None
_tasks: dict[str, dict] = {}
_semaphore: Optional[asyncio.Semaphore] = None
_last_api_call: float = 0
_COOLDOWN = 8
_MAX_CONCURRENT = 5
_processing_count = 0
_real_project_id: Optional[str] = None


def init_ve3_compat(flow_client: FlowClient):
    global _client, _semaphore
    _client = flow_client
    _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    logger.info("VE3 compatibility adapter initialized")


# ── Helpers ──────────────────────────────────────────────────

async def _rate_limit():
    global _last_api_call
    now = time.time()
    wait = _COOLDOWN - (now - _last_api_call)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_api_call = time.time()


async def _ensure_project() -> str:
    """Get or create a real Flow project for API calls.

    Strategy: extract from browser tab URL first (most reliable),
    fall back to tRPC create if no project is open.
    """
    global _real_project_id
    if _real_project_id:
        return _real_project_id

    # Strategy 1: Extract from open Flow tab URL (most reliable)
    logger.info("[VE3] Extracting project_id from browser Flow tab...")
    extract_result = await _client.extract_project_id()
    if not extract_result.get("error"):
        data = extract_result.get("data", {})
        pid = data.get("projectId") if isinstance(data, dict) else None
        if pid:
            _real_project_id = pid
            logger.info("[VE3] Project ID extracted from browser: %s", pid[:12])
            return _real_project_id

    logger.info("[VE3] Browser extraction failed (%s), creating project via tRPC...",
                extract_result.get("error", "unknown"))

    # Strategy 2: Create via tRPC
    result = await _client.create_project("VE3-FlowKit-Bridge")
    if not result.get("error"):
        data = result.get("data", result)
        pid = None
        if isinstance(data, dict):
            # tRPC response: {result: {data: {json: {projectId: "..."}}}}
            json_data = data.get("result", {}).get("data", {}).get("json", {})
            pid = json_data.get("projectId") or json_data.get("id")
            if not pid:
                pid = data.get("projectId") or data.get("id")

        if pid:
            _real_project_id = pid
            logger.info("[VE3] Flow project created via tRPC: %s", pid[:12])
            return _real_project_id

    logger.warning("[VE3] All project_id strategies failed, will use from request body if available")
    return ""


def _fix_url(flow_url: str, real_pid: str) -> str:
    """Replace fake project_id in URL with real one."""
    return re.sub(
        r"/v1/projects/[^/]+/",
        f"/v1/projects/{real_pid}/",
        flow_url,
    )


_RECAPTCHA_PLACEHOLDER = {
    "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
    "token": "",
}

def _fix_body(body: dict, real_pid: str) -> dict:
    """Replace project_id and ensure recaptchaContext exists for captcha injection."""
    if "clientContext" in body:
        body["clientContext"]["projectId"] = real_pid
        if "recaptchaContext" not in body["clientContext"]:
            body["clientContext"]["recaptchaContext"] = {**_RECAPTCHA_PLACEHOLDER}
    for req in body.get("requests", []):
        if isinstance(req, dict):
            ctx = req.get("clientContext")
            if isinstance(ctx, dict):
                ctx["projectId"] = real_pid
                if "recaptchaContext" not in ctx:
                    ctx["recaptchaContext"] = {**_RECAPTCHA_PLACEHOLDER}
    return body


def _queue_position(task_id: str) -> int:
    return sum(1 for t in _tasks.values() if t["status"] == "queued")


def _check_response_error(result: dict) -> Optional[str]:
    """Check if extension response contains a Google API error."""
    status = result.get("status", 200)
    if isinstance(status, int) and status >= 400:
        data = result.get("data", {})
        if isinstance(data, dict):
            err = data.get("error", {})
            if isinstance(err, dict):
                return err.get("message", f"HTTP {status}")
            return str(data)[:200]
        return f"HTTP {status}"
    return None


def _is_quota_error(result: dict) -> bool:
    """Check if response is 429 quota exhausted."""
    status = result.get("status", 200)
    if status == 429:
        return True
    data = result.get("data", {})
    if isinstance(data, dict):
        err = data.get("error", {})
        if isinstance(err, dict):
            return "quota" in err.get("message", "").lower() or err.get("code") == 429
    return False


_MODEL_FALLBACK = {
    "GEM_PIX_2": "NARWHAL",
    "NARWHAL": "GEM_PIX_2",
}


def _switch_model_in_body(body: dict) -> bool:
    """Switch image model in body to fallback. Returns True if switched."""
    switched = False
    for req in body.get("requests", []):
        if isinstance(req, dict):
            model = req.get("imageModelName", "")
            if model in _MODEL_FALLBACK:
                req["imageModelName"] = _MODEL_FALLBACK[model]
                switched = True
    return switched


# ── Image Generation ────────────────────────────────────────

async def _process_image_task(task_id: str, data: dict):
    global _processing_count
    _tasks[task_id]["status"] = "processing"
    _processing_count += 1

    try:
        body_json = data.get("body_json", {})
        flow_url = data.get("flow_url", "")

        real_pid = await _ensure_project()
        if not real_pid:
            # Fallback: use project_id from request body
            real_pid = body_json.get("clientContext", {}).get("projectId", "")
            if real_pid and real_pid != "auto" and real_pid != "flowkit-managed":
                logger.info("[VE3] Using project_id from request body: %s", real_pid[:12])
            else:
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = "No project_id available. Open a Flow project in Chrome."
                return

        if not flow_url:
            flow_url = f"{GOOGLE_FLOW_API}/v1/projects/{real_pid}/flowMedia:batchGenerateImages?key={GOOGLE_API_KEY}"
        else:
            flow_url = _fix_url(flow_url, real_pid)
            if "key=" not in flow_url:
                flow_url += f"&key={GOOGLE_API_KEY}" if "?" in flow_url else f"?key={GOOGLE_API_KEY}"

        _fix_body(body_json, real_pid)

        async with _semaphore:
            await _rate_limit()
            logger.info("[VE3] Image task %s: sending to extension (project=%s)", task_id[:8], real_pid[:8])

            result = await _client._send("api_request", {
                "url": flow_url,
                "method": "POST",
                "headers": random_headers(),
                "body": body_json,
                "captchaAction": "IMAGE_GENERATION",
            }, timeout=120)

        if result.get("error"):
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = result["error"]
            logger.warning("[VE3] Image task %s FAILED (ext): %s", task_id[:8], result["error"])
        elif _is_quota_error(result):
            # 429 quota exhausted → retry with fallback model
            if _switch_model_in_body(body_json):
                new_model = body_json.get("requests", [{}])[0].get("imageModelName", "?")
                logger.info("[VE3] Image task %s: 429 quota, switching to model %s", task_id[:8], new_model)
                async with _semaphore:
                    await _rate_limit()
                    result2 = await _client._send("api_request", {
                        "url": flow_url,
                        "method": "POST",
                        "headers": random_headers(),
                        "body": body_json,
                        "captchaAction": "IMAGE_GENERATION",
                    }, timeout=120)
                if result2.get("error"):
                    _tasks[task_id]["status"] = "failed"
                    _tasks[task_id]["error"] = result2["error"]
                elif _check_response_error(result2):
                    _tasks[task_id]["status"] = "failed"
                    _tasks[task_id]["error"] = f"Google API error: {_check_response_error(result2)}"
                else:
                    _tasks[task_id]["status"] = "completed"
                    _tasks[task_id]["result"] = result2.get("data", result2)
                    logger.info("[VE3] Image task %s COMPLETED (fallback model)", task_id[:8])
            else:
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = "Google API error: quota exhausted, no fallback model"
        else:
            api_error = _check_response_error(result)
            if api_error:
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = f"Google API error: {api_error}"
                logger.warning("[VE3] Image task %s FAILED (API): %s", task_id[:8], api_error)
            else:
                response_data = result.get("data", result)
                _tasks[task_id]["status"] = "completed"
                _tasks[task_id]["result"] = response_data
                logger.info("[VE3] Image task %s COMPLETED", task_id[:8])

    except Exception as e:
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(e)
        logger.exception("[VE3] Image task %s exception", task_id[:8])
    finally:
        _processing_count -= 1


@router.post("/api/fix/create-image-veo3")
async def create_image(request: Request):
    if not _client or not _client.connected:
        return {"success": False, "error": "Extension not connected"}

    data = await request.json()
    task_id = str(uuid.uuid4())
    job_id = data.get("job_id", task_id)

    _tasks[task_id] = {
        "status": "queued", "type": "image",
        "result": None, "error": None,
        "created_at": time.time(), "job_id": job_id,
    }
    asyncio.create_task(_process_image_task(task_id, data))
    return {"success": True, "taskId": task_id, "jobId": job_id,
            "queue_position": _queue_position(task_id)}


# ── Video Generation ────────────────────────────────────────

async def _process_video_task(task_id: str, data: dict):
    global _processing_count
    _tasks[task_id]["status"] = "processing"
    _processing_count += 1

    try:
        body_json = data.get("body_json", {})
        flow_url = data.get("flow_url", "")

        real_pid = await _ensure_project()
        if not real_pid:
            real_pid = body_json.get("clientContext", {}).get("projectId", "")
            if real_pid and real_pid != "auto" and real_pid != "flowkit-managed":
                logger.info("[VE3] Video: using project_id from request body: %s", real_pid[:12])
            else:
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = "No project_id available. Open a Flow project in Chrome."
                return

        if not flow_url:
            flow_url = f"{GOOGLE_FLOW_API}/v1/video:batchAsyncGenerateVideoReferenceImages?key={GOOGLE_API_KEY}"
        else:
            flow_url = _fix_url(flow_url, real_pid)
            if "key=" not in flow_url:
                flow_url += f"&key={GOOGLE_API_KEY}" if "?" in flow_url else f"?key={GOOGLE_API_KEY}"

        _fix_body(body_json, real_pid)

        # Step 1: Submit
        async with _semaphore:
            await _rate_limit()
            logger.info("[VE3] Video task %s: submitting", task_id[:8])
            result = await _client._send("api_request", {
                "url": flow_url,
                "method": "POST",
                "headers": random_headers(),
                "body": body_json,
                "captchaAction": "VIDEO_GENERATION",
            }, timeout=60)

        if result.get("error"):
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = result["error"]
            return

        api_error = _check_response_error(result)
        if api_error:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = f"Google API error: {api_error}"
            return

        response_data = result.get("data", result)
        operations = response_data.get("operations", [])

        if not operations:
            media = response_data.get("media", [])
            workflows = response_data.get("workflows", [])
            if media or workflows:
                _tasks[task_id]["status"] = "completed"
                _tasks[task_id]["result"] = response_data
                return
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["error"] = "No operations in video response"
            return

        # Step 2: Poll operations
        check_url = f"{GOOGLE_FLOW_API}/v1/video:batchCheckAsyncVideoGenerationStatus?key={GOOGLE_API_KEY}"
        poll_body = {"operations": operations}
        timeout_at = time.time() + 420

        while time.time() < timeout_at:
            await asyncio.sleep(10)
            poll_result = await _client._send("api_request", {
                "url": check_url,
                "method": "POST",
                "headers": random_headers(),
                "body": poll_body,
            }, timeout=30)

            if poll_result.get("error"):
                continue

            poll_data = poll_result.get("data", poll_result)
            ops = poll_data.get("operations", [])
            if not ops:
                continue

            op = ops[0]
            op_status = op.get("status", "")

            if op_status in ("SUCCESSFUL", "COMPLETED"):
                _tasks[task_id]["status"] = "completed"
                _tasks[task_id]["result"] = poll_data
                logger.info("[VE3] Video task %s COMPLETED", task_id[:8])
                return
            elif op_status in ("FAILED", "CANCELLED"):
                error_msg = op.get("metadata", {}).get("error", {}).get("message", op_status)
                _tasks[task_id]["status"] = "failed"
                _tasks[task_id]["error"] = error_msg
                return

            poll_body = {"operations": ops}

        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = "Video generation timeout (420s)"

    except Exception as e:
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(e)
    finally:
        _processing_count -= 1


@router.post("/api/fix/create-video-veo3")
async def create_video(request: Request):
    if not _client or not _client.connected:
        return {"success": False, "error": "Extension not connected"}

    data = await request.json()
    task_id = str(uuid.uuid4())
    job_id = data.get("job_id", task_id)

    _tasks[task_id] = {
        "status": "queued", "type": "video",
        "result": None, "error": None,
        "created_at": time.time(), "job_id": job_id,
    }
    asyncio.create_task(_process_video_task(task_id, data))
    return {"success": True, "taskId": task_id, "jobId": job_id,
            "queue_position": _queue_position(task_id)}


# ── Task Status ──────────────────────────────────────────────

@router.get("/api/fix/task-status")
async def task_status(taskId: str = "", jobId: str = ""):
    task = _tasks.get(taskId)
    if not task:
        return {"success": False, "error": f"Task {taskId} not found"}

    if task["status"] == "completed":
        return {"success": True, "status": "completed", "result": task["result"]}
    elif task["status"] == "failed":
        return {"success": False, "status": "failed", "error": task.get("error", "Unknown")}
    else:
        return {"success": True, "status": task["status"],
                "queue_position": _queue_position(taskId)}


# ── Health ───────────────────────────────────────────────────

@router.get("/api/status")
async def server_status():
    connected = _client.connected if _client else False
    return {
        "server_state": "idle",
        "chrome_ready": connected,
        "accepting_tasks": True,
        "extension_connected": connected,
        "queue_size": sum(1 for t in _tasks.values() if t["status"] == "queued"),
        "processing_count": _processing_count,
        "total_completed": sum(1 for t in _tasks.values() if t["status"] == "completed"),
        "total_failed": sum(1 for t in _tasks.values() if t["status"] == "failed"),
        "engine": "flowkit",
    }


@router.post("/api/fix/reset-captcha")
async def reset_captcha():
    """Clear Flow site data + refresh page to reset reCAPTCHA."""
    if not _client or not _client.connected:
        return {"success": False, "error": "Extension not connected"}

    global _real_project_id
    result = await _client.reset_captcha()
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    _real_project_id = None
    logger.info("[VE3] reCAPTCHA reset triggered, project_id cleared for re-extraction")
    return {"success": True, "needsRelogin": result.get("data", {}).get("needsRelogin", False)}


@router.get("/api/ping")
async def ping():
    connected = _client.connected if _client else False
    return {
        "server_state": "idle",
        "extension_connected": connected,
        "snapshot_age": 0, "engine": "flowkit",
    }


# ── Image Upload ─────────────────────────────────────────────

@router.post("/api/fix/upload-image")
async def upload_image(request: Request):
    if not _client or not _client.connected:
        return {"success": False, "error": "Extension not connected"}

    data = await request.json()
    image_b64 = data.get("image_base64", "")
    mime_type = data.get("mime_type", "image/png")
    project_id = data.get("project_id", "")

    body_json = data.get("body_json", {})
    if not image_b64 and body_json:
        img_input = body_json.get("imageInput", {})
        image_b64 = img_input.get("rawImageBytes", "")
        mime_type = img_input.get("mimeType", "image/png")

    if not image_b64:
        return {"success": False, "error": "No image data"}

    async with _semaphore:
        await _rate_limit()
        result = await _client.upload_image(
            image_base64=image_b64, mime_type=mime_type, project_id=project_id,
        )

    if result.get("error"):
        return {"success": False, "error": result["error"]}

    return {"success": True, "result": result.get("data", result),
            "media_name": result.get("_mediaId")}


# ── Cleanup ──────────────────────────────────────────────────

async def cleanup_stale_tasks():
    cutoff = time.time() - 3600
    stale = [tid for tid, t in _tasks.items()
             if t["created_at"] < cutoff and t["status"] in ("completed", "failed")]
    for tid in stale:
        del _tasks[tid]
