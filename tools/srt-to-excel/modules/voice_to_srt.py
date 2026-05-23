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
FASTER_WHISPER_AVAILABLE = False

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

try:
    import faster_whisper
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    pass


class WhisperNotFoundError(Exception):
    """Exception khi không tìm thấy Whisper."""
    
    def __init__(self):
        message = """
Whisper không được cài đặt. Vui lòng cài đặt một trong các package sau:

Option 1 - Whisper gốc (OpenAI):
    pip install openai-whisper

Option 2 - Faster Whisper (khuyến nghị cho đa ngôn ngữ, nhanh và ổn định):
    pip install faster-whisper

Option 3 - Whisper Timestamped:
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
    Ưu tiên faster-whisper cho bài toán đa ngôn ngữ + auto detect.
    Fallback sang whisper_timestamped rồi standard whisper.
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
        self.backend = "whisper"
        if FASTER_WHISPER_AVAILABLE:
            self.backend = "faster-whisper"
        elif WHISPER_TIMESTAMPED_AVAILABLE:
            self.backend = "whisper_timestamped"
        if self.backend == "faster-whisper":
            self.logger.info("Using faster-whisper backend")
        elif self.backend == "whisper_timestamped":
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

        if self.backend == "faster-whisper":
            from faster_whisper import WhisperModel
            model_kwargs = {}
            if self.device:
                model_kwargs["device"] = self.device
            self._model = WhisperModel(
                self.model_name,
                **model_kwargs
            )
        elif self.backend == "whisper_timestamped":
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
            if self.backend == "faster-whisper":
                result = self._transcribe_faster_whisper(input_audio_path, **kwargs)
            elif self.backend == "whisper_timestamped":
                result = self._transcribe_timestamped(input_audio_path, **kwargs)
            else:
                result = self._transcribe_standard(input_audio_path, **kwargs)
        except Exception as e:
            self.logger.error(f"Transcription failed: {e}")
            raise RuntimeError(f"Transcription thất bại: {e}")

        detected_lang = result.get("language")
        if detected_lang:
            prob = result.get("language_probability")
            if prob is not None:
                self.logger.info(f"Detected language: {detected_lang} (p={prob:.3f})")
                print(f"  [INFO] Detected language: {detected_lang} (confidence {prob:.3f})")
            else:
                self.logger.info(f"Detected language: {detected_lang}")
                print(f"  [INFO] Detected language: {detected_lang}")
        
        # Tạo file SRT
        self._write_srt(result, output_srt_path)
        
        self.logger.info(f"SRT saved to: {output_srt_path}")

        return result

    def _transcribe_faster_whisper(
        self,
        audio_path: Path,
        **kwargs
    ) -> Dict[str, Any]:
        """Transcribe sử dụng faster-whisper."""
        transcribe_options = {
            "language": self.language,
            "task": "transcribe",
            "beam_size": 5,
            "best_of": 5,
            "word_timestamps": True,
            "vad_filter": True,
            "condition_on_previous_text": False,
        }
        transcribe_options.update(kwargs)

        segments_iter, info = self._model.transcribe(
            str(audio_path),
            **transcribe_options
        )

        segments = []
        full_text_parts = []
        for seg in segments_iter:
            text = (getattr(seg, "text", "") or "").strip()
            segment = {
                "id": getattr(seg, "id", None),
                "start": float(getattr(seg, "start", 0.0) or 0.0),
                "end": float(getattr(seg, "end", 0.0) or 0.0),
                "text": text,
                "words": [],
            }
            words = getattr(seg, "words", None) or []
            for w in words:
                w_start = getattr(w, "start", None)
                w_end = getattr(w, "end", None)
                segment["words"].append({
                    "word": (getattr(w, "word", "") or "").strip(),
                    "start": float(w_start if w_start is not None else segment["start"]),
                    "end": float(w_end if w_end is not None else segment["end"]),
                    "probability": getattr(w, "probability", None),
                })
            segments.append(segment)
            if text:
                full_text_parts.append(text)

        return {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "text": " ".join(full_text_parts).strip(),
            "segments": segments,
        }
    
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
        """Transcribe standard whisper, bat word_timestamps de split chinh xac."""
        import whisper

        transcribe_options = {
            "language": self.language,
            "task": "transcribe",
            "verbose": False,
            "word_timestamps": True,   # lay timestamp tung tu
        }
        transcribe_options.update(kwargs)

        result = self._model.transcribe(
            str(audio_path),
            **transcribe_options
        )

        return result

    # ── SRT Writing ───────────────────────────────────────────────────────────

    def _write_srt(self, result: Dict[str, Any], output_path: Path) -> None:
        """
        Tao file SRT chia theo dau cau, moi entry toi da 8 giay.

        Thuat toan (uu tien theo thu tu):
          1. Tich luy tu cho den khi gap dau cau ket thuc (.!?) -> tao entry
          2. Neu chua het cau ma da >= 8s -> break tai dau ,; hoac word boundary
          3. Moi entry = 1 cau hoan chinh (hoac sat 8s)
        """
        import re

        MAX_DUR = 5.0   # capcut-like: cue ngan, doc nhanh
        MIN_DUR = 0.7
        MAX_WORDS = 12
        MAX_CHARS = 48

        # Regex kiem tra dau ket thuc cau
        RE_SENTENCE_END = re.compile(r'[.!?…]+$')    # dau ket thuc cau
        RE_CLAUSE_END   = re.compile(r'[,;]+$')      # dau ngu phap nhe hon

        def _ts(s: float) -> str:
            return self._seconds_to_srt_time(s)

        def _to_two_lines(text: str, max_len: int = MAX_CHARS) -> str:
            words = text.split()
            if not words:
                return text
            if len(text) <= max_len:
                return text

            best_i = None
            best_score = None
            for i in range(1, len(words)):
                l1 = " ".join(words[:i])
                l2 = " ".join(words[i:])
                score = max(len(l1), len(l2))
                penalty = 0
                if len(l1) > max_len:
                    penalty += (len(l1) - max_len) * 10
                if len(l2) > max_len:
                    penalty += (len(l2) - max_len) * 10
                score += penalty
                if best_score is None or score < best_score:
                    best_score = score
                    best_i = i

            if best_i is None:
                return text
            line1 = " ".join(words[:best_i]).strip()
            line2 = " ".join(words[best_i:]).strip()
            if not line1 or not line2:
                return text
            return f"{line1}\n{line2}"

        # ── Thu nhap danh sach tu co timestamp ───────────────────────────────
        # word = {"word": str, "start": float, "end": float}
        all_words = []

        segments = result.get("segments", [])
        for seg in segments:
            words = seg.get("words", [])
            if words:
                # word_timestamps=True: moi tu co start/end
                for w in words:
                    txt = w.get("word", "").strip()
                    if not txt:
                        continue
                    all_words.append({
                        "word":  txt,
                        "start": float(w.get("start", seg.get("start", 0))),
                        "end":   float(w.get("end",   seg.get("end",   0))),
                    })
            else:
                # Khong co word timestamps (whisper_timestamped tra ve khac)
                # Phan bo deu theo character count
                raw = seg.get("text", "").strip()
                if not raw:
                    continue
                seg_s = float(seg.get("start", 0))
                seg_e = float(seg.get("end",   0))
                seg_d = seg_e - seg_s
                ws = raw.split()
                for i, w in enumerate(ws):
                    frac_s = (i     / len(ws)) * seg_d + seg_s
                    frac_e = ((i+1) / len(ws)) * seg_d + seg_s
                    all_words.append({"word": w, "start": frac_s, "end": frac_e})

        if not all_words:
            # Fallback: dung segment-level chia theo ky tu
            self._write_srt_fallback(result, output_path)
            self._write_txt(result, output_path)
            return

        # ── Chia thanh cac SRT entries ────────────────────────────────────────
        srt_blocks = []
        idx = 1

        buf_words  = []   # danh sach tu dang tich luy
        buf_start  = None
        buf_end    = None

        def flush(words, t_start, t_end):
            nonlocal idx
            if not words:
                return
            text = " ".join(w["word"] for w in words).strip()
            # Loai bo khoang trang thua truoc dau cau
            text = re.sub(r'\s+([.,!?;:])', r'\1', text)
            text = _to_two_lines(text, MAX_CHARS)
            dur  = max(MIN_DUR, t_end - t_start)
            srt_blocks.append({
                "start": t_start,
                "end": t_start + dur,
                "text": text,
            })
            idx += 1

        last_clause_idx  = None   # vi tri cuoi cung co dau ,;
        last_clause_end  = None   # end time tai vi tri do

        for wi, w in enumerate(all_words):
            if buf_start is None:
                buf_start = w["start"]

            buf_words.append(w)
            buf_end = w["end"]

            cur_dur = buf_end - buf_start
            word_text = w["word"].rstrip()

            # Ghi lai vi tri dau ngu phap (,;) goa nhat de dung khi can force-break
            if RE_CLAUSE_END.search(word_text):
                last_clause_idx  = len(buf_words) - 1
                last_clause_end  = buf_end

            # Uu tien 1: force break khi qua nguong (duration/words/chars).
            current_text = " ".join(x["word"] for x in buf_words)
            must_break = (
                cur_dur >= MAX_DUR or
                len(buf_words) >= MAX_WORDS or
                len(current_text) >= MAX_CHARS * 2
            )
            if must_break:
                if last_clause_idx is not None and last_clause_idx < len(buf_words) - 1:
                    # Break tai dau ,; cuoi cung
                    keep  = buf_words[:last_clause_idx + 1]
                    carry = buf_words[last_clause_idx + 1:]
                    flush(keep, buf_start, last_clause_end)
                    buf_words  = carry
                    buf_start  = carry[0]["start"] if carry else None
                    buf_end    = carry[-1]["end"]   if carry else None
                else:
                    # Khong co dau ,; -> break truoc tu hien tai neu co the.
                    if len(buf_words) > 1:
                        keep = buf_words[:-1]
                        carry = [buf_words[-1]]
                        flush(keep, buf_start, keep[-1]["end"])
                        buf_words = carry
                        buf_start = carry[0]["start"]
                        buf_end = carry[-1]["end"]
                    else:
                        flush(buf_words, buf_start, buf_end)
                        buf_words, buf_start, buf_end = [], None, None
                last_clause_idx = None
                last_clause_end = None
                continue

            # Uu tien 2: dau ket thuc cau (.!?) -> flush khi da dat toi thieu
            if RE_SENTENCE_END.search(word_text) and cur_dur >= MIN_DUR:
                flush(buf_words, buf_start, buf_end)
                buf_words, buf_start, buf_end = [], None, None
                last_clause_idx  = None
                last_clause_end  = None
                continue

        # Flush phan con lai
        if buf_words and buf_start is not None:
            flush(buf_words, buf_start, buf_end)

        def _polish_blocks(blocks):
            if not blocks:
                return blocks

            merged = []
            for b in blocks:
                if merged:
                    prev = merged[-1]
                    gap = b["start"] - prev["end"]
                    dur_b = b["end"] - b["start"]
                    words_b = len(b["text"].replace("\n", " ").split())
                    is_tiny_tail = dur_b < 0.9 and words_b <= 3
                    if is_tiny_tail and gap <= 0.18 and (b["end"] - prev["start"]) <= (MAX_DUR + 0.6):
                        joined = (prev["text"].replace("\n", " ") + " " + b["text"].replace("\n", " ")).strip()
                        joined = re.sub(r"\s+([.,!?;:])", r"\1", joined)
                        prev["text"] = _to_two_lines(joined, MAX_CHARS)
                        prev["end"] = max(prev["end"], b["end"])
                        continue
                merged.append(dict(b))

            HOLD_SEC = 0.10
            MIN_GAP = 0.03
            for i in range(len(merged) - 1):
                cur = merged[i]
                nxt = merged[i + 1]
                desired_end = min(cur["end"] + HOLD_SEC, nxt["start"] - MIN_GAP)
                cur["end"] = max(cur["start"] + MIN_DUR, desired_end)
                min_next_start = cur["end"] + MIN_GAP
                if nxt["start"] < min_next_start:
                    shift = min_next_start - nxt["start"]
                    nxt["start"] += shift
                    nxt["end"] = max(nxt["end"] + shift, nxt["start"] + MIN_DUR)

            for b in merged:
                b["end"] = max(b["end"], b["start"] + MIN_DUR)
            return merged

        srt_blocks = _polish_blocks(srt_blocks)

        with open(output_path, "w", encoding="utf-8") as f:
            for i, b in enumerate(srt_blocks, start=1):
                f.write(f"{i}\n{_ts(b['start'])} --> {_ts(b['end'])}\n{b['text']}\n\n")

        self._write_txt(result, output_path)

    def _write_srt_fallback(self, result: Dict[str, Any], output_path: Path) -> None:
        """
        Fallback khi khong co word timestamps.
        Chia segment theo dau cau, max 8s moi entry.
        """
        import re
        MAX_DUR = 5.0
        MIN_DUR = 0.7

        RE_SENT = re.compile(r'(?<=[.!?])\s+')

        segments = result.get("segments", [])
        srt_blocks = []
        idx = 1

        for seg in segments:
            raw = seg.get("text", "").strip()
            if not raw:
                continue
            seg_s = float(seg.get("start", 0))
            seg_e = float(seg.get("end",   0))
            seg_d = seg_e - seg_s

            # Tach thanh cac cau
            sentences = [s.strip() for s in RE_SENT.split(raw) if s.strip()]
            if not sentences:
                continue

            total_chars = sum(len(s) for s in sentences) or 1
            cur_time = seg_s

            for sent in sentences:
                sent_dur = seg_d * (len(sent) / total_chars)
                sent_dur = max(MIN_DUR, min(MAX_DUR, sent_dur))
                sent_end = min(cur_time + sent_dur, seg_e)

                srt_blocks.append(
                    f"{idx}\n{self._seconds_to_srt_time(cur_time)} --> {self._seconds_to_srt_time(sent_end)}\n{sent}\n"
                )
                idx += 1
                cur_time = sent_end

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_blocks))

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
