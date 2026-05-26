"""
Flow Client — communicates with Chrome extension via WebSocket.

Stripped-down server version: no DB, no tier sync.
Key difference from tools/flowkit version: supports external bearer_token passthrough.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

GOOGLE_FLOW_API = "https://aisandbox-pa.googleapis.com"
GOOGLE_API_KEY = "AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY"


class FlowClient:
    """Sends commands to Chrome extension via WebSocket."""

    def __init__(self, instance_name: str = ""):
        self._extension_ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._flow_key: Optional[str] = None
        self.instance_name = instance_name
        self._ws_connect_count = 0
        self._ws_connected_at: Optional[float] = None

    def set_extension(self, ws):
        self._extension_ws = ws
        self._ws_connect_count += 1
        self._ws_connected_at = time.time()
        logger.info("[%s] Extension connected", self.instance_name)

    def clear_extension(self):
        self._extension_ws = None
        pending_copy = list(self._pending.items())
        for req_id, future in pending_copy:
            if not future.done():
                future.set_exception(ConnectionError("Extension disconnected"))
        self._pending.clear()
        logger.warning("[%s] Extension disconnected, cleared %d pending", self.instance_name, len(pending_copy))

    def set_flow_key(self, key: str):
        self._flow_key = key

    @property
    def connected(self) -> bool:
        return self._extension_ws is not None

    @property
    def flow_key_present(self) -> bool:
        return bool(self._flow_key)

    async def handle_message(self, data: dict):
        if data.get("type") == "token_captured":
            self._flow_key = data.get("flowKey")
            logger.info("[%s] Flow key captured from extension", self.instance_name)
            return

        if data.get("type") == "extension_ready":
            logger.info("[%s] Extension ready, flowKey=%s", self.instance_name,
                        "yes" if data.get("flowKeyPresent") else "no")
            return

        if data.get("type") in ("pong", "ping", "media_urls_refresh"):
            return

        req_id = data.get("id")
        if req_id and req_id in self._pending:
            if not self._pending[req_id].done():
                self._pending[req_id].set_result(data)
            return

    async def send(self, method: str, params: dict, timeout: float = 300) -> dict:
        """Send request to extension and wait for response."""
        if not self._extension_ws:
            return {"error": "Extension not connected"}

        req_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._extension_ws.send(json.dumps({
                "id": req_id,
                "method": method,
                "params": params,
            }))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return {"error": f"Timeout ({timeout}s) waiting for {method}"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            self._pending.pop(req_id, None)

    async def reset_captcha(self) -> dict:
        return await self.send("reset_captcha", {}, timeout=30)

    async def extract_project_id(self) -> dict:
        return await self.send("extract_project_id", {}, timeout=15)

    def health_dict(self) -> dict:
        uptime = None
        if self._ws_connected_at and self.connected:
            uptime = int(time.time() - self._ws_connected_at)
        return {
            "instance": self.instance_name,
            "connected": self.connected,
            "flow_key_present": self.flow_key_present,
            "connects": self._ws_connect_count,
            "uptime_s": uptime,
        }
