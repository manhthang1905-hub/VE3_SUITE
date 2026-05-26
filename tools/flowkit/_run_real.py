"""Run VE3 worker with FlowKit mode on TL1-0189 — like the real tool."""
import sys, os, yaml, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ve3"))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ve3"))

from pathlib import Path
from ve3_worker import VE3Worker

PROJECT = Path(r"D:\VE3_SUITE\PROJECTS\TL1-0189")
with open(PROJECT / ".excel_runtime_config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

def log_func(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{level}] {msg}", flush=True)

print("=" * 60)
print("VE3 Worker — FlowKit mode — TL1-0189")
print(f"Backend: {config.get('generation_backend')}")
print("=" * 60)

worker = VE3Worker(project_dir=str(PROJECT), config=config, log_func=log_func)
result = worker.run()

print("\n" + "=" * 60)
print(f"Result: success={result.get('success')}")
print(f"  total={result.get('total')} completed={result.get('completed')} failed={result.get('failed')}")
if result.get("errors"):
    for e in result["errors"][:5]:
        print(f"  ERROR: {e}")
print("=" * 60)
