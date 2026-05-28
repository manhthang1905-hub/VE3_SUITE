"""
FlowKit Agent — per-Chrome-instance server.

Each Chrome copy has one agent: FastAPI (API port) + WebSocket (extension port).
The Gateway communicates with agents via their API ports.
"""
import asyncio
import json
import logging
import os
import secrets
import time

import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agent.services.flow_client import FlowClient, GOOGLE_FLOW_API, GOOGLE_API_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8100"))
WS_HOST = os.environ.get("WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("WS_PORT", "9222"))
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", f"flowkit-{API_PORT - 8099}")

_client = FlowClient(instance_name=INSTANCE_NAME)
_CALLBACK_SECRET = secrets.token_urlsafe(32)

# Track processing state
_processing_count = 0
_total_completed = 0
_total_failed = 0
_consecutive_403 = 0
_last_403_time: float = 0


def get_client() -> FlowClient:
    return _client


# ─── WebSocket Server for Extension ─────────────────────────

async def ws_handler(websocket):
    _client.set_extension(websocket)
    await websocket.send(json.dumps({"type": "callback_secret", "secret": _CALLBACK_SECRET}))

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                await _client.handle_message(data)
            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.exception("Error handling extension message: %s", e)
    except websockets.ConnectionClosed:
        pass
    finally:
        _client.clear_extension()


async def run_ws_server():
    max_retries = 12
    for attempt in range(max_retries):
        try:
            server = await websockets.serve(
                ws_handler, WS_HOST, WS_PORT,
                ping_interval=None,
                ping_timeout=None,
            )
            logger.info("[%s] WS server on ws://%s:%d", INSTANCE_NAME, WS_HOST, WS_PORT)
            await asyncio.Future()
        except OSError as e:
            if attempt < max_retries - 1 and e.errno in (10048, 98):
                logger.warning("WS port %d in use, retry %d/%d...", WS_PORT, attempt + 1, max_retries)
                await asyncio.sleep(5)
            else:
                raise


# ─── FastAPI App ─────────────────────────────────────────────

app = FastAPI(title=f"FlowKit Agent ({INSTANCE_NAME})", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    asyncio.create_task(run_ws_server())
    logger.info("[%s] Agent starting on %s:%d", INSTANCE_NAME, API_HOST, API_PORT)


# ─── Extension Callback ─────────────────────────────────────

@app.post("/api/ext/callback")
async def ext_callback(request: Request):
    data = await request.json()
    req_id = data.get("id")
    if req_id and req_id in _client._pending:
        future = _client._pending[req_id]
        try:
            future.set_result(data)
        except asyncio.InvalidStateError:
            pass
        return {"ok": True}
    return {"ok": False, "reason": "no matching pending request"}


# ─── Health ──────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "instance": INSTANCE_NAME,
        "extension_connected": _client.connected,
        "flow_key_present": _client.flow_key_present,
        "processing": _processing_count,
        "total_completed": _total_completed,
        "total_failed": _total_failed,
        "consecutive_403": _consecutive_403,
    }


# ─── Image Generation (called by Gateway) ───────────────────

@app.post("/api/generate-image")
async def generate_image(request: Request):
    """Generate image using external bearer_token from main machine.

    Gateway sends:
    {
        "bearer_token": "ya29.xxx",    # Main machine's token
        "project_id": "uuid",
        "body_json": {...},            # Original body from VE3
        "flow_url": "https://..."      # Optional
    }
    """
    global _processing_count, _total_completed, _total_failed, _consecutive_403, _last_403_time

    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    data = await request.json()
    bearer_token = data.get("bearer_token", "")
    project_id = data.get("project_id", "")
    body_json = data.get("body_json", {})
    flow_url = data.get("flow_url", "")

    if not bearer_token:
        return {"success": False, "error": "MISSING_BEARER_TOKEN"}

    if not flow_url:
        flow_url = f"{GOOGLE_FLOW_API}/v1/projects/{project_id}/flowMedia:batchGenerateImages"

    # Ensure recaptchaContext exists for captcha injection
    _ensure_recaptcha_context(body_json)

    _processing_count += 1
    try:
        result = await _client.send("api_request", {
            "url": flow_url,
            "method": "POST",
            "headers": _random_headers(),
            "body": body_json,
            "captchaAction": "IMAGE_GENERATION",
            "bearerToken": bearer_token,  # KEY: pass external token to extension
        }, timeout=120)

        if result.get("error"):
            _total_failed += 1
            error_msg = result["error"]
            if "CAPTCHA_FAILED" in str(error_msg) or result.get("status") == 403:
                _consecutive_403 += 1
                _last_403_time = time.time()
            return {"success": False, "error": error_msg, "status": result.get("status", 500)}

        status = result.get("status", 200)
        if isinstance(status, int) and status == 403:
            _consecutive_403 += 1
            _last_403_time = time.time()
            _total_failed += 1
            return {"success": False, "error": "RECAPTCHA_403", "status": 403}

        if isinstance(status, int) and status >= 400:
            _total_failed += 1
            response_data = result.get("data", {})
            err_msg = ""
            if isinstance(response_data, dict):
                err_msg = response_data.get("error", {}).get("message", f"HTTP {status}")
            return {"success": False, "error": err_msg or f"HTTP {status}", "status": status}

        # Success
        _consecutive_403 = 0
        _total_completed += 1
        return {"success": True, "result": result.get("data", result), "status": status}

    except Exception as e:
        _total_failed += 1
        return {"success": False, "error": str(e), "status": 500}
    finally:
        _processing_count -= 1


# ─── Video Generation (called by Gateway) ───────────────────

@app.post("/api/generate-video")
async def generate_video(request: Request):
    """Submit video generation. Returns operations for polling."""
    global _processing_count, _total_completed, _total_failed, _consecutive_403, _last_403_time

    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    data = await request.json()
    bearer_token = data.get("bearer_token", "")
    body_json = data.get("body_json", {})
    flow_url = data.get("flow_url", "")

    if not bearer_token:
        return {"success": False, "error": "MISSING_BEARER_TOKEN"}
    if not flow_url:
        flow_url = f"{GOOGLE_FLOW_API}/v1/video:batchAsyncGenerateVideoReferenceImages"

    _ensure_recaptcha_context(body_json)

    _processing_count += 1
    try:
        result = await _client.send("api_request", {
            "url": flow_url,
            "method": "POST",
            "headers": _random_headers(),
            "body": body_json,
            "captchaAction": "VIDEO_GENERATION",
            "bearerToken": bearer_token,
        }, timeout=60)

        if result.get("error"):
            _total_failed += 1
            if "CAPTCHA_FAILED" in str(result["error"]) or result.get("status") == 403:
                _consecutive_403 += 1
                _last_403_time = time.time()
            return {"success": False, "error": result["error"], "status": result.get("status", 500)}

        status = result.get("status", 200)
        if isinstance(status, int) and status == 403:
            _consecutive_403 += 1
            _last_403_time = time.time()
            _total_failed += 1
            return {"success": False, "error": "RECAPTCHA_403", "status": 403}

        if isinstance(status, int) and status >= 400:
            _total_failed += 1
            response_data = result.get("data", {})
            err_msg = ""
            if isinstance(response_data, dict):
                err_msg = response_data.get("error", {}).get("message", f"HTTP {status}")
            return {"success": False, "error": err_msg or f"HTTP {status}", "status": status, "detail": response_data}

        _consecutive_403 = 0
        _total_completed += 1
        return {"success": True, "result": result.get("data", result), "status": status}

    except Exception as e:
        _total_failed += 1
        return {"success": False, "error": str(e), "status": 500}
    finally:
        _processing_count -= 1


# ─── Video Poll (called by Gateway) ─────────────────────────

@app.post("/api/poll-video")
async def poll_video(request: Request):
    """Poll video generation status."""
    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    data = await request.json()
    bearer_token = data.get("bearer_token", "")
    operations = data.get("operations", [])

    if not bearer_token or not operations:
        return {"success": False, "error": "MISSING_PARAMS"}

    check_url = f"{GOOGLE_FLOW_API}/v1/video:batchCheckAsyncVideoGenerationStatus"

    result = await _client.send("api_request", {
        "url": check_url,
        "method": "POST",
        "headers": _random_headers(),
        "body": {"operations": operations},
        "bearerToken": bearer_token,
    }, timeout=30)

    if result.get("error"):
        return {"success": False, "error": result["error"]}

    return {"success": True, "result": result.get("data", result)}


# ─── Check Media Status (called by Gateway for I2V workflows) ─

@app.post("/api/check-media")
async def check_media(request: Request):
    """Check media status by ID — used when I2V returns workflows instead of operations.

    Uses API key auth (not bearer token) to match Google Flow SDK pattern.
    Response when video ready: {name, video: {encodedVideo: "<base64 MP4>"}}
    """
    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    data = await request.json()
    media_id = data.get("media_id", "")

    if not media_id:
        return {"success": False, "error": "MISSING_PARAMS"}

    check_url = f"{GOOGLE_FLOW_API}/v1/media/{media_id}?key={GOOGLE_API_KEY}&clientContext.tool=PINHOLE"

    result = await _client.send("api_request", {
        "url": check_url,
        "method": "GET",
        "headers": _random_headers(),
    }, timeout=30)

    status = result.get("status", 500)
    if result.get("error"):
        return {"success": False, "error": result["error"], "status": status}

    return {"success": True, "result": result.get("data", result), "status": status}


# ─── Upload Image (called by Gateway) ───────────────────────

@app.post("/api/upload-image")
async def upload_image(request: Request):
    """Upload reference image."""
    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    data = await request.json()
    bearer_token = data.get("bearer_token", "")
    image_b64 = data.get("image_base64", "")
    mime_type = data.get("mime_type", "image/png")
    project_id = data.get("project_id", "")

    if not bearer_token or not image_b64:
        return {"success": False, "error": "MISSING_PARAMS"}

    upload_url = f"{GOOGLE_FLOW_API}/v1/flow/uploadImage"
    body = {
        "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
        "fileName": "image.jpg",
        "imageBytes": image_b64,
        "isHidden": False,
        "isUserUploaded": True,
        "mimeType": mime_type,
    }

    result = await _client.send("api_request", {
        "url": upload_url,
        "method": "POST",
        "headers": _random_headers(),
        "body": body,
        "bearerToken": bearer_token,
    }, timeout=60)

    if result.get("error"):
        return {"success": False, "error": result["error"]}

    response_data = result.get("data", {})
    media_name = ""
    if isinstance(response_data, dict):
        media = response_data.get("media", {})
        if isinstance(media, dict):
            media_name = media.get("name", "")

    return {"success": True, "result": response_data, "media_name": media_name}


# ─── Reset Captcha ───────────────────────────────────────────

@app.post("/api/reset-captcha")
async def reset_captcha():
    global _consecutive_403
    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    result = await _client.reset_captcha()
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    _consecutive_403 = 0
    return {"success": True, "needs_relogin": True}


# ─── Ensure Project ─────────────────────────────────────────

@app.post("/api/ensure-project")
async def ensure_project():
    if not _client.connected:
        return {"success": False, "error": "EXTENSION_NOT_CONNECTED"}

    result = await _client.ensure_project()
    if result.get("error"):
        return {"success": False, "error": result["error"]}

    return {"success": True, "data": result.get("data", {})}


# ─── Helpers ─────────────────────────────────────────────────

import random

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
]


def _random_headers() -> dict:
    return {
        "content-type": "application/json",
        "user-agent": random.choice(_USER_AGENTS),
        "x-goog-api-client": "genai-js/0.29.0",
    }


def _ensure_recaptcha_context(body: dict):
    placeholder = {
        "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
        "token": "",
    }
    if "clientContext" in body:
        if "recaptchaContext" not in body["clientContext"]:
            body["clientContext"]["recaptchaContext"] = {**placeholder}
    for req in body.get("requests", []):
        if isinstance(req, dict):
            ctx = req.get("clientContext")
            if isinstance(ctx, dict) and "recaptchaContext" not in ctx:
                ctx["recaptchaContext"] = {**placeholder}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent.main:app", host=API_HOST, port=API_PORT)
