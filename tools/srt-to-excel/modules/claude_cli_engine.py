"""
SRT to Excel - Claude Code CLI Engine (agentic, simple)
=======================================================
A simple, "2-prompt" style engine: instead of a multi-step DeepSeek/VOV API
pipeline, we hand the work to the locally installed **Claude Code CLI**
(`claude`), which the user is already logged into.

How it works (one agentic call, like using Claude in the IDE):
  1. Resolve the channel's reference character image + a SHORT visual style from
     reference_characters/<topic>/<channel>/style.yaml  (authoritative source:
     the project's .nguon_runtime_metadata.yaml when present).
  2. Build ONE instruction (the user's storyboard rules) and run `claude -p`.
     Claude reads the indexed SRT, divides it into scenes by meaning, and WRITES
     its result to a file `{code}_scenes.jsonl` (one scene per line). Because it
     writes to a file across as many turns as it needs, long videos are not
     truncated by the single-response token limit — no manual batching needed.
  3. We read that file and build the VE3-compatible `{code}_prompts.xlsx`
     (`scenes` sheet) via the existing excel_manager.PromptWorkbook, computing
     the authoritative timecodes/duration from the SRT entries (never trusting
     the model's arithmetic). The rest of the suite keeps working unchanged.

The main character is referred to ONLY as "the reference character" — its
identity AND look come from the nv1.png reference image attached at
image-generation time, so prompts never describe its appearance.
"""

import sys
import os
import json
import time
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Tuple

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        if _s and hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

try:
    import yaml
except ImportError:
    yaml = None

from modules.utils import parse_srt_file, format_srt_time, get_logger
from modules.excel_manager import PromptWorkbook, Character, Scene


TOOL_DIR = Path(__file__).resolve().parents[1]

# Vietnamese topic label -> reference_characters subdir
TOPIC_DIR_MAP = {
    "tam ly": "psychology",
    "psychology": "psychology",
    "tai chinh": "finance",
    "finance": "finance",
    "phat trien ban than": "success",
    "success": "success",
}

PREFIX_TOPIC_MAP = {
    "TL": "psychology",
    "TH": "finance",
    "MT": "success",
}


def _normalize_topic(value: str) -> str:
    import unicodedata
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("_", " ").replace("-", " ")
    return " ".join(text.split())


