"""
VE3 Tool - Utility Functions
============================
Chứa các hàm tiện ích chung cho toàn bộ pipeline.
"""

import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass


import logging
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml


# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

def setup_logging(
    log_file: Optional[Path] = None,
    log_level: str = "INFO",
    logger_name: str = "ve3_tool"
) -> logging.Logger:
    """
    Cấu hình logging cho pipeline.
    
    Args:
        log_file: Path đến file log (nếu None thì chỉ log ra console)
        log_level: Mức độ log (DEBUG, INFO, WARNING, ERROR)
        logger_name: Tên logger
        
    Returns:
        Logger đã được cấu hình
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Format
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (nếu có)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str = "ve3_tool") -> logging.Logger:
    """Lấy logger đã được tạo."""
    return logging.getLogger(name)


# ============================================================================
# CONFIG LOADER
# ============================================================================

class ConfigError(Exception):
    """Exception cho lỗi cấu hình."""
    pass

def load_settings(config_path: Path) -> Dict[str, Any]:
    """
    Đọc file settings.yaml và validate các key bắt buộc.
    
    Args:
        config_path: Path đến file settings.yaml
        
    Returns:
        Dictionary chứa cấu hình
        
    Raises:
        ConfigError: Nếu file không tồn tại hoặc thiếu key bắt buộc
    """
    if not config_path.exists():
        raise ConfigError(f"File cấu hình không tồn tại: {config_path}")
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Lỗi đọc file YAML: {e}")
    
    if settings is None:
        raise ConfigError("File cấu hình rỗng")
    
    # Validate các key bắt buộc (chỉ project_root là bắt buộc)
    required_keys = [
        "project_root",
    ]
    
    missing_keys = [key for key in required_keys if key not in settings]
    if missing_keys:
        raise ConfigError(f"Thiếu các key bắt buộc trong settings.yaml: {missing_keys}")
    
    # Set defaults cho các key optional
    settings.setdefault("flowslab_base_url", "https://app.flowslab.io")
    settings.setdefault("browser", "chrome")
    
    # Validate Gemini config - hỗ trợ cả format cũ và mới (optional - chỉ cần cho prompts)
    has_old_format = "gemini_api_key" in settings and "gemini_model" in settings
    has_new_format = "gemini_api_keys" in settings and "gemini_models" in settings
    
    # Gemini không bắt buộc nếu chỉ dùng Flow API để tạo ảnh
    settings["_gemini_configured"] = False
    
    if has_old_format or has_new_format:
        # Validate API key không phải placeholder
        if has_old_format:
            if settings["gemini_api_key"] != "YOUR_GEMINI_API_KEY_HERE":
                settings["_gemini_configured"] = True
        
        if has_new_format:
            keys = settings["gemini_api_keys"]
            if keys and not all(k == "YOUR_GEMINI_API_KEY_HERE" for k in keys):
                settings["_gemini_configured"] = True
    
    # Set default values
    settings.setdefault("max_scenes_per_account", 50)
    settings.setdefault("retry_count", 3)
    settings.setdefault("wait_timeout", 30)
    settings.setdefault("min_scene_duration", 5)   # 5-8s phù hợp cho video
    settings.setdefault("max_scene_duration", 8)
    settings.setdefault("whisper_model", "base")
    settings.setdefault("whisper_language", "vi")
    settings.setdefault("log_level", "INFO")
    
    # Flow API defaults
    settings.setdefault("flow_bearer_token", "")
    settings.setdefault("flow_project_id", "")
    settings.setdefault("flow_aspect_ratio", "landscape")
    settings.setdefault("flow_delay", 3.0)
    settings.setdefault("flow_timeout", 120)
    
    return settings


# ============================================================================
# PATH UTILITIES
# ============================================================================

def get_project_dir(project_root: Path, code: str) -> Path:
    """
    Lấy đường dẫn thư mục project theo mã code.
    
    Args:
        project_root: Thư mục root chứa PROJECTS
        code: Mã project (ví dụ: "KA1-0001")
        
    Returns:
        Path đến thư mục project
    """
    return project_root / "PROJECTS" / code


def ensure_project_structure(project_dir: Path) -> Dict[str, Path]:
    """
    Tạo cấu trúc thư mục cho project nếu chưa có.
    
    Args:
        project_dir: Thư mục project
        
    Returns:
        Dictionary chứa các path đã tạo
    """
    subdirs = {
        "srt": project_dir / "srt",
        "prompts": project_dir / "prompts",
        "nv": project_dir / "nv",
        "img": project_dir / "img",
        "vid": project_dir / "vid",
        "logs": project_dir / "logs",
    }
    
    for name, path in subdirs.items():
        path.mkdir(parents=True, exist_ok=True)
    
    return subdirs


def find_voice_file(project_dir: Path, code: str) -> Optional[Path]:
    """
    Tìm file voice trong project.
    
    Args:
        project_dir: Thư mục project
        code: Mã project
        
    Returns:
        Path đến file voice hoặc None nếu không tìm thấy
    """
    for ext in [".mp3", ".wav", ".m4a", ".ogg"]:
        voice_file = project_dir / f"{code}{ext}"
        if voice_file.exists():
            return voice_file
    return None


# ============================================================================
# SRT PARSER
# ============================================================================

class SrtEntry:
    """Đại diện cho một entry trong file SRT."""
    
    def __init__(
        self,
        index: int,
        start_time: timedelta,
        end_time: timedelta,
        text: str
    ):
        self.index = index
        self.start_time = start_time
        self.end_time = end_time
        self.text = text
    
    @property
    def duration(self) -> float:
        """Thời lượng của entry (giây)."""
        return (self.end_time - self.start_time).total_seconds()
    
    def __repr__(self):
        return f"SrtEntry({self.index}, {self.start_time}, {self.end_time}, '{self.text[:30]}...')"


def parse_srt_time(time_str: str) -> timedelta:
    """
    Parse thời gian SRT thành timedelta.
    
    Args:
        time_str: Chuỗi thời gian dạng "HH:MM:SS,mmm"
        
    Returns:
        timedelta object
    """
    # SRT format: 00:01:23,456
    time_str = time_str.strip().replace(",", ".")
    parts = time_str.split(":")
    
    if len(parts) != 3:
        raise ValueError(f"Invalid SRT time format: {time_str}")
    
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def format_srt_time(td: timedelta) -> str:
    """
    Format timedelta thành chuỗi thời gian SRT.
    
    Args:
        td: timedelta object
        
    Returns:
        Chuỗi thời gian dạng "HH:MM:SS,mmm"
    """
    total_seconds = td.total_seconds()
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}".replace(".", ",")


def parse_srt_file(srt_path: Path) -> List[SrtEntry]:
    """
    Parse file SRT thành list các SrtEntry.
    
    Args:
        srt_path: Path đến file SRT
        
    Returns:
        List các SrtEntry
        
    Raises:
        FileNotFoundError: Nếu file không tồn tại
        ValueError: Nếu format SRT không hợp lệ
    """
    if not srt_path.exists():
        raise FileNotFoundError(f"File SRT không tồn tại: {srt_path}")
    
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    entries = []
    
    # Pattern để parse SRT
    # Format: index \n start --> end \n text \n\n
    pattern = re.compile(
        r"(\d+)\s*\n"  # Index
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"  # Timestamps
        r"((?:.*?\n)*?)"  # Text (có thể nhiều dòng)
        r"(?:\n|$)",  # Kết thúc bằng dòng trống hoặc EOF
        re.MULTILINE
    )
    
    for match in pattern.finditer(content):
        index = int(match.group(1))
        start_time = parse_srt_time(match.group(2))
        end_time = parse_srt_time(match.group(3))
        text = match.group(4).strip().replace("\n", " ")
        
        entries.append(SrtEntry(index, start_time, end_time, text))
    
    if not entries:
        # Thử parse theo cách khác nếu pattern trên không match
        entries = _parse_srt_fallback(content)
    
    return entries


def _parse_srt_fallback(content: str) -> List[SrtEntry]:
    """
    Fallback parser cho SRT khi pattern chính không hoạt động.
    """
    entries = []
    blocks = re.split(r"\n\s*\n", content.strip())
    
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        
        try:
            index = int(lines[0].strip())
            time_line = lines[1].strip()
            time_parts = time_line.split("-->")
            if len(time_parts) != 2:
                continue
            
            start_time = parse_srt_time(time_parts[0])
            end_time = parse_srt_time(time_parts[1])
            text = " ".join(lines[2:]).strip()
            
            entries.append(SrtEntry(index, start_time, end_time, text))
        except (ValueError, IndexError):
            continue
    
    return entries


def group_srt_into_scenes(
    entries: List[SrtEntry],
    min_duration: float = 15.0,
    max_duration: float = 25.0
) -> List[Dict[str, Any]]:
    """
    Gom các SRT entries thành các scene theo thời lượng.

    Args:
        entries: List các SrtEntry
        min_duration: Thời lượng tối thiểu của scene (giây)
        max_duration: Thời lượng tối đa của scene (giây)

    Returns:
        List các scene, mỗi scene có: scene_id, start_time, end_time, text,
        srt_start, srt_end, duration_seconds, srt_indices
    """
    if not entries:
        return []
    
    scenes = []
    current_scene = {
        "srt_indices": [entries[0].index],
        "texts": [entries[0].text],
        "start_time": entries[0].start_time,
        "end_time": entries[0].end_time,
    }
    
    for entry in entries[1:]:
        # Tính thời lượng nếu thêm entry này
        new_duration = (entry.end_time - current_scene["start_time"]).total_seconds()
        current_duration = (current_scene["end_time"] - current_scene["start_time"]).total_seconds()
        
        # Nếu vượt quá max_duration và đã có đủ min_duration thì tạo scene mới
        if new_duration > max_duration and current_duration >= min_duration:
            # Lưu scene hiện tại
            scene_duration = (current_scene["end_time"] - current_scene["start_time"]).total_seconds()
            scenes.append({
                "scene_id": len(scenes) + 1,
                "start_time": current_scene["start_time"],
                "end_time": current_scene["end_time"],
                "text": " ".join(current_scene["texts"]),
                "srt_start": format_srt_time(current_scene["start_time"]),  # Timestamp thực
                "srt_end": format_srt_time(current_scene["end_time"]),      # Timestamp thực
                "duration_seconds": scene_duration,  # Duration từ SRT
                "srt_indices": current_scene["srt_indices"],  # Giữ lại indices cho reference
            })
            
            # Bắt đầu scene mới
            current_scene = {
                "srt_indices": [entry.index],
                "texts": [entry.text],
                "start_time": entry.start_time,
                "end_time": entry.end_time,
            }
        else:
            # Thêm vào scene hiện tại
            current_scene["srt_indices"].append(entry.index)
            current_scene["texts"].append(entry.text)
            current_scene["end_time"] = entry.end_time
    
    # Thêm scene cuối cùng
    if current_scene["srt_indices"]:
        scene_duration = (current_scene["end_time"] - current_scene["start_time"]).total_seconds()
        scenes.append({
            "scene_id": len(scenes) + 1,
            "start_time": current_scene["start_time"],
            "end_time": current_scene["end_time"],
            "text": " ".join(current_scene["texts"]),
            "srt_start": format_srt_time(current_scene["start_time"]),  # Timestamp thực
            "srt_end": format_srt_time(current_scene["end_time"]),      # Timestamp thực
            "duration_seconds": scene_duration,  # Duration từ SRT
            "srt_indices": current_scene["srt_indices"],  # Giữ lại indices cho reference
        })

    return scenes

# ============================================================================
# MISC UTILITIES
# ============================================================================

def sanitize_filename(name: str) -> str:
    """
    Làm sạch tên file, loại bỏ ký tự không hợp lệ.
    
    Args:
        name: Tên file gốc
        
    Returns:
        Tên file đã được làm sạch
    """
    # Thay thế các ký tự không hợp lệ
    invalid_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid_chars, "_", name)
    
    # Loại bỏ khoảng trắng thừa
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    
    return sanitized


def format_duration(seconds: float) -> str:
    """
    Format thời lượng thành chuỗi dễ đọc.
    
    Args:
        seconds: Số giây
        
    Returns:
        Chuỗi dạng "MM:SS" hoặc "HH:MM:SS"
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
