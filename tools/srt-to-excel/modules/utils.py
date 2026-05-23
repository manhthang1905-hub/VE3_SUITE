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
import math
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
    settings.setdefault("whisper_model", "large-v3")
    settings.setdefault("whisper_language", "auto")
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
    min_duration: float = 5.0,
    max_duration: float = 8.0
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

    target_duration = (float(min_duration) + float(max_duration)) / 2.0
    soft_max_duration = max(float(max_duration), target_duration + 1.25)
    scenes: List[Dict[str, Any]] = []

    continuation_words = {
        "and", "or", "but", "because", "which", "who", "whom", "whose",
        "that", "where", "when", "while", "though", "although", "since",
        "until", "before", "after", "if", "then", "than", "like", "as",
        "with", "without", "from", "into", "onto", "through", "trying",
        "being", "feeling", "making", "taking", "holding", "thinking",
        "saying", "telling", "looking", "watching", "waiting", "using",
        "called", "meant", "means", "meaning", "people", "everything",
        "something", "someone", "nothing", "gift", "store", "sitting",
        "breathe", "of", "to",
    }
    weak_endings = {
        "a", "an", "the", "and", "or", "but", "because", "which", "who",
        "that", "with", "without", "from", "to", "for", "of", "in", "on",
        "at", "by", "into", "onto", "through", "than", "as", "if", "when",
        "while", "trying", "being", "feeling", "making", "taking", "holding",
        "thinking", "saying", "telling", "looking", "people", "something",
        "everything", "gift", "store",
    }
    shift_markers = {
        "then", "later", "after", "by the time", "the next", "that night",
        "the following", "meanwhile", "instead", "eventually", "suddenly",
        "on friday", "on monday", "on tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday", "that morning", "that afternoon",
        "that evening", "back at", "at work", "at home",
    }

    def get_start(item: Dict[str, Any]) -> timedelta:
        return item["start_time"]

    def get_end(item: Dict[str, Any]) -> timedelta:
        return item["end_time"]

    def split_unit_by_duration(unit: Dict[str, Any]) -> List[Dict[str, Any]]:
        duration = (get_end(unit) - get_start(unit)).total_seconds()
        if duration <= max_duration:
            return [unit]
        parts = max(2, math.ceil(duration / max_duration))
        part_seconds = duration / parts
        words = get_text(unit).split()
        out: List[Dict[str, Any]] = []
        for part_index in range(parts):
            part_start = get_start(unit) + timedelta(seconds=part_seconds * part_index)
            part_end = get_end(unit) if part_index == parts - 1 else get_start(unit) + timedelta(seconds=part_seconds * (part_index + 1))
            word_start = int(len(words) * part_index / parts)
            word_end = int(len(words) * (part_index + 1) / parts)
            part_text = " ".join(words[word_start:word_end]).strip() or get_text(unit)
            out.append(make_unit(part_start, part_end, part_text, get_indices(unit), unit.get("force_boundary_before") if part_index == 0 else False))
        return out

    def get_text(item: Dict[str, Any]) -> str:
        return " ".join(str(item.get("text", "") or "").split()).strip()

    def get_indices(item: Dict[str, Any]) -> List[int]:
        return list(item.get("srt_indices", []) or [])

    def items_duration(items: List[Dict[str, Any]]) -> float:
        if not items:
            return 0.0
        return (get_end(items[-1]) - get_start(items[0])).total_seconds()

    def items_text(items: List[Dict[str, Any]]) -> str:
        return " ".join(get_text(item) for item in items if get_text(item)).strip()

    def make_unit(
        start_time: timedelta,
        end_time: timedelta,
        text: str,
        srt_indices: List[int],
        force_boundary_before: bool = False,
    ) -> Dict[str, Any]:
        dur = max(0.01, (end_time - start_time).total_seconds())
        normalized_text = " ".join(str(text or "").split()).strip()
        return {
            "start_time": start_time,
            "end_time": end_time,
            "text": normalized_text,
            "srt_text": normalized_text,
            "srt_indices": list(srt_indices or []),
            "duration_seconds": round(dur, 2),
            "duration": round(dur, 2),
            "force_boundary_before": bool(force_boundary_before),
        }

    def split_text_timed(text: str, start_time: timedelta, end_time: timedelta, srt_indices: List[int]) -> List[Dict[str, Any]]:
        normalized = " ".join(str(text or "").split()).strip()
        total_seconds = max(0.001, (end_time - start_time).total_seconds())
        if not normalized:
            return [make_unit(start_time, end_time, normalized, srt_indices)]

        def clause_beat_type(fragment: str) -> str:
            frag = " ".join(str(fragment or "").split()).strip().lower()
            if not frag:
                return "neutral"
            if any(k in frag for k in [
                "drove", "driving", "drive away", "got into her car", "got into his car",
                "crossed the lot", "walked", "walking", "left work", "headed", "pulled into"
            ]):
                return "movement"
            if any(k in frag for k in [
                "said", "asked", "told me", "explained", "speaking", "talk about",
                "healthy for us", "strengthen their bond", "not the only model"
            ]):
                return "dialogue"
            if any(k in frag for k in [
                "looked at me", "held his gaze", "held her gaze", "sat there", "couldn't form",
                "frozen", "stunned", "absorbs", "noticed", "remember what"
            ]):
                return "reaction"
            if any(k in frag for k in [
                "laptop", "phone bill", "transaction", "charge", "call logs", "texts, calls",
                "wells fargo", "cad files", "records", "screen"
            ]):
                return "screen"
            if any(k in frag for k in [
                "fork", "pasta", "coffee", "cup", "making coffee", "door handle", "opened my phone"
            ]):
                return "object_action"
            if any(k in frag for k in [
                "house looked", "room", "kitchen", "parking lot", "felt like", "the space", "the house"
            ]):
                return "environment"
            return "neutral"

        def should_force_boundary(current_text: str, next_piece: str) -> bool:
            current_frag = " ".join(str(current_text or "").split()).strip().lower()
            next_frag = " ".join(str(next_piece or "").split()).strip().lower()
            if not current_frag or not next_frag:
                return False
            current_type = clause_beat_type(current_frag)
            next_type = clause_beat_type(next_frag)
            if current_type == next_type:
                return False
            strong_pairs = {
                ("environment", "movement"),
                ("object_action", "movement"),
                ("screen", "reaction"),
                ("reaction", "movement"),
                ("dialogue", "movement"),
                ("screen", "movement"),
            }
            if (current_type, next_type) in strong_pairs:
                return True
            if any(k in next_frag for k in ["i drove", "she drove away", "he drove", "got into her car", "got into his car"]):
                return True
            if any(k in current_frag for k in ["making coffee", "stood in the kitchen"]) and any(k in next_frag for k in ["drove to work", "on autopilot"]):
                return True
            return False

        pieces = re.findall(r"[^.!?]+[.!?\"']*|[^.!?]+$", normalized)
        pieces = [" ".join(piece.split()).strip() for piece in pieces if piece and piece.strip()]
        if len(pieces) <= 1:
            pieces = re.split(r"(?<=,)\s+|(?<=;)\s+|(?<=:)\s+", normalized)
            pieces = [" ".join(piece.split()).strip() for piece in pieces if piece and piece.strip()]
        if len(pieces) <= 1:
            words = normalized.split()
            target_words = max(10, int(len(words) * (target_duration / max(total_seconds, 0.1))))
            target_words = max(target_words, 12)
            pieces = [" ".join(words[i:i + target_words]).strip() for i in range(0, len(words), target_words)]

        seconds_per_word = total_seconds / max(1, len(normalized.split()))

        chunks: List[str] = []
        chunk_force_boundary_before: List[bool] = []
        current = ""
        current_words = 0
        current_force_boundary_before = False
        soft_words_cap = max(12, int(soft_max_duration / max(seconds_per_word, 0.15)))

        for piece in pieces:
            piece_words = len(piece.split())
            if not current:
                current = piece
                current_words = piece_words
                current_force_boundary_before = False
                continue
            projected_words = current_words + piece_words
            projected_duration = projected_words * seconds_per_word
            if should_force_boundary(current, piece):
                chunks.append(current)
                chunk_force_boundary_before.append(current_force_boundary_before)
                current = piece
                current_words = piece_words
                current_force_boundary_before = True
            elif projected_duration <= soft_max_duration and projected_words <= soft_words_cap:
                current = f"{current} {piece}".strip()
                current_words = projected_words
            else:
                chunks.append(current)
                chunk_force_boundary_before.append(current_force_boundary_before)
                current = piece
                current_words = piece_words
                current_force_boundary_before = False
        if current:
            chunks.append(current)
            chunk_force_boundary_before.append(current_force_boundary_before)

        if len(chunks) > 1 and len(chunks[-1].split()) < 5:
            chunks[-2] = f"{chunks[-2]} {chunks[-1]}".strip()
            chunks.pop()
            chunk_force_boundary_before.pop()

        total_words = max(1, sum(len(chunk.split()) for chunk in chunks))
        units: List[Dict[str, Any]] = []
        running_start = start_time
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index == len(chunks) - 1:
                chunk_end = end_time
            else:
                weight = len(chunk.split()) / total_words
                chunk_end = running_start + timedelta(seconds=total_seconds * weight)
            units.append(
                make_unit(
                    running_start,
                    chunk_end,
                    chunk,
                    srt_indices,
                    force_boundary_before=chunk_force_boundary_before[chunk_index],
                )
            )
            running_start = chunk_end
        return units

    def is_continuation_boundary(prev_text: str, next_text: str) -> bool:
        prev_clean = " ".join(str(prev_text or "").split()).strip()
        next_clean = " ".join(str(next_text or "").split()).strip()
        if not prev_clean or not next_clean:
            return False

        if re.search(r"[.!?][\"')\]]*$", prev_clean) and next_clean[:1].isupper():
            return False

        next_first_match = re.match(r"([A-Za-z']+)", next_clean)
        next_first = next_first_match.group(1).lower() if next_first_match else ""
        prev_last_match = re.search(r"([A-Za-z']+)[^A-Za-z']*$", prev_clean)
        prev_last = prev_last_match.group(1).lower() if prev_last_match else ""

        if next_clean[:1].islower():
            return True
        if next_first in continuation_words:
            return True
        if prev_last in weak_endings:
            return True
        if re.search(r"[,;:][\"')\]]*$", prev_clean):
            return True
        return False

    def build_atomic_units() -> List[Dict[str, Any]]:
        atomic_units: List[Dict[str, Any]] = []
        chain: List[SrtEntry] = []

        def flush_chain(chain_items: List[SrtEntry]) -> None:
            if not chain_items:
                return
            chain_text = " ".join(" ".join(str(item.text or "").split()).strip() for item in chain_items if str(item.text or "").strip()).strip()
            chain_start = chain_items[0].start_time
            chain_end = chain_items[-1].end_time
            chain_indices = [item.index for item in chain_items]
            chain_duration = max(0.001, (chain_end - chain_start).total_seconds())

            if len(chain_items) == 1 and chain_duration <= soft_max_duration:
                atomic_units.append(make_unit(chain_start, chain_end, chain_text, chain_indices))
                return

            for unit in split_text_timed(chain_text, chain_start, chain_end, chain_indices):
                atomic_units.append(unit)

        for entry in entries:
            if not chain:
                chain = [entry]
                continue
            prev_text = str(chain[-1].text or "")
            next_text = str(entry.text or "")
            if is_continuation_boundary(prev_text, next_text):
                chain.append(entry)
            else:
                flush_chain(chain)
                chain = [entry]

        flush_chain(chain)
        return atomic_units

    atomic_units = []
    for unit in build_atomic_units():
        atomic_units.extend(split_unit_by_duration(unit))

    def boundary_score(items: List[Dict[str, Any]], next_item: Optional[Dict[str, Any]]) -> float:
        duration = items_duration(items)
        score = 8.0 - abs(duration - target_duration) * 1.15
        prev_text = get_text(items[-1])
        next_text = get_text(next_item) if next_item else ""
        full_text = items_text(items)

        if not next_item:
            return score + 6.0

        if re.search(r"[.!?][\"')\]]*$", prev_text):
            score += 4.0
        elif re.search(r"[;:][\"')\]]*$", prev_text):
            score += 1.5
        elif re.search(r"[,][\"')\]]*$", prev_text):
            score -= 2.5

        prev_last_word_match = re.search(r"([A-Za-z']+)[^A-Za-z']*$", prev_text)
        if prev_last_word_match and prev_last_word_match.group(1).lower() in weak_endings:
            score -= 3.5

        next_first_word_match = re.match(r"([A-Za-z']+)", next_text)
        next_first_word = next_first_word_match.group(1).lower() if next_first_word_match else ""
        if next_first_word in continuation_words:
            score -= 4.0

        if prev_text and prev_text[-1:].isalnum() and next_text[:1].islower():
            score -= 6.5

        if next_text[:1].isupper():
            score += 0.8

        next_lower = next_text.lower()
        if any(next_lower.startswith(marker) for marker in shift_markers):
            score += 2.0
        if next_item and next_item.get("force_boundary_before"):
            score += 5.5

        if re.search(r"[.!?][\"')\]]*$", full_text):
            score += 1.2
        if re.search(r"\b(looked at|asked|said|told|then|later|after|before|suddenly)\b", next_lower):
            score += 0.6

        if duration > max_duration:
            score -= (duration - max_duration) * 7.5
        return score

    def append_scene_items(items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        duration = items_duration(items)
        text = items_text(items)
        indices: List[int] = []
        for item in items:
            for idx in get_indices(item):
                if idx not in indices:
                    indices.append(idx)
        scenes.append({
            "scene_id": len(scenes) + 1,
            "start_time": get_start(items[0]),
            "end_time": get_end(items[-1]),
            "text": text,
            "srt_text": text,
            "srt_start": format_srt_time(get_start(items[0])),
            "srt_end": format_srt_time(get_end(items[-1])),
            "duration_seconds": round(duration, 2),
            "duration": round(duration, 2),
            "srt_indices": indices,
            "force_boundary_before": bool(items[0].get("force_boundary_before")),
        })

    i = 0
    while i < len(atomic_units):
        best_end = None
        best_score = float("-inf")
        fallback_end = i
        fallback_score = float("-inf")
        j = i

        while j < len(atomic_units):
            items = atomic_units[i:j + 1]
            duration = items_duration(items)
            next_item = atomic_units[j + 1] if j + 1 < len(atomic_units) else None
            score = boundary_score(items, next_item)

            if score > fallback_score:
                fallback_score = score
                fallback_end = j

            if duration >= min_duration and duration <= max_duration:
                if score > best_score:
                    best_score = score
                    best_end = j

            if duration > max_duration:
                if best_end is not None:
                    break
                if fallback_end == j:
                    fallback_end = max(i, j - 1)
                break
            j += 1

        if best_end is None:
            best_end = fallback_end

        chosen_items = atomic_units[i:best_end + 1]
        remaining_units = len(atomic_units) - (best_end + 1)
        remaining_duration = 0.0
        if remaining_units > 0:
            remaining_duration = (get_end(atomic_units[-1]) - get_start(atomic_units[best_end + 1])).total_seconds()

        if (
            remaining_units > 0
            and remaining_duration < min_duration * 0.9
            and len(chosen_items) > 1
        ):
            shifted_end = best_end - 1
            shifted_items = atomic_units[i:shifted_end + 1]
            if shifted_items and items_duration(shifted_items) >= min_duration * 0.85:
                chosen_items = shifted_items
                best_end = shifted_end

        append_scene_items(chosen_items)
        i = best_end + 1

    if len(scenes) >= 2:
        last = scenes[-1]
        prev = scenes[-2]
        if float(last.get("duration") or 0) < min_duration * 0.8 and not last.get("force_boundary_before"):
            merged_duration = (prev["end_time"] - prev["start_time"]).total_seconds() + (last["end_time"] - last["start_time"]).total_seconds()
            if merged_duration <= soft_max_duration:
                scenes.pop()
                scenes.pop()
                append_scene_items([
                    make_unit(prev["start_time"], prev["end_time"], prev["srt_text"], prev["srt_indices"]),
                    make_unit(last["start_time"], last["end_time"], last["srt_text"], last["srt_indices"]),
                ])

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