class ClaudeCliEngine:
    """Generate a VE3 prompts workbook from an SRT using the Claude Code CLI."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.logger = get_logger("claude_cli_engine")
        self.log_callback: Optional[Callable] = None

        self.claude_path = self._resolve_claude_path(
            str(self.config.get("claude_cli_path", "") or "").strip()
        )
        self.model = str(self.config.get("claude_cli_model", "") or "").strip()
        self.permission_mode = str(
            self.config.get("claude_cli_permission_mode", "") or "acceptEdits"
        ).strip() or "acceptEdits"
        try:
            self.timeout_seconds = int(self.config.get("claude_cli_timeout_seconds", 1800) or 1800)
        except Exception:
            self.timeout_seconds = 1800

        try:
            # Claude engine targets 3-8s scenes (content-first), decoupled from the
            # API pipeline's min_scene_duration. Override with claude_cli_min/max_scene.
            self.min_dur = float(self.config.get("claude_cli_min_scene", 3) or 3)
        except Exception:
            self.min_dur = 3.0
        try:
            self.max_dur = float(self.config.get("claude_cli_max_scene", 8) or 8)
        except Exception:
            self.max_dur = 8.0
        # HARD ceiling for Veo3: a generated video clip is max ~8s, so NO scene
        # may exceed max_dur. Shorter scenes are fine (down to ~min_dur).
        self.split_ceiling = self.max_dur
        # A separate review/QA pass (mirrors the manual "2nd prompt") — on by default.
        self.review_enabled = bool(self.config.get("claude_cli_review", True))
        # Long SRTs can't be done in ONE Claude call (one output is too big and slow).
        # Like the VS Code flow (which builds the file gradually), we split a long SRT
        # into batches, generate each, and merge — fast + no truncation.
        try:
            self.chunk_threshold = int(self.config.get("claude_cli_chunk_threshold", 100) or 100)
        except Exception:
            self.chunk_threshold = 100
        try:
            self.chunk_size = int(self.config.get("claude_cli_chunk_size", 55) or 55)
        except Exception:
            self.chunk_size = 55
        # Chunks of one video run in PARALLEL (each its own claude.exe) so a long
        # video finishes ~3x faster. Capped low so total concurrent claude stays
        # safe even when the queue runs 2 codes at once.
        try:
            self.chunk_parallel = max(1, int(self.config.get("claude_cli_chunk_parallel", 3) or 3))
        except Exception:
            self.chunk_parallel = 3
        # Each chunk retries on failure (rate-limit / timeout) with backoff so the
        # parallelism never loses a chunk to a transient Max throttle.
        try:
            self.chunk_retries = max(0, int(self.config.get("claude_cli_chunk_retries", 2) or 2))
        except Exception:
            self.chunk_retries = 2

    @staticmethod
    def _neg_tail() -> str:
        """Short negative clause appended to every prompt (Veo3 tends to drift to
        realism, so we explicitly forbid it + any text)."""
        return ("no real people, no photorealism, no 3D render, "
                "no text, no letters, no numbers, no watermark")

    # ------------------------------------------------------------------ utils
    def _log(self, msg: str, level: str = "INFO"):
        if self.log_callback:
            try:
                self.log_callback(msg, level)
            except Exception:
                pass
        try:
            self.logger.log(
                {"ERROR": 40, "WARN": 30, "WARNING": 30, "SUCCESS": 20}.get(level, 20),
                msg,
            )
        except Exception:
            pass

    @staticmethod
    def _resolve_claude_path(explicit: str) -> str:
        if explicit:
            return explicit
        found = shutil.which("claude")
        if found:
            return found
        guess = Path(os.path.expanduser("~")) / ".local" / "bin" / "claude.exe"
        if guess.exists():
            return str(guess)
        return "claude"

    # -------------------------------------------------------------- style load
    def _read_metadata(self, project_dir: Path) -> Dict[str, Any]:
        meta_path = project_dir / ".nguon_runtime_metadata.yaml"
        if meta_path.exists() and yaml:
            try:
                return yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except Exception as e:
                self._log(f"  [WARN] Cannot read metadata: {e}", "WARN")
        return {}

    def _resolve_topic_and_channel(self, project_dir: Path, code: str) -> Tuple[str, str, str]:
        """Return (topic_dir, channel, reference_image_path)."""
        meta = self._read_metadata(project_dir)

        topic_raw = meta.get("topic") or self.config.get("topic") or ""
        topic_dir = TOPIC_DIR_MAP.get(_normalize_topic(topic_raw), "")
        if not topic_dir:
            import re
            m = re.match(r"^([A-Za-z]+)", str(code or "").strip())
            if m:
                topic_dir = PREFIX_TOPIC_MAP.get(m.group(1).upper(), "")

        channel = str(
            meta.get("reference_channel")
            or self.config.get("reference_channel")
            or self.config.get("channel")
            or ""
        ).strip()
        if not channel:
            channel = self._guess_channel(code, topic_dir)

        ref_img = str(meta.get("psychology_reference_image") or "").strip()
        if not ref_img and topic_dir and channel:
            guess = TOOL_DIR / "reference_characters" / topic_dir / channel / "nv1.png"
            if guess.exists():
                ref_img = str(guess)
        return topic_dir, channel, ref_img

    def _guess_channel(self, code: str, topic_dir: str) -> str:
        import re
        if not topic_dir:
            return ""
        root = TOOL_DIR / "reference_characters" / topic_dir
        candidates = []
        m = re.match(r"^([A-Za-z]+\d+)-0*(\d+)$", str(code or "").strip(), flags=re.IGNORECASE)
        if m:
            candidates.append(f"{m.group(1).upper()}-T{int(m.group(2))}")
        candidates.append(str(code or "").strip())
        for cand in candidates:
            if cand and (root / cand / "style.yaml").exists():
                return cand
        return ""

    def _load_style(self, topic_dir: str, channel: str) -> Dict[str, Any]:
        if not (topic_dir and channel and yaml):
            return {}
        path = TOOL_DIR / "reference_characters" / topic_dir / channel / "style.yaml"
        if not path.exists():
            self._log(f"  [WARN] style.yaml not found: {path}", "WARN")
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            self._log(f"  -> Style: {data.get('style_name', channel)} ({path})")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self._log(f"  [WARN] Cannot parse style.yaml: {e}", "WARN")
            return {}

    def _short_style(self, style: Dict[str, Any]) -> Tuple[str, str, str, str]:
        """Return (art_look, palette, negative, audience_language) — SHORT, with
        NO main-character description (identity AND look come from the attached
        reference image). We deliberately do NOT inject style_name/image_style,
        because those fields often embed the character's body/clothes."""
        palette = str(style.get("palette", "") or "").strip()
        negative = str(style.get("negative_prompt", "") or "").strip() or (
            "no real humans, no photorealism, no 3D, no text, no letters, no numbers, no watermark"
        )
        audience = str(style.get("audience_language", "") or "Vietnamese").strip() or "Vietnamese"
        look = ("flat 2D illustrated cartoon in the SAME art style as the attached "
                "reference image (do not invent a new look; no photoreal, no 3D)")
        return look, palette, negative, audience

    # ------------------------------------------------------------ prompt build
    def _build_indexed_srt(self, srt_entries: list) -> str:
        lines = []
        for e in srt_entries:
            start_s = e.start_time.total_seconds()
            end_s = e.end_time.total_seconds()
            text = " ".join(str(e.text or "").split())
            lines.append(
                f"[{e.index}] {start_s:.2f}s-{end_s:.2f}s ({e.duration:.1f}s): {text}"
            )
        return "\n".join(lines)

    def _build_prompt(self, code: str, srt_entries: list, style: Dict[str, Any], out_file: str,
                      topic: str = "psychology", include_thumbnails: bool = True) -> str:
        look, palette, negative, audience = self._short_style(style)
        first_index = srt_entries[0].index if srt_entries else 1
        last_index = srt_entries[-1].index if srt_entries else 0
        total_dur = srt_entries[-1].end_time.total_seconds() if srt_entries else 0
        indexed_srt = self._build_indexed_srt(srt_entries)
        neg_tail = self._neg_tail()
        # Thumbnail style (per-channel) — thumbnails DO carry a short hook text.
        # Ported verbatim from Option 1's _generate_psychology_thumbnails so the
        # per-channel text styling is identical across all channels.
        thumb_style = str(style.get("thumbnail_style", "") or "").strip() or look
        tt_style = str(style.get("thumb_text_style", "") or "").strip()
        tt_shadow = str(style.get("thumb_text_shadow", "") or "").strip()
        tt_font = str(style.get("thumb_text_font", "") or "").strip() or "bold condensed font (Anton / Bebas Neue style)"
        if not tt_style:
            _ts = thumb_style.lower()
            _mono = ("pencil" in _ts and "white paper" in _ts) or ("chalk" in _ts and "blackboard" in _ts)
            if _mono:
                tt_style = "white text on vivid red blocks (#FF3B30)"
                tt_shadow = tt_shadow or "subtle depth shadow on red blocks only"
            else:
                tt_style = "black text on vivid yellow blocks (#FFD400)"
                tt_shadow = tt_shadow or "subtle depth shadow on yellow blocks only"
        # Niche triggers by topic (psychology / finance / success), like Option 1.
        if topic in ("finance",):
            niche_desc = "financial history and economics education"
            niche_triggers = ('- revelation ("I didn\'t know that about money")\n'
                              '- contrarian shock ("everything you believed is WRONG")\n'
                              '- hidden power dynamics ("who really controls this?")\n'
                              '- data surprise ("these numbers don\'t add up")')
        elif topic in ("success",):
            niche_desc = "success mindset and self-improvement"
            niche_triggers = ('- aspiration gap ("they have what I want")\n'
                              '- uncomfortable truth ("the real reason you\'re stuck")\n'
                              '- before/after contrast ("the invisible shift")\n'
                              '- secret knowledge ("the one thing nobody tells you")')
        else:
            niche_desc = "psychology, human behavior, and emotional content"
            niche_triggers = ('- curiosity ("what\'s happening here?")\n'
                              '- emotional discomfort ("I\'ve felt this")\n'
                              '- recognition ("that\'s me!")\n'
                              '- social judgment ("why are they looking at them like that?")\n'
                              '- hidden truth ("there\'s something deeper here")')
        neg_tail_thumb = ("no real people, no photorealism, no 3D render, "
                          "no extra text except the requested thumbnail hook text, no watermark")

        # Thumbnails are only needed ONCE per video — generated on the first batch.
        # Later batches skip them (scenes only) to stay fast.
        thumb_intro = ""
        thumb_section = ""
        thumb_json = ""
        thumb_rules = ""
        if include_thumbnails:
            thumb_intro = " plus 3 YouTube thumbnail prompts"
            thumb_json = (',\n  "thumbnails": [\n'
                          '    {"version_desc": "portrait_main", "img_prompt": "<full thumbnail prompt in the THUMBNAILS example format, real newlines inside>"},\n'
                          '    {"version_desc": "dramatic_scene", "img_prompt": "..."},\n'
                          '    {"version_desc": "youtube_ctr", "img_prompt": "..."}\n'
                          '  ]')
            thumb_rules = f"- Exactly 3 thumbnails; each thumbnail img_prompt ends with: {neg_tail_thumb}\n"
            thumb_section = f"""THUMBNAILS — also write 3 YouTube thumbnail prompts (these DO carry a short text
overlay, unlike the scenes which have NO text). You are an elite YouTube thumbnail
prompt writer for {niche_desc} faceless channels. Goal is STOPPING THE SCROLL, not
beauty. High-CTR thumbnails in this niche trigger:
{niche_triggers}

Write 3 prompts (a DIFFERENT emotional concept each), named exactly:
"portrait_main", "dramatic_scene", "youtube_ctr". Write each in EXACTLY the format,
length and style of this PERFECT EXAMPLE (study it):
---
psychological youtube thumbnail designed for extremely high click-through-rate, emotional tension, visual curiosity, cinematic storytelling
use provided character reference, keep consistent branding style

scene composition:
the reference character standing still in foreground, visibly different from everyone else
surrounding figures in background slightly blurred, subtly judging, whispering, or staring
emotional atmosphere: feeling of being different, misunderstood, emotionally strong but isolated
expression/body language matters: slight discomfort, guarded posture, introspective mood, subtle emotional tension

visual psychology: create curiosity and emotional contradiction, viewer should instantly wonder "why are they different?"

composition: the reference character large (occupying 35-45% frame), asymmetrical framing, strong focus on face/body posture, background simplified
negative space reserved for text

TEXT STYLE (HIGH CTR YOUTUBE):
text: "<hook in {audience}>"

typography should feel emotionally charged, not flat
"<secondary words>" smaller, placed above left like a trigger word
"<MAIN WORD>" huge dominant word, partially cropped for impact
{tt_font}
{tt_style}
imperfect alignment for energy, slightly layered composition
{tt_shadow}
slight tilt for dynamism (2-4 degrees max)
text should integrate into composition, not float awkwardly

cinematic lighting, dramatic contrast, emotional storytelling, subtle vignette, eye-catching composition optimized for thumbnails
youtube thumbnail designed to trigger curiosity, emotional recognition, and controversy
aspect ratio 16:9, ultra sharp
{neg_tail_thumb}
---
ABSOLUTE RULES for the thumbnails:
- CHARACTER DESCRIPTION IS FORBIDDEN — refer to the main character ONLY as "the
  reference character"; never add physical descriptors. Describe only POSE,
  EMOTION, BODY LANGUAGE (guarded posture, slight discomfort, introspective mood).
- Use real NEWLINES between sections (not one giant paragraph); keep each section
  short and punchy (2-3 lines max).
- Each thumbnail must have a social conflict OR a visual surprise (shadow /
  reflection / split). Formula: character + emotional state + social/internal
  conflict + visual symbolism.
- The hook text is the ONLY text in the thumbnail. The MAIN word must be the
  emotionally strongest word (NEVER a grammar word like the/a/no/and/of). Use the
  channel art style: {thumb_style}.
- Keep the "TEXT STYLE (HIGH CTR YOUTUBE)" block EXACTLY as in the example (only
  change the hook text + word names), and end with: {neg_tail_thumb}
"""

        return f"""You are an expert video-script analyst and storyboard director.
Read the SRT below, divide it into scenes by MEANING, and write an image prompt +
a video prompt for each scene{thumb_intro}. Output the result
as JSON saved to a file (see OUTPUT). Do NOT write or run any Python.

THE REFERENCE CHARACTER (very important):
There is ONE recurring main character. Its reference image (nv1.png) is attached
separately at image-generation time, which already locks BOTH its identity and
its art look. So in EVERY prompt call the main character ONLY
"the reference character". NEVER describe its face, hair, skin, body, clothing
or colors — that fights the attached reference. Describe only its pose, gesture,
expression, action and position in the frame.

CHANNEL STYLE (for the scene, background, props and any secondary/supporting
figures — NOT for the main character's body):
- Art style: {look}
- Color palette: {palette}
- FORBIDDEN (must respect — absolutely NO TEXT anywhere): {negative}

PROCESSING RULES:
1. Each prompt sticks closely to the exact words/meaning of that narration,
   shown visually and concretely so the viewer instantly feels the meaning and
   keeps watching.
2. "The reference character" is always the centre. Secondary figures, background
   and props are simple, support the meaning, and use the channel style.
3. Do NOT repeat the same setting/background over and over — let the meaning of
   the content drive a fitting, varied setting for each scene.
4. There is NO TEXT, no letters, no words, no numbers in any image or video.
5. Image Prompt (English): scene + the reference character's pose/expression/
   action + space + lighting + camera angle + channel style. NO TEXT. Refer to
   the main character only as "the reference character".
6. Video Prompt (English): only the motion/action. The reference character does
   NOT change — only expression and movement change. NO TEXT.
7. Every image and video prompt must be UNIQUE.
8. End EVERY image prompt AND EVERY video prompt with exactly this tail (so the
   render stays a 2D illustration and never drifts to realism or text):
   "{neg_tail}"

SCENE DIVISION (use the real timecodes shown in the SRT):
- DIVIDE BY COMPLETE MEANING first — each scene is exactly one complete idea.
  NEVER cut in the middle of a sentence. This matters most.
- Target {self.min_dur:.0f}-{self.max_dur:.0f}s per scene. HARD LIMIT: never
  longer than {self.max_dur:.0f}s (a Veo3 clip is at most {self.max_dur:.0f}s) —
  if one idea runs longer, split it into consecutive scenes, each <= {self.max_dur:.0f}s.
- Keep scenes at least ~{self.min_dur:.0f}s by grouping short consecutive entries
  that belong to the same idea; a scene below {self.min_dur:.0f}s is only OK as a
  deliberate punchy one-liner or the final leftover tail (don't make many tiny ones).
- Scenes are contiguous and cover EVERY SRT entry from [{first_index}] to
  [{last_index}] with no gaps/overlaps: first scene starts at index {first_index},
  each next starts at previous last index + 1, last scene ends at [{last_index}].

The narration audience is {audience} — keep cultural/visual choices appropriate.

{thumb_section}
SRT (each line: [index] start-end (duration): text). Total ~{total_dur:.0f}s,
indices [{first_index}]..[{last_index}]:
------------------------------------------------------------
{indexed_srt}
------------------------------------------------------------

OUTPUT — reply with ONE JSON object and NOTHING ELSE (no markdown, no code
fences, no prose, no tools, do not write any file). Your entire reply must start
with {{ and end with }}:
{{
  "scenes": [
    {{"first": <int first SRT index>, "last": <int last SRT index>, "img_prompt": "<English scene image prompt, 'the reference character', NO TEXT>", "video_prompt": "<English motion prompt, NO TEXT>", "note": "<short Vietnamese note>"}}
    // one object per scene, in order, contiguous, covering indices {first_index}..{last_index}
  ]{thumb_json}
}}

JSON RULES:
- End EVERY scene img_prompt and video_prompt with: {neg_tail}
- "scenes" must be contiguous and cover every SRT index from {first_index} to
  {last_index} (first scene "first"={first_index}; each next "first" = previous
  "last" + 1; last scene "last"={last_index}); NO scene longer than {self.max_dur:.0f}s.
- Do NOT include timecodes, durations or srt_text — the tool computes those from
  the SRT indices. Only the creative fields shown above.
{thumb_rules}- Output strictly valid JSON (no comments, no trailing commas; escape any quotes
  and newlines inside strings as \\" and \\n). Your WHOLE reply is just this JSON.
"""

    @staticmethod
    def _kill_proc_tree(proc) -> None:
        """Kill the claude.exe process AND all its children (so nothing orphans)."""
        if proc is None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=8)
        except Exception:
            pass

    # ------------------------------------------------------------- claude call
    def _run_claude(self, prompt: str, cwd: Path) -> str:
        # Claude only writes a JSON data file (no code execution). acceptEdits
        # auto-approves the Write tool in headless mode, so we do NOT need the
        # dangerous skip — safer (Claude never runs anything).
        cmd = [self.claude_path, "-p", "--output-format", "json",
               "--permission-mode", "acceptEdits"]
        if self.model:
            cmd += ["--model", self.model]

        self._log(f"  -> Calling Claude CLI ({os.path.basename(self.claude_path)}, "
                  f"model={self.model or 'default'})...")
        self._log("     (Claude is reading the SRT and writing scene data — a few minutes)")

        stop_heartbeat = threading.Event()

        def _heartbeat():
            t0 = time.time()
            while not stop_heartbeat.wait(20):
                self._log(f"  ... Claude still working ({int(time.time() - t0)}s)")

        # Visible window (so the user sees it working) + its OWN process group so
        # we can kill the WHOLE claude.exe tree on timeout/error — never orphan it.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        hb = threading.Thread(target=_heartbeat, daemon=True)
        hb.start()
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
        try:
            out, err = proc.communicate(input=prompt, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill_proc_tree(proc)
            raise RuntimeError(f"Claude CLI timeout after {self.timeout_seconds}s")
        except BaseException:
            self._kill_proc_tree(proc)
            raise
        finally:
            stop_heartbeat.set()

        if proc.returncode != 0:
            self._kill_proc_tree(proc)  # ensure no surviving children on error
            msg = (err or out or "").strip()
            raise RuntimeError(f"Claude CLI exited {proc.returncode}: {msg[:500]}")

        out = (out or "").strip()
        if not out:
            raise RuntimeError("Claude CLI returned empty output.")

        # --output-format json wraps the answer: {"type":"result","result":"..."}
        try:
            wrapper = json.loads(out)
            if isinstance(wrapper, dict):
                if wrapper.get("is_error"):
                    raise RuntimeError(f"Claude reported error: {str(wrapper.get('result'))[:400]}")
                usage = wrapper.get("usage") or {}
                cost = wrapper.get("total_cost_usd")
                self._log(f"  -> Claude finished (in={usage.get('input_tokens','?')} "
                          f"out={usage.get('output_tokens','?')} tok"
                          f"{', ~$' + format(cost, '.3f') if isinstance(cost, (int, float)) else ''})")
                return str(wrapper.get("result", ""))
        except json.JSONDecodeError:
            pass
        return out

    def _build_review_prompt(self, code: str, data: dict) -> str:
        """Second QA pass (data-based, stdout) — mirrors the manual '2nd prompt'."""
        cur = json.dumps(data, ensure_ascii=False)
        return f"""Below is a JSON storyboard you produced for the video `{code}`.
REVIEW it like a strict editor, FIX every problem, and reply with the CORRECTED
JSON only (same shape: {{"scenes":[...], "thumbnails":[...]}}). For timecodes you
may re-read `{code}.srt`.

Fix:
1. TIMING: each scene {self.min_dur:.0f}-{self.max_dur:.0f}s, NEVER longer than
   {self.max_dur:.0f}s. MERGE consecutive too-short scenes (< {self.min_dur:.0f}s)
   into a neighbour when the combined length stays <= {self.max_dur:.0f}s and they
   share the same idea.
2. NO mid-sentence cuts — each scene is one complete idea.
3. CONTENT FIDELITY: each image & video prompt sticks closely to that scene's
   narration meaning, concrete and vivid; strengthen any vague/generic prompt.
4. NO TEXT in any scene (only the 3 thumbnails carry their hook text).
5. STYLE: flat 2D illustration, NO real humans/photoreal; main character ONLY
   "the reference character".
6. NO duplicate / near-identical prompts; no repeated settings back-to-back.
7. Keep coverage contiguous (same index range) and keep the thumbnails.

CURRENT JSON:
{cur}

Reply with ONLY the corrected JSON object (start with {{ end with }}), no prose."""

    def _run_review_pass(self, project_dir: Path, code: str, data: dict) -> dict:
        self._log("  -> QA pass (ra soat lai nhu prompt thu cong #2)...")
        prompt = self._build_review_prompt(code, data)
        try:
            txt = self._run_claude(prompt, project_dir)
            obj = self._extract_json_obj(txt or "")
            if isinstance(obj, dict) and obj.get("scenes"):
                self._log(f"  -> QA: {len(obj.get('scenes', []))} scenes sau ra soat")
                return obj
        except Exception as e:
            self._log(f"  [WARN] QA pass failed (giu ban dau): {e}", "WARN")
        return {}

    # ---------------------------------------------------- read data + builder
    def _read_data_file(self, path: Path, result_text: str = "") -> dict:
        """Read the JSON {scenes, thumbnails} Claude wrote; fall back to stdout."""
        text = ""
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        for src in (text, result_text):
            if not src or not src.strip():
                continue
            obj = self._extract_json_obj(src)
            if isinstance(obj, dict) and isinstance(obj.get("scenes"), list):
                return obj
        return {}

    @staticmethod
    def _extract_json_obj(text: str):
        text = (text or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
            text = text.strip()
        for cand in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
            try:
                d = json.loads(cand)
                if isinstance(d, dict):
                    return d
            except Exception:
                continue
        return None

    def _build_workbook_from_data(self, excel_path: Path, data: dict, srt_entries: list,
                                  style: Dict[str, Any]) -> Tuple[int, int, list]:
        """Fixed deterministic builder: JSON data -> VE3 workbook (scenes +
        thumbnails + nv1 char). Timecodes computed from the SRT; schema guaranteed."""
        from modules.excel_manager import Thumbnail
        scenes, warnings = self._build_scenes(data.get("scenes") or [], srt_entries)
        wb = PromptWorkbook(excel_path).load_or_create()
        wb.clear_characters()
        char_lock = (style.get("reference_lock") or style.get("default_character_lock")
                     or "existing local reference image")
        wb.add_character(Character(id="nv1", name="Reference", role="protagonist",
                                   english_prompt=style.get("default_character_prompt", "") or "",
                                   vietnamese_prompt="", character_lock=char_lock,
                                   image_file="nv1.png", status="pending", is_child=False))
        wb.clear_scenes()
        for sc in scenes:
            wb.add_scene(sc)
        # Thumbnails
        wb.clear_thumbnails()
        thumbs = data.get("thumbnails") or []
        order = ["portrait_main", "dramatic_scene", "youtube_ctr"]
        n_thumb = 0
        for i, vdesc in enumerate(order, 1):
            t = next((x for x in thumbs if str(x.get("version_desc", "")).strip() == vdesc), None)
            if t is None and i - 1 < len(thumbs):
                t = thumbs[i - 1]
            img = str((t or {}).get("img_prompt", "") or "").strip()
            if not img:
                continue
            wb.add_thumbnail(Thumbnail(thumb_id=i, version_desc=vdesc, img_prompt=img,
                                       characters_used="nv1",
                                       reference_files=json.dumps(["nv1.png"]),
                                       status_img="pending", status_portrait="pending"))
            n_thumb += 1
        wb.save()
        return len(scenes), n_thumb, warnings

    def _split_range(self, first: int, last: int, by_index: dict) -> list:
        """Split an SRT index range [first,last] into contiguous sub-ranges whose
        SRT duration each stays <= max_dur (Veo3 clip ceiling). Cuts only on entry
        boundaries; a lone entry already longer than max_dur is kept as-is."""
        ranges: list = []
        seg_start = first
        i = first
        while i <= last:
            e_start, e_cur = by_index.get(seg_start), by_index.get(i)
            if not e_start or not e_cur:
                i += 1
                continue
            span = e_cur.end_time.total_seconds() - e_start.start_time.total_seconds()
            if span > self.max_dur and i > seg_start:
                # adding entry i overflows -> close segment at i-1, restart at i
                ranges.append((seg_start, i - 1))
                seg_start = i
                continue
            i += 1
        if seg_start <= last:
            ranges.append((seg_start, last))
        return ranges or [(first, last)]

    def _build_scenes(self, raw_scenes: list, srt_entries: list) -> Tuple[list, list]:
        """Map [{first,last,img_prompt,video_prompt,note}] -> contiguous Scene list
        with authoritative timecodes from the SRT."""
        by_index = {e.index: e for e in srt_entries}
        max_index = srt_entries[-1].index if srt_entries else 0
        warnings: list = []
        cleaned = []
        for raw in raw_scenes:
            try:
                first = int(raw.get("first")); last = int(raw.get("last"))
            except Exception:
                continue
            first = max(1, first)
            last = min(max_index, last)
            if last < first:
                last = first
            cleaned.append((first, last, raw))
        cleaned.sort(key=lambda x: x[0])
        scenes: list = []
        sid = 0
        expected = 1
        tail = self._neg_tail()
        for first, last, raw in cleaned:
            if first > expected:
                warnings.append(f"Gap before {first} (expected {expected}) — auto-filled")
                first = expected
            if first < expected:
                first = expected
                if last < first:
                    continue
            es, ee = by_index.get(first), by_index.get(last)
            if not es or not ee:
                continue
            img = str(raw.get("img_prompt", "") or "").strip()
            vid = str(raw.get("video_prompt", "") or "").strip()
            if img and "photoreal" not in img.lower():
                img = img.rstrip(" .,") + ", " + tail
            if vid and "photoreal" not in vid.lower():
                vid = vid.rstrip(" .,") + ", " + tail
            # SAFETY NET: a clip can't exceed max_dur (Veo3 ~8s). If Claude grouped
            # entries whose combined SRT length is too long, split it on entry edges
            # into <= max_dur sub-scenes (same idea/prompt — continuous action).
            for sf, sl in self._split_range(first, last, by_index):
                sub_es, sub_ee = by_index.get(sf), by_index.get(sl)
                if not sub_es or not sub_ee:
                    continue
                sid += 1
                srt_text = " ".join(" ".join(str(by_index[i].text or "").split())
                                    for i in range(sf, sl + 1) if i in by_index).strip()
                dur = round(sub_ee.end_time.total_seconds() - sub_es.start_time.total_seconds(), 1)
                scenes.append(Scene(
                    scene_id=sid, srt_start=format_srt_time(sub_es.start_time),
                    srt_end=format_srt_time(sub_ee.end_time), duration=dur, srt_text=srt_text,
                    img_prompt=img, video_prompt=vid, status_img="pending", status_vid="pending",
                    characters_used="nv1", location_used="", reference_files=json.dumps(["nv1.png"]),
                ))
            expected = last + 1
        if expected <= max_index:
            warnings.append(f"Coverage stops at {expected - 1}/{max_index}")
        return scenes, warnings

    # ----------------------------------------------------------- gather (chunk)
    def _chunk_entries(self, srt_entries: list) -> list:
        """Split entries into balanced batches of <= chunk_size (on entry edges)."""
        import math
        n = len(srt_entries)
        size = max(20, self.chunk_size)
        n_chunks = max(1, math.ceil(n / size))
        base = math.ceil(n / n_chunks)
        return [srt_entries[i:i + base] for i in range(0, n, base)]

    def _gather_data(self, project_dir: Path, code: str, srt_entries: list,
                     style: Dict[str, Any], topic: str, out_file: str) -> dict:
        """Single Claude call for short SRTs; sequential batched calls for long
        ones (merged). Mirrors the VS Code 'build the file gradually' behaviour."""
        n = len(srt_entries)
        if n <= self.chunk_threshold:
            prompt = self._build_prompt(code, srt_entries, style, out_file, topic)
            result = self._run_claude(prompt, project_dir)
            data = self._read_data_file(project_dir / out_file, result) or {}
            if self.review_enabled and data.get("scenes"):
                reviewed = self._run_review_pass(project_dir, code, data)
                if reviewed and reviewed.get("scenes"):
                    data = reviewed
            return data

        chunks = self._chunk_entries(srt_entries)
        n_par = min(self.chunk_parallel, len(chunks))
        self._log(f"  -> SRT dai ({n} cau) -> chia {len(chunks)} khuc "
                  f"(~{len(chunks[0])} cau/khuc), chay {n_par} khuc song song (nhanh + khong cat cut)")

        from concurrent.futures import ThreadPoolExecutor
        results: dict = {}
        with ThreadPoolExecutor(max_workers=n_par) as ex:
            fut_map = {
                ex.submit(self._run_chunk, project_dir, code, i, len(chunks),
                          chunk, style, topic): i
                for i, chunk in enumerate(chunks)
            }
            for fut in fut_map:
                i = fut_map[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    self._log(f"  [WARN] khuc {i + 1} loi: {e}", "WARN")
                    results[i] = {}

        # Merge in chunk order so the Scene list stays contiguous with the SRT.
        all_scenes: list = []
        thumbnails: list = []
        for i in range(len(chunks)):
            cdata = results.get(i) or {}
            cs = cdata.get("scenes") or []
            if i == 0:
                thumbnails = cdata.get("thumbnails") or []
            if not cs:
                self._log(f"  [WARN] khuc {i + 1} khong co scene -> bo qua", "WARN")
                continue
            all_scenes.extend(cs)
            self._log(f"  -> khuc {i + 1}: {len(cs)} scenes")
        self._log(f"  -> tong {len(all_scenes)} scenes tu {len(chunks)} khuc")
        return {"scenes": all_scenes, "thumbnails": thumbnails}

    def _run_chunk(self, project_dir: Path, code: str, i: int, n_chunks: int,
                   chunk: list, style: Dict[str, Any], topic: str) -> dict:
        """Generate ONE chunk's scenes (chunk 0 also writes thumbnails). Retries on
        a transient failure (rate-limit / timeout) with backoff so parallel chunks
        never silently lose data to a Max throttle."""
        import time
        cfile = f"{code}_scenes_{i + 1}.json"
        try:
            if (project_dir / cfile).exists():
                (project_dir / cfile).unlink()
        except Exception:
            pass
        self._log(f"  -> khuc {i + 1}/{n_chunks} [{chunk[0].index}-{chunk[-1].index}]"
                  f"{' (+thumbnail)' if i == 0 else ''} bat dau...")
        prompt = self._build_prompt(code, chunk, style, cfile, topic, include_thumbnails=(i == 0))
        last_err = ""
        for attempt in range(self.chunk_retries + 1):
            try:
                result = self._run_claude(prompt, project_dir)
                cdata = self._read_data_file(project_dir / cfile, result) or {}
                if cdata.get("scenes"):
                    return cdata
                last_err = "khong co scene"
            except Exception as e:
                last_err = str(e)
            if attempt < self.chunk_retries:
                wait = 15 * (attempt + 1)
                self._log(f"  [WARN] khuc {i + 1} that bai ({last_err}) -> "
                          f"thu lai sau {wait}s ({attempt + 1}/{self.chunk_retries})", "WARN")
                time.sleep(wait)
        self._log(f"  [WARN] khuc {i + 1} bo cuoc sau {self.chunk_retries + 1} lan: {last_err}", "WARN")
        return {}

    # --------------------------------------------------------------------- run
    def run(
        self,
        project_dir: Path,
        code: str,
        log_callback: Callable = None,
        step_callback: Callable = None,
    ) -> bool:
        self.log_callback = log_callback
        project_dir = Path(project_dir)

        def _step(idx, status, msg=""):
            if step_callback:
                try:
                    step_callback(idx, status, msg)
                except Exception:
                    pass

        self._log("\n" + "=" * 70)
        self._log("  CLAUDE CODE CLI ENGINE (agentic)")
        self._log("  SRT -> Excel: Claude tu doc SRT va tao scenes, Python dung Excel")
        self._log("=" * 70)

        srt_path = project_dir / f"{code}.srt"
        excel_path = project_dir / f"{code}_prompts.xlsx"
        if not srt_path.exists():
            self._log(f"ERROR: SRT not found: {srt_path}", "ERROR")
            return False

        srt_entries = parse_srt_file(srt_path)
        if not srt_entries:
            self._log("ERROR: No SRT entries found!", "ERROR")
            return False
        self._log(f"  SRT: {len(srt_entries)} entries")

        # Step 1: resolve style
        _step(1, "running", "Resolve nhan vat + style theo kenh...")
        topic_dir, channel, ref_img = self._resolve_topic_and_channel(project_dir, code)
        self._log(f"  -> topic={topic_dir or '?'}  channel={channel or '?'}")
        if ref_img:
            self._log(f"  -> reference image: {ref_img}")
        style = self._load_style(topic_dir, channel)
        if not style:
            self._log("  [WARN] No style.yaml resolved — using generic illustrated defaults.", "WARN")
        _step(1, "done")

        # Step 2: Claude writes scene+thumbnail data as JSON (no code execution).
        _step(2, "running", "Claude doc SRT + viet data canh (JSON)...")
        out_file = f"{code}_scenes.json"
        for stale in (excel_path,
                      excel_path.with_suffix(".xlsx.lock"),
                      excel_path.with_suffix(".xlsx.tmp"),
                      excel_path.with_suffix(".xlsx.bak"),
                      project_dir / out_file):
            try:
                if stale.exists():
                    stale.unlink()
            except Exception:
                pass
        try:
            data = self._gather_data(project_dir, code, srt_entries, style,
                                     topic_dir or "psychology", out_file)
        except Exception as e:
            self._log(f"ERROR: Claude CLI failed: {e}", "ERROR")
            _step(2, "error", "Claude CLI failed")
            return False
        _step(2, "done")

        # Step 3: validate the data
        _step(3, "running", "Doc data canh...")
        if not data or not data.get("scenes"):
            self._log("ERROR: Claude khong tao duoc data canh hop le.", "ERROR")
            _step(3, "error", "No data")
            return False
        self._log(f"  -> data: {len(data.get('scenes', []))} scenes, "
                  f"{len(data.get('thumbnails', []))} thumbnails")
        _step(3, "done")

        # Step 4: fixed Python builder -> VE3 workbook (schema + timecodes guaranteed)
        _step(4, "running", "Dung Excel (builder co dinh)...")
        try:
            n_scene, n_thumb, warnings = self._build_workbook_from_data(
                excel_path, data, srt_entries, style)
        except Exception as e:
            self._log(f"ERROR: build Excel that bai: {e}", "ERROR")
            _step(4, "error", "Build failed")
            return False
        if not n_scene:
            self._log("ERROR: khong co scene hop le.", "ERROR")
            _step(4, "error", "No scenes")
            return False
        for w in warnings[:10]:
            self._log(f"  [WARN] {w}", "WARN")
        wb = PromptWorkbook(excel_path).load_or_create()
        scenes = wb.get_scenes()
        self._verify_workbook(wb, scenes, srt_entries)
        if n_thumb != 3:
            self._log(f"  [WARN] {n_thumb} thumbnail (mong doi 3)", "WARN")
        _step(4, "done")
        _step(5, "done")

        self._log("\n" + "=" * 70)
        self._log(f"  DONE — {len(scenes)} scenes -> {excel_path.name}", "SUCCESS")
        self._log("=" * 70)
        for i in (6, 7):
            _step(i, "done")
        return True

    def _verify_workbook(self, wb: "PromptWorkbook", scenes: list, srt_entries: list) -> None:
        """Light sanity checks + normalize on the workbook Claude produced."""
        last_end = srt_entries[-1].end_time.total_seconds() if srt_entries else 0
        changed = False
        # Mark all processing steps COMPLETED so downstream excel_is_usable() (which
        # requires completion_pct == 100) accepts this workbook, like the API pipeline.
        try:
            n = len(scenes)
            for i in range(1, 8):
                wb.update_step_status(f"step_{i}", "COMPLETED", n, n, "claude_cli engine")
            changed = True
        except Exception as e:
            self._log(f"  [WARN] cannot mark processing_status complete: {e}", "WARN")
        # SRT coverage 100% (best-effort; not required by excel_is_usable)
        try:
            if hasattr(wb, "update_srt_coverage_scenes"):
                dp = wb.get_director_plan() if hasattr(wb, "get_director_plan") else []
                if dp:
                    wb.update_srt_coverage_scenes(dp)
                    changed = True
        except Exception:
            pass
        # Ensure nv1 reference character exists
        try:
            char_ids = {str(getattr(c, "id", "")).lower() for c in wb.get_characters()}
            if "nv1" not in char_ids:
                wb.add_character(Character(id="nv1", name="Reference", role="protagonist",
                                           character_lock="", image_file="nv1.png",
                                           status="pending", is_child=False))
                changed = True
        except Exception:
            pass
        # Normalize statuses / character ref / negative tail; collect stats
        tail = self._neg_tail()
        durs, dups = [], len(scenes) - len(set(s.img_prompt for s in scenes))
        covered = 0.0
        for s in scenes:
            durs.append(s.duration or 0)
            covered += (s.duration or 0)
            patch = {}
            if not str(s.status_img or "").strip():
                patch["status_img"] = "pending"
            if not str(s.status_vid or "").strip():
                patch["status_vid"] = "pending"
            if not str(s.characters_used or "").strip():
                patch["characters_used"] = "nv1"
            # The image generator uploads scene.reference_files (e.g. ["nv1.png"])
            # to lock the character — must NOT be empty.
            if not str(s.reference_files or "").strip():
                patch["reference_files"] = json.dumps(["nv1.png"])
            # Guarantee the anti-realism negative tail is present on each prompt.
            img = str(s.img_prompt or "")
            if img and "photoreal" not in img.lower():
                patch["img_prompt"] = img.rstrip(" .,") + ", " + tail
            vid = str(s.video_prompt or "")
            if vid and "photoreal" not in vid.lower():
                patch["video_prompt"] = vid.rstrip(" .,") + ", " + tail
            if patch:
                try:
                    wb.update_scene(s.scene_id, **patch)
                    changed = True
                except Exception:
                    pass
        if changed:
            try:
                wb.save()
            except Exception:
                pass
        self._log(f"  -> {len(scenes)} scenes | dur {min(durs):.1f}-{max(durs):.1f}s "
                  f"(avg {sum(durs)/len(durs):.1f}) | covered ~{covered:.0f}s/{last_end:.0f}s")
        # For Veo3 the only hard problem is a scene LONGER than max_dur; short is fine.
        too_long = [s.scene_id for s in scenes if (s.duration or 0) > self.max_dur + 0.4]
        if too_long:
            self._log(f"  [WARN] {len(too_long)} scene(s) > {self.max_dur:.0f}s (Veo3 limit): {too_long[:12]}", "WARN")
        if dups:
            self._log(f"  [WARN] {dups} img_prompt trung nhau", "WARN")
        miss = [s.scene_id for s in scenes if not str(s.img_prompt).strip() or not str(s.video_prompt).strip()]
        if miss:
            self._log(f"  [WARN] scene thieu prompt: {miss[:12]}", "WARN")

        # ── Thumbnails ──
        try:
            thumbs = wb.get_thumbnails()
        except Exception:
            thumbs = []
        tchanged = False
        import json as _json
        for t in thumbs:
            tpatch = {}
            if not str(getattr(t, "characters_used", "") or "").strip():
                tpatch["characters_used"] = "nv1"
            if not str(getattr(t, "reference_files", "") or "").strip():
                tpatch["reference_files"] = _json.dumps(["nv1.png"])
            if not str(getattr(t, "status_img", "") or "").strip():
                tpatch["status_img"] = "pending"
            if not str(getattr(t, "status_portrait", "") or "").strip():
                tpatch["status_portrait"] = "pending"
            if tpatch:
                try:
                    wb.update_thumbnail(t.thumb_id, **tpatch)
                    tchanged = True
                except Exception:
                    pass
        if tchanged:
            try:
                wb.save()
            except Exception:
                pass
        if len(thumbs) == 3:
            self._log(f"  -> {len(thumbs)} thumbnails (sheet 'thumbnail')")
        else:
            self._log(f"  [WARN] thumbnail sheet co {len(thumbs)} dong (mong doi 3)", "WARN")
