"""Full run: VE3 Worker with FlowKit backend for TL1-0202."""
import sys, os, time, yaml
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ve3"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ve3"))
from pathlib import Path

SUITE = Path(r"D:\VE3_SUITE")
PROJECT = SUITE / "PROJECTS" / "TL1-0202"

print("=" * 60)
print("FULL FLOWKIT RUN - TL1-0202")
print("=" * 60)

# Load config
with open(PROJECT / ".excel_runtime_config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

def log_func(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    m = str(msg)
    show = any(k in m.lower() for k in [
        "phase", "scene", "thumb", "video", " ok ", " fail", "error",
        "flowkit", "reset", "captcha", "=====", "completed", "finalize",
        "connected", "chrome", "agent", "ready", "pool", "upload",
        "media_id", "reference", "psy", "skip"
    ])
    if show or level in ("ERROR", "WARN", "SUCCESS"):
        print(f"[{ts}][{level}] {m[:300]}", flush=True)

from ve3_worker import VE3Worker
worker = VE3Worker(project_dir=str(PROJECT), config=config, log_func=log_func)
result = worker.run()

print("\n" + "=" * 60)
print(f"DONE: total={result.get('total')} completed={result.get('completed')} failed={result.get('failed')}")
if result.get("errors"):
    for e in result["errors"][:5]:
        print(f"  ERROR: {e[:300]}")
print("=" * 60)
