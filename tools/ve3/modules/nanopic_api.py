"""NanoPic V2 API client for VE3 image/video generation."""

import base64
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests


@dataclass
class NanoPicResult:
    media_id: str = ""
    media_url: str = ""
    encoded_image: str = ""
    task_id: str = ""


class NanoPicAPI:
    def __init__(
        self,
        nano_token: str,
        access_token: str,
        video_cookie: str = "",
        base_url: str = "https://flow-api.nanoai.pics/api/v2",
        timeout: int = 120,
        poll_interval: float = 5,
        poll_timeout: float = 900,
        log_func: Optional[Callable[[str, str], None]] = None,
        use_flow_proxy: bool = False,
        flow_auth_token: str = "",
        flow_base_url: str = "https://aisandbox-pa.googleapis.com",
        flow_project_id: str = "",
    ):
        self.nano_token = str(nano_token or "").strip()
        self.access_token = str(access_token or "").strip()
        self.video_cookie = str(video_cookie or "").strip()
        self.base_url = str(base_url or "https://flow-api.nanoai.pics/api/v2").strip().rstrip("/")
        self.timeout = int(timeout or 120)
        self.poll_interval = max(1.0, float(poll_interval or 5))
        self.poll_timeout = max(30.0, float(poll_timeout or 900))
        self.log = log_func or (lambda msg, level="INFO": None)
        self.use_flow_proxy = bool(use_flow_proxy)
        self.flow_auth_token = str(flow_auth_token or "").strip()
        self.flow_base_url = str(flow_base_url or "https://aisandbox-pa.googleapis.com").strip().rstrip("/")
        self.flow_project_id = str(flow_project_id or "").strip()

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.nano_token}",
            "Content-Type": "application/json",
        }

    def validate_for_image(self) -> Optional[str]:
        if not self.nano_token:
            return "Missing NanoPic token"
        if self.use_flow_proxy:
            if not self.flow_auth_token:
                return "Missing Flow auth token for NanoPic Flow proxy mode"
            if not self.flow_project_id:
                return "Missing Flow project id for NanoPic Flow proxy mode"
            return None
        if not self.access_token:
            return "Missing NanoPic accessToken"
        return None

    def validate_for_video(self) -> Optional[str]:
        err = self.validate_for_image()
        if err:
            return err
        if self.use_flow_proxy:
            return None
        if not self.video_cookie:
            return "Missing NanoPic video cookie"
        return None

    @staticmethod
    def file_to_data_uri(path: Path) -> str:
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{data}"

    def create_image_and_download(
        self,
        prompt: str,
        output_path: Path,
        aspect_ratio: str,
        image_model: str = "NARWHAL",
        image_urls: Optional[List[str]] = None,
        image_inputs: Optional[List[Dict[str, Any]]] = None,
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> tuple[bool, NanoPicResult, str]:
        err = self.validate_for_image()
        if err:
            return False, NanoPicResult(), err

        if self.use_flow_proxy:
            return self._create_image_flow_proxy(prompt, output_path, aspect_ratio, image_model, image_urls, image_inputs, poll_callback)

        body = {
            "accessToken": self.access_token,
            "promptText": prompt,
            "imageUrls": image_urls or [],
            "aspectRatio": aspect_ratio,
            "imageModel": image_model or "NARWHAL",
        }
        ok, task_id, error = self._post_task("/images/create", body)
        if not ok:
            return False, NanoPicResult(), error
        result, error = self._poll_task(task_id, poll_callback=poll_callback)
        if not result:
            return False, NanoPicResult(task_id=task_id), error
        result.task_id = task_id
        if not self._save_result(result, output_path):
            return False, result, "NanoPic task succeeded but media download failed"
        return True, result, ""

    def create_video_and_download(
        self,
        prompt: str,
        output_path: Path,
        aspect_ratio: str,
        video_model: str = "VEO_3_FAST_LOWER",
        video_type: str = "frame",
        image_urls: Optional[List[str]] = None,
        start_image_media_id: str = "",
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> tuple[bool, NanoPicResult, str]:
        err = self.validate_for_video()
        if err:
            return False, NanoPicResult(), err

        if self.use_flow_proxy:
            return self._create_video_flow_proxy(prompt, output_path, aspect_ratio, video_model, image_urls, start_image_media_id, poll_callback)

        body = {
            "accessToken": self.access_token,
            "cookie": self.video_cookie,
            "promptText": prompt,
            "imageUrls": image_urls or [],
            "aspectRatio": aspect_ratio,
            "videoModel": video_model or "VEO_3_FAST_LOWER",
            "type": video_type or "frame",
        }
        ok, task_id, error = self._post_task("/videos/create", body)
        if not ok:
            return False, NanoPicResult(), error
        result, error = self._poll_task(task_id, poll_callback=poll_callback)
        if not result:
            return False, NanoPicResult(task_id=task_id), error
        result.task_id = task_id
        if not self._save_result(result, output_path):
            return False, result, "NanoPic task succeeded but media download failed"
        return True, result, ""

    def _post_task(self, endpoint: str, body: Dict[str, Any]) -> tuple[bool, str, str]:
        try:
            # Flow proxy endpoints use base URL without /v2
            if endpoint.startswith('/fix/'):
                url = self.base_url.replace('/api/v2', '/api') + endpoint
            else:
                url = f"{self.base_url}{endpoint}"
            response = requests.post(url, headers=self.headers, json=body, timeout=self.timeout)
            if response.status_code != 200:
                return False, "", self._response_error(response)
            data = response.json()
            if not data.get("success") or not data.get("taskId"):
                return False, "", data.get("message") or data.get("error") or data.get("code") or str(data)[:500]
            task_id = str(data.get("taskId") or "")
            self.log(f"NanoPic task created: {task_id}")
            return True, task_id, ""
        except Exception as exc:
            return False, "", f"NanoPic request error: {type(exc).__name__}: {exc}"

    def _poll_task(
        self,
        task_id: str,
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> tuple[Optional[NanoPicResult], str]:
        deadline = time.time() + self.poll_timeout
        last_message = ""
        while time.time() < deadline:
            try:
                response = requests.get(f"{self.base_url}/task", headers={"Authorization": f"Bearer {self.nano_token}"}, params={"taskId": task_id}, timeout=self.timeout)
                if response.status_code != 200:
                    return None, self._response_error(response)
                data = response.json()
                code = str(data.get("code") or "").lower()
                if poll_callback:
                    poll_callback({"status": code or data.get("message", ""), "task_id": task_id})
                if data.get("success") is True and code == "success":
                    return self._parse_result(data), ""
                if code == "processing" or data.get("message") == "Task is being processed":
                    last_message = data.get("message") or code
                    time.sleep(self.poll_interval)
                    continue
                return None, data.get("message") or data.get("code") or str(data)[:500]
            except Exception as exc:
                return None, f"NanoPic poll error: {type(exc).__name__}: {exc}"
        return None, f"NanoPic task timeout after {int(self.poll_timeout)}s: {last_message or task_id}"

    def _parse_result(self, data: Dict[str, Any]) -> NanoPicResult:
        payload = data.get("data") or data.get("result") or {}
        if not isinstance(payload, dict):
            payload = {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        return NanoPicResult(
            media_id=str(payload.get("mediaId") or payload.get("media_id") or result.get("mediaId") or ""),
            media_url=str(payload.get("mediaUrl") or payload.get("media_url") or result.get("mediaUrl") or ""),
            encoded_image=str(payload.get("encodedImage") or payload.get("base64") or result.get("encodedImage") or ""),
        )

    def _save_result(self, result: NanoPicResult, output_path: Path) -> bool:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if result.media_url:
                response = requests.get(result.media_url, timeout=max(self.timeout, 120), stream=True)
                if response.status_code != 200:
                    self.log(f"NanoPic download failed: HTTP {response.status_code}", "WARN")
                    return False
                with output_path.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
            if result.encoded_image:
                b64_data = result.encoded_image
                if "," in b64_data:
                    b64_data = b64_data.split(",", 1)[1]
                output_path.write_bytes(base64.b64decode(b64_data.strip().replace("\n", "").replace("\r", "")))
                return True
            return False
        except Exception as exc:
            self.log(f"NanoPic save error: {type(exc).__name__}: {exc}", "WARN")
            return False

    @staticmethod
    def _response_error(response: requests.Response) -> str:
        try:
            data = response.json()
            msg = data.get("message") or data.get("code") or str(data)
        except Exception:
            msg = response.text[:500]
        return f"HTTP {response.status_code}: {msg}"

    def _create_image_flow_proxy(
        self,
        prompt: str,
        output_path: Path,
        aspect_ratio: str,
        image_model: str,
        image_urls: Optional[List[str]] = None,
        image_inputs: Optional[List[Dict[str, Any]]] = None,
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> tuple[bool, NanoPicResult, str]:
        """Create image via NanoPic Chrome-compatible proxy mode (/api/fix/create-image-veo3)."""
        flow_url = f"{self.flow_base_url}/v1/projects/{self.flow_project_id}/flowMedia:batchGenerateImages"
        session_id = f";{int(time.time() * 1000)}"
        body_json = {
            "clientContext": {
                "sessionId": session_id,
                "projectId": self.flow_project_id,
                "tool": "PINHOLE",
            },
            "requests": [{
                "clientContext": {
                    "sessionId": session_id,
                    "projectId": self.flow_project_id,
                    "tool": "PINHOLE",
                },
                "seed": int(time.time()) % 1000000,
                "imageModelName": image_model or "GEM_PIX_2",
                "imageAspectRatio": aspect_ratio,
                "prompt": prompt,
                "imageInputs": []
            }]
        }
        flow_image_inputs = list(image_inputs or [])
        if image_urls:
            for url in image_urls:
                if isinstance(url, str) and url.startswith("data:") and "," in url:
                    meta, b64 = url.split(",", 1)
                    mime = "image/png"
                    if meta.startswith("data:") and ";" in meta:
                        mime = meta[5:].split(";", 1)[0] or mime
                    flow_image_inputs.append({
                        "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
                        "rawImageBytes": b64,
                        "mimeType": mime,
                    })
        if flow_image_inputs:
            body_json["requests"][0]["imageInputs"] = flow_image_inputs

        proxy_body = {
            "flow_url": flow_url,
            "flow_auth_token": self.flow_auth_token,
            "body_json": body_json,
            "is_proxy": False
        }
        ok, task_id, error = self._post_task("/fix/create-image-veo3", proxy_body)
        if not ok:
            return False, NanoPicResult(), error
        result, error = self._poll_task_flow(task_id, poll_callback=poll_callback, expect_video=False)
        if not result:
            return False, NanoPicResult(task_id=task_id), error
        result.task_id = task_id
        if not self._save_result(result, output_path):
            return False, result, "NanoPic Flow proxy task succeeded but media download failed"
        return True, result, ""

    def _resolve_i2v_model(self, video_model: str, aspect_ratio: str) -> str:
        return str(video_model or "").strip()

    def _create_video_flow_proxy(
        self,
        prompt: str,
        output_path: Path,
        aspect_ratio: str,
        video_model: str,
        image_urls: Optional[List[str]] = None,
        start_image_media_id: str = "",
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> tuple[bool, NanoPicResult, str]:
        """Create video via NanoPic Chrome-compatible proxy mode (/api/fix/create-video-veo3)."""

        # Determine if this is Image-to-Video or Text-to-Video
        media_id = start_image_media_id or ""
        if not media_id and image_urls:
            first_image = image_urls[0]
            if isinstance(first_image, str) and not first_image.startswith(("data:", "http://", "https://")):
                media_id = first_image

        is_i2v = bool(media_id)

        # Choose correct endpoint based on mode
        if is_i2v:
            flow_url = f"{self.flow_base_url}/v1/video:batchAsyncGenerateVideoReferenceImages"
        else:
            flow_url = f"{self.flow_base_url}/v1/video:batchAsyncGenerateVideoText"

        video_model_key = self._resolve_i2v_model(video_model, aspect_ratio) if is_i2v else (video_model or "veo_3_1_t2v_fast_ultra")

        request_data = {
            "aspectRatio": aspect_ratio,
            "seed": int(time.time()) % 100000,
            "textInput": {"prompt": prompt},
            "metadata": {"sceneId": str(output_path.stem)},
        }
        if video_model_key:
            request_data["videoModelKey"] = video_model_key

        # Add referenceImages for Image-to-Video mode
        if is_i2v:
            request_data["referenceImages"] = [{
                "imageUsageType": "IMAGE_USAGE_TYPE_ASSET",
                "mediaId": media_id,
            }]
            self.log(f"Image-to-Video mode with mediaId: {media_id[:50]}...")
        else:
            self.log(f"Text-to-Video mode")

        body_json = {
            "clientContext": {
                "sessionId": f";{int(time.time() * 1000)}",
                "projectId": self.flow_project_id,
                "tool": "PINHOLE",
                "userPaygateTier": "PAYGATE_TIER_TWO"
            },
            "requests": [request_data]
        }

        proxy_body = {
            "flow_url": flow_url,
            "flow_auth_token": self.flow_auth_token,
            "body_json": body_json,
            "is_proxy": False,
            "proxy": ""
        }
        ok, task_id, error = self._post_task("/fix/create-video-veo3", proxy_body)
        if not ok:
            return False, NanoPicResult(), error
        result, error = self._poll_task_flow(task_id, poll_callback=poll_callback, expect_video=True)
        if not result:
            return False, NanoPicResult(task_id=task_id), error
        result.task_id = task_id
        if not self._save_result(result, output_path):
            return False, result, "NanoPic Flow proxy task succeeded but media download failed"
        return True, result, ""

    def _poll_task_flow(
        self,
        task_id: str,
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        expect_video: bool = False,
    ) -> tuple[Optional[NanoPicResult], str]:
        """Poll Flow proxy task via /api/fix/task-status."""
        deadline = time.time() + self.poll_timeout
        last_message = ""
        base_url = self.base_url.replace('/api/v2', '/api')
        processing_states = {"processing", "pending", "queued", "running", "in_progress", "task is being processed"}
        success_states = {"completed", "complete", "success", "successful", "successfully", "succeeded", "done", "finished"}
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{base_url}/fix/task-status",
                    headers={"Authorization": f"Bearer {self.nano_token}"},
                    params={"taskId": task_id},
                    timeout=self.timeout
                )
                if response.status_code != 200:
                    return None, self._response_error(response)
                data = response.json()
                raw_status = str(data.get("status") or data.get("code") or data.get("message") or "")
                status = raw_status.strip().lower()
                if poll_callback:
                    poll_callback({"status": raw_status, "task_id": task_id})

                if data.get("success") is True:
                    result_data = data.get("result") or data.get("data") or {}
                    if expect_video:
                        media_result = self._flow_video_media_result(result_data)
                        if media_result.media_url:
                            return media_result, ""
                        media_status = self._flow_video_media_status(result_data)
                        media_summary = self._flow_video_media_summary(result_data)
                        if media_status:
                            last_message = media_summary or media_status
                            upper_status = media_status.upper()
                            if any(token in upper_status for token in ("FAILED", "FAILURE", "ERROR", "CANCELLED")):
                                return None, media_summary or media_status
                            if any(token in upper_status for token in ("SCHEDULED", "ACTIVE", "PENDING", "PROCESSING", "RUNNING")):
                                time.sleep(self.poll_interval)
                                continue
                            if "SUCCESS" not in upper_status:
                                time.sleep(self.poll_interval)
                                continue
                        operations = self._extract_operations(data)
                        if operations:
                            polled, poll_error = self._poll_google_video_with_operations(operations, poll_callback=poll_callback)
                            if polled:
                                return polled, ""
                            last_message = poll_error or media_summary or "waiting for video"
                            time.sleep(self.poll_interval)
                            continue

                    if status in processing_states or status.startswith("task is being"):
                        last_message = raw_status
                        time.sleep(self.poll_interval)
                        continue

                    if status in success_states or result_data:
                        parsed = self._parse_flow_result(data)
                        if parsed.media_url or parsed.encoded_image:
                            return parsed, ""
                        err_msg = self._find_error_in_object(result_data)
                        if err_msg:
                            return None, err_msg
                        if expect_video:
                            media_result = self._flow_video_media_result(result_data)
                            media_summary = self._flow_video_media_summary(result_data)
                            if media_result.media_url:
                                return media_result, ""
                            if media_result.media_id:
                                last_message = media_summary or f"{raw_status or 'success'} without downloadable video"
                                time.sleep(self.poll_interval)
                                continue
                        if parsed.media_id:
                            last_message = f"{raw_status or 'success'} without downloadable media"
                            time.sleep(self.poll_interval)
                            continue
                        return None, f"NanoPic Flow task finished without media: {str(data)[:500]}"

                    last_message = raw_status or "waiting"
                    time.sleep(self.poll_interval)
                    continue

                message = data.get("message") or data.get("error") or data.get("code") or ""
                if str(message).strip().lower() in processing_states or str(message).strip().lower().startswith("task is being"):
                    last_message = str(message)
                    time.sleep(self.poll_interval)
                    continue
                return None, message or str(data)[:500]
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_message = f"poll network retry: {type(exc).__name__}"
                time.sleep(self.poll_interval)
                continue
            except Exception as exc:
                return None, f"NanoPic Flow poll error: {type(exc).__name__}: {exc}"
        return None, f"NanoPic Flow task timeout after {int(self.poll_timeout)}s: {last_message or task_id}"

    def _extract_operations(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        result_data = data.get("result") or data.get("data") or {}
        if isinstance(result_data, dict):
            operations = result_data.get("operations")
            if isinstance(operations, list) and operations:
                return [op for op in operations if isinstance(op, dict)]
        operations = data.get("operations")
        if isinstance(operations, list) and operations:
            return [op for op in operations if isinstance(op, dict)]
        return []

    def _flow_video_media_result(self, result_data: Any) -> NanoPicResult:
        if not isinstance(result_data, dict):
            return NanoPicResult()
        media_items = result_data.get("media") or []
        if not isinstance(media_items, list):
            return NanoPicResult()
        for item in media_items:
            if not isinstance(item, dict):
                continue
            found = self._find_media_in_object(item)
            media_id = str(item.get("name") or item.get("mediaId") or item.get("id") or found.media_id or "")
            video = item.get("video") if isinstance(item.get("video"), dict) else {}
            operation = video.get("operation") if isinstance(video.get("operation"), dict) else {}
            operation_name = str(operation.get("name") or "")
            if found.media_url:
                return NanoPicResult(media_id=media_id or operation_name, media_url=found.media_url)
            if operation_name or media_id:
                return NanoPicResult(media_id=operation_name or media_id)
        return NanoPicResult()

    @staticmethod
    def _flow_video_media_status(result_data: Any) -> str:
        if not isinstance(result_data, dict):
            return ""
        media_items = result_data.get("media") or []
        if not isinstance(media_items, list):
            return ""
        for item in media_items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("mediaMetadata")
            if not isinstance(metadata, dict):
                continue
            media_status = metadata.get("mediaStatus")
            if isinstance(media_status, dict):
                status = str(media_status.get("mediaGenerationStatus") or "")
                if status:
                    return status
        return ""

    @staticmethod
    def _flow_video_media_summary(result_data: Any) -> str:
        if not isinstance(result_data, dict):
            return ""
        media_items = result_data.get("media") or []
        if not isinstance(media_items, list) or not media_items:
            return ""

        summaries = []
        for item in media_items:
            if not isinstance(item, dict):
                continue
            media_id = str(item.get("name") or item.get("id") or "")
            status = ""
            metadata = item.get("mediaMetadata")
            if isinstance(metadata, dict):
                media_status = metadata.get("mediaStatus")
                if isinstance(media_status, dict):
                    status = str(media_status.get("mediaGenerationStatus") or "")
            video = item.get("video")
            model = ""
            if isinstance(video, dict):
                generated = video.get("generatedVideo")
                if isinstance(generated, dict):
                    model = str(generated.get("model") or "")
            parts = []
            if media_id:
                parts.append(f"media_id={media_id}")
            if status:
                parts.append(f"status={status}")
            if model:
                parts.append(f"model={model}")
            if parts:
                summaries.append(", ".join(parts))
        return "; ".join(summaries)

    def _poll_google_video_with_operations(
        self,
        operations: List[Dict[str, Any]],
        poll_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> tuple[Optional[NanoPicResult], str]:
        deadline = time.time() + self.poll_timeout
        url = f"{self.flow_base_url}/v1/video:batchCheckAsyncVideoGenerationStatus"
        headers = {
            "Authorization": f"Bearer {self.flow_auth_token}",
            "Content-Type": "application/json",
        }
        last_status = ""
        while time.time() < deadline:
            try:
                response = requests.post(url, headers=headers, json={"operations": operations}, timeout=self.timeout)
                if response.status_code != 200:
                    last_status = self._response_error(response)
                    time.sleep(self.poll_interval)
                    continue
                data = response.json()
                if poll_callback:
                    poll_callback({"status": "google_video_poll", "task_id": ""})
                parsed = self._parse_google_video_status(data)
                if parsed.media_url:
                    return parsed, ""
                err_msg = self._find_error_in_object(data)
                if err_msg:
                    return None, err_msg
                last_status = self._find_status_in_object(data) or "waiting for video"
                if "FAILED" in last_status or "ERROR" in last_status:
                    return None, last_status
                time.sleep(self.poll_interval)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_status = f"google poll network retry: {type(exc).__name__}"
                time.sleep(self.poll_interval)
            except Exception as exc:
                return None, f"NanoPic Google video poll error: {type(exc).__name__}: {exc}"
        return None, f"NanoPic Google video timeout after {int(self.poll_timeout)}s: {last_status}"

    def _parse_google_video_status(self, data: Dict[str, Any]) -> NanoPicResult:
        operations = data.get("operations") or []
        if isinstance(operations, list):
            for op in operations:
                if not isinstance(op, dict):
                    continue
                status = str(op.get("status") or "")
                if "SUCCESS" not in status and "SUCCEEDED" not in status and "COMPLETE" not in status:
                    continue
                media_info = op.get("operation", {}).get("metadata", {}).get("video", {})
                if isinstance(media_info, dict):
                    media_url = media_info.get("fifeUrl") or media_info.get("downloadUrl") or media_info.get("videoUrl") or media_info.get("url")
                    media_id = media_info.get("mediaId") or media_info.get("id") or op.get("operation", {}).get("name")
                    if media_url:
                        return NanoPicResult(media_id=str(media_id or ""), media_url=str(media_url))
                found = self._find_media_in_object(op)
                if found.media_url:
                    return found
        return NanoPicResult()

    @staticmethod
    def _find_status_in_object(obj: Any) -> str:
        status = ""

        def walk(value: Any) -> None:
            nonlocal status
            if status:
                return
            if isinstance(value, dict):
                candidate = value.get("status") or value.get("state")
                if candidate:
                    status = str(candidate)
                    return
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(obj)
        return status

    def _parse_flow_result(self, data: Dict[str, Any]) -> NanoPicResult:
        """Parse Flow proxy result from /api/fix/task-status."""
        result_data = data.get("result") or data.get("data") or {}
        if not isinstance(result_data, dict):
            result_data = {}

        media_items = result_data.get("media") or []
        if isinstance(media_items, list):
            for media_item in media_items:
                if not isinstance(media_item, dict):
                    continue
                generated = media_item.get("image", {}).get("generatedImage", {}) if isinstance(media_item.get("image"), dict) else {}
                video_info = media_item.get("video", {}) if isinstance(media_item.get("video"), dict) else {}
                media_info = generated or video_info or media_item
                if isinstance(media_info, dict):
                    media_id = media_item.get("name") or media_info.get("mediaId") or media_info.get("media_id") or media_info.get("mediaGenerationId") or media_info.get("id") or media_item.get("workflowId")
                    media_url = media_info.get("fifeUrl") or media_info.get("downloadUrl") or media_info.get("videoUrl") or media_info.get("url")
                    encoded_image = media_info.get("encodedImage") or media_info.get("base64EncodedImage") or media_info.get("base64")
                    if media_url or encoded_image:
                        return NanoPicResult(media_id=str(media_id or ""), media_url=str(media_url or ""), encoded_image=str(encoded_image or ""))

        operations = result_data.get("operations") or []
        if isinstance(operations, list):
            for op in operations:
                found = self._find_media_in_object(op)
                if found.media_url or found.encoded_image or found.media_id:
                    return found

        responses = result_data.get("responses") or []
        if isinstance(responses, list):
            for first_resp in responses:
                if isinstance(first_resp, dict):
                    generated = first_resp.get("generatedImages") or first_resp.get("generatedVideos") or []
                    if generated and isinstance(generated, list):
                        for media in generated:
                            found = self._find_media_in_object(media)
                            if found.media_url or found.encoded_image or found.media_id:
                                return found

        found = self._find_media_in_object(result_data)
        if found.media_url or found.encoded_image or found.media_id:
            return found

        return NanoPicResult(
            media_id=str(result_data.get("mediaId") or result_data.get("id") or ""),
            media_url=str(result_data.get("mediaUrl") or result_data.get("url") or ""),
            encoded_image=str(result_data.get("encodedImage") or result_data.get("base64") or ""),
        )

    @staticmethod
    def _find_error_in_object(obj: Any) -> str:
        errors = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                err = value.get("error")
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("status") or err.get("code")
                    if msg:
                        errors.append(str(msg))
                elif isinstance(err, str) and err:
                    errors.append(err)
                for key in ("message", "status"):
                    if value.get("error") and value.get(key):
                        errors.append(str(value.get(key)))
                for child in value.values():
                    if not errors:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    if not errors:
                        walk(child)

        walk(obj)
        return errors[0] if errors else ""

    @staticmethod
    def _find_media_in_object(obj: Any) -> NanoPicResult:
        media_id = ""
        media_url = ""
        encoded_image = ""

        def walk(value: Any) -> None:
            nonlocal media_id, media_url, encoded_image
            if isinstance(value, dict):
                if not media_id:
                    media_id = str(value.get("mediaGenerationId") or value.get("mediaId") or value.get("media_id") or value.get("id") or "")
                if not media_url:
                    for key in ("fifeUrl", "downloadUrl", "downloadURL", "signedUrl", "videoUrl", "videoURL", "mediaUrl", "url", "uri"):
                        candidate = value.get(key)
                        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                            media_url = candidate
                            break
                if not encoded_image:
                    encoded_image = str(value.get("encodedImage") or value.get("base64EncodedImage") or value.get("base64") or "")
                if media_url and (encoded_image or media_id):
                    return
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str) and not media_url and value.startswith(("http://", "https://")):
                if any(hint in value for hint in ("fife", "download", ".mp4", "video", "image", "googleusercontent", "storage")):
                    media_url = value

        walk(obj)
        return NanoPicResult(media_id=media_id, media_url=media_url, encoded_image=encoded_image)
