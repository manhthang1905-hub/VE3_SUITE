"""
VE3 Tool - Voice to SRT Module
==============================
Chuyển đổi file audio thành file subtitle SRT sử dụng Whisper.
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


from pathlib import Path
from typing import Optional, Dict, Any

from modules.utils import get_logger, format_srt_time


# ============================================================================
# WHISPER AVAILABILITY CHECK
# ============================================================================

WHISPER_AVAILABLE = False
WHISPER_TIMESTAMPED_AVAILABLE = False

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    pass

try:
    import whisper_timestamped
    WHISPER_TIMESTAMPED_AVAILABLE = True
except ImportError:
    pass


class WhisperNotFoundError(Exception):
    """Exception khi không tìm thấy Whisper."""
    
    def __init__(self):
        message = """
Whisper không được cài đặt. Vui lòng cài đặt một trong các package sau:

Option 1 - Whisper gốc (OpenAI):
    pip install openai-whisper

Option 2 - Whisper Timestamped (khuyến nghị, timestamp chính xác hơn):
    pip install whisper-timestamped

Lưu ý: Cả hai đều yêu cầu FFmpeg được cài đặt trên hệ thống.
- Windows: choco install ffmpeg hoặc download từ https://ffmpeg.org/
- macOS: brew install ffmpeg
- Linux: sudo apt install ffmpeg
        """
        super().__init__(message)


# ============================================================================
# VOICE TO SRT CONVERTER
# ============================================================================

class VoiceToSrt:
    """
    Class chuyển đổi file audio thành file SRT.
    
    Sử dụng Whisper để transcribe audio và tạo subtitle với timestamp.
    Ưu tiên whisper_timestamped nếu có vì cho timestamp chính xác hơn.
    """
    
    def __init__(
        self,
        model_name: str = "medium",
        language: Optional[str] = None,
        device: Optional[str] = None
    ):
        """
        Khởi tạo VoiceToSrt converter.
        
        Args:
            model_name: Tên model Whisper (tiny, base, small, medium, large)
            language: Ngôn ngữ (ví dụ: "vi", "en"). None để tự phát hiện.
            device: Device để chạy model (cpu, cuda). None để tự chọn.
        """
        self.model_name = model_name
        self.language = language
        self.device = device
        self.logger = get_logger("voice_to_srt")
        
        # Kiểm tra Whisper có sẵn không
        if not WHISPER_AVAILABLE and not WHISPER_TIMESTAMPED_AVAILABLE:
            raise WhisperNotFoundError()
        
        # Chọn backend
        self.use_timestamped = WHISPER_TIMESTAMPED_AVAILABLE
        if self.use_timestamped:
            self.logger.info("Using whisper_timestamped backend")
        else:
            self.logger.info("Using standard whisper backend")
        
        # Load model (lazy loading)
        self._model = None
    
    def _load_model(self):
        """Load Whisper model (lazy loading)."""
        if self._model is not None:
            return

        self.logger.info(f"Loading Whisper model: {self.model_name}")
        print(f"  [WAIT] Loading Whisper model '{self.model_name}'... (this may take a moment)")

        if self.use_timestamped:
            import whisper_timestamped
            self._model = whisper_timestamped.load_model(
                self.model_name,
                device=self.device
            )
        else:
            import whisper
            self._model = whisper.load_model(
                self.model_name,
                device=self.device
            )

        print(f"  [OK] Whisper model loaded!")
        self.logger.info("Model loaded successfully")
    
    def transcribe(
        self,
        input_audio_path: Path,
        output_srt_path: Path,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Transcribe file audio và tạo file SRT.
        
        Args:
            input_audio_path: Path đến file audio (mp3, wav, m4a, ...)
            output_srt_path: Path để lưu file SRT
            **kwargs: Các tham số bổ sung cho Whisper
            
        Returns:
            Dictionary chứa kết quả transcription
            
        Raises:
            FileNotFoundError: Nếu file audio không tồn tại
            RuntimeError: Nếu transcription thất bại
        """
        # Validate input
        input_audio_path = Path(input_audio_path)
        output_srt_path = Path(output_srt_path)
        
        if not input_audio_path.exists():
            raise FileNotFoundError(f"File audio không tồn tại: {input_audio_path}")
        
        # Tạo thư mục output nếu chưa có
        output_srt_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load model
        self._load_model()

        self.logger.info(f"Transcribing: {input_audio_path}")
        print(f"  [WAIT] Transcribing audio... (may take 1-2 minutes for long files)")

        # Transcribe
        try:
            if self.use_timestamped:
                result = self._transcribe_timestamped(input_audio_path, **kwargs)
            else:
                result = self._transcribe_standard(input_audio_path, **kwargs)
        except Exception as e:
            self.logger.error(f"Transcription failed: {e}")
            raise RuntimeError(f"Transcription thất bại: {e}")
        
        # Tạo file SRT
        self._write_srt(result, output_srt_path)
        
        self.logger.info(f"SRT saved to: {output_srt_path}")
        
        return result
    
    def _transcribe_timestamped(
        self,
        audio_path: Path,
        **kwargs
    ) -> Dict[str, Any]:
        """Transcribe sử dụng whisper_timestamped."""
        import whisper_timestamped
        
        transcribe_options = {
            "language": self.language,
            "beam_size": 5,
            "best_of": 5,
            "vad": True,  # Voice Activity Detection
            "detect_disfluencies": False,
        }
        transcribe_options.update(kwargs)

        # Try with VAD first, fallback to no VAD if error
        try:
            result = whisper_timestamped.transcribe(
                self._model,
                str(audio_path),
                **transcribe_options
            )
        except Exception as e:
            error_msg = str(e)
            if "silero" in error_msg.lower() or "vad" in error_msg.lower() or "select()" in error_msg:
                print(f"[Whisper] VAD error, retrying without VAD: {error_msg[:100]}")
                transcribe_options["vad"] = False
                result = whisper_timestamped.transcribe(
                    self._model,
                    str(audio_path),
                    **transcribe_options
                )
            else:
                raise

        return result
    
    def _transcribe_standard(
        self,
        audio_path: Path,
        **kwargs
    ) -> Dict[str, Any]:
        """Transcribe sử dụng standard whisper."""
        import whisper
        
        transcribe_options = {
            "language": self.language,
            "task": "transcribe",
            "verbose": False,
        }
        transcribe_options.update(kwargs)
        
        result = self._model.transcribe(
            str(audio_path),
            **transcribe_options
        )
        
        return result
    
    def _write_srt(self, result: Dict[str, Any], output_path: Path) -> None:
        """
        Ghi kết quả transcription ra file SRT.

        Args:
            result: Kết quả từ Whisper
            output_path: Path file SRT
        """
        segments = result.get("segments", [])

        with open(output_path, "w", encoding="utf-8") as f:
            for idx, segment in enumerate(segments, start=1):
                start_time = segment.get("start", 0)
                end_time = segment.get("end", 0)
                text = segment.get("text", "").strip()

                # Format thời gian SRT
                start_str = self._seconds_to_srt_time(start_time)
                end_str = self._seconds_to_srt_time(end_time)

                # Ghi entry
                f.write(f"{idx}\n")
                f.write(f"{start_str} --> {end_str}\n")
                f.write(f"{text}\n")
                f.write("\n")

        # Cũng xuất file TXT (full text không có timestamp) cho đạo diễn
        self._write_txt(result, output_path)

    def _write_txt(self, result: Dict[str, Any], srt_path: Path) -> None:
        """
        Ghi kết quả transcription ra file TXT (không có timestamp).
        File TXT dùng cho đạo diễn để đọc và phân tích nội dung.

        Args:
            result: Kết quả từ Whisper
            srt_path: Path file SRT (sẽ đổi đuôi thành .txt)
        """
        segments = result.get("segments", [])
        txt_path = srt_path.with_suffix(".txt")

        # Ghép tất cả text thành đoạn văn
        full_text = " ".join([
            segment.get("text", "").strip()
            for segment in segments
        ])

        # Xử lý format: đảm bảo có space sau dấu câu
        import re
        full_text = re.sub(r'([.!?])([A-ZÀ-Ỹ])', r'\1 \2', full_text)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        self.logger.info(f"TXT saved to: {txt_path}")
    
    @staticmethod
    def _seconds_to_srt_time(seconds: float) -> str:
        """
        Chuyển đổi số giây thành format thời gian SRT.
        
        Args:
            seconds: Số giây
            
        Returns:
            Chuỗi thời gian dạng "HH:MM:SS,mmm"
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def convert_voice_to_srt(
    input_audio_path: Path,
    output_srt_path: Path,
    model_name: str = "medium",
    language: Optional[str] = None
) -> Dict[str, Any]:
    """
    Hàm tiện ích để chuyển đổi voice thành SRT.
    
    Args:
        input_audio_path: Path đến file audio
        output_srt_path: Path để lưu file SRT
        model_name: Tên model Whisper
        language: Ngôn ngữ (None để tự phát hiện)
        
    Returns:
        Kết quả transcription
    """
    converter = VoiceToSrt(model_name=model_name, language=language)
    return converter.transcribe(input_audio_path, output_srt_path)
