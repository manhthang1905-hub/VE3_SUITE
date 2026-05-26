"""
Setup FlowKit Extensions — tạo bản extension riêng cho mỗi Chrome/server.

Mỗi bản extension có WS port và HTTP callback port khác nhau,
đảm bảo các Chrome hoạt động độc lập.

Usage:
    python setup_extensions.py --chrome-dir "D:\\VE3_SUITE" --base-port 8100
    python setup_extensions.py --count 7 --base-port 8100 --output-dir "D:\\VE3_SUITE\\flowkit_extensions"

Output:
    flowkit_extensions/
    ├── ext_8100/    → Chrome Copy (1), WS=9222, HTTP=8100
    ├── ext_8101/    → Chrome Copy (2), WS=9223, HTTP=8101
    ├── ext_8102/    → Chrome Copy (3), WS=9224, HTTP=8102
    └── ...
"""
import argparse
import glob
import os
import re
import shutil
from pathlib import Path

EXTENSION_SRC = Path(__file__).parent / "extension"


def create_extension_copy(api_port: int, ws_port: int, output_dir: Path, name: str = ""):
    """Create a copy of the extension with custom ports."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(EXTENSION_SRC, output_dir)

    bg_path = output_dir / "background.js"
    bg_content = bg_path.read_text(encoding="utf-8")

    # Replace WS port
    bg_content = re.sub(
        r"const AGENT_WS_URL\s*=\s*'ws://127\.0\.0\.1:\d+'",
        f"const AGENT_WS_URL = 'ws://127.0.0.1:{ws_port}'",
        bg_content,
    )

    # Replace HTTP callback URL
    bg_content = re.sub(
        r"http://127\.0\.0\.1:\d+/api/ext/callback",
        f"http://127.0.0.1:{api_port}/api/ext/callback",
        bg_content,
    )

    bg_path.write_text(bg_content, encoding="utf-8")

    # Also update manifest name for identification
    manifest_path = output_dir / "manifest.json"
    manifest = manifest_path.read_text(encoding="utf-8")
    display_name = name or f"FlowKit-{api_port}"
    manifest = re.sub(
        r'"name"\s*:\s*"[^"]*"',
        f'"name": "{display_name}"',
        manifest,
    )
    # Update host_permissions to include this port
    manifest = re.sub(
        r"http://127\.0\.0\.1:8100/\*",
        f"http://127.0.0.1:{api_port}/*",
        manifest,
    )
    manifest_path.write_text(manifest, encoding="utf-8")

    return output_dir


def discover_chromes(chrome_dir: str):
    """Find all Chrome Portable instances."""
    pattern = os.path.join(chrome_dir, "GoogleChromePortable - Copy*", "GoogleChromePortable.exe")
    paths = sorted(glob.glob(pattern))
    return paths


def main():
    parser = argparse.ArgumentParser(description="Setup FlowKit Extensions for multi-Chrome")
    parser.add_argument("--chrome-dir", type=str, default="D:\\VE3_SUITE",
                        help="Directory with Chrome Portable instances")
    parser.add_argument("--count", type=int, default=0,
                        help="Number of extensions to create (auto-detect from chrome-dir if 0)")
    parser.add_argument("--base-port", type=int, default=8100,
                        help="Base API port (default: 8100)")
    parser.add_argument("--output-dir", type=str, default="",
                        help="Output directory (default: <chrome-dir>/flowkit_extensions)")
    args = parser.parse_args()

    if args.count > 0:
        count = args.count
        chromes = []
    else:
        chromes = discover_chromes(args.chrome_dir)
        count = len(chromes)

    if count == 0:
        print("No Chrome instances found!")
        return

    output_base = Path(args.output_dir) if args.output_dir else Path(args.chrome_dir) / "flowkit_extensions"
    output_base.mkdir(parents=True, exist_ok=True)

    print(f"Creating {count} FlowKit extensions in {output_base}")
    print(f"Source extension: {EXTENSION_SRC}")
    print()

    for i in range(count):
        api_port = args.base_port + i
        ws_port = 9222 + i
        ext_dir = output_base / f"ext_{api_port}"
        chrome_name = Path(chromes[i]).parent.name if i < len(chromes) else f"Chrome-{i+1}"
        display_name = f"FlowKit-{i+1}"

        create_extension_copy(api_port, ws_port, ext_dir, display_name)

        print(f"  [{display_name}] port={api_port} ws={ws_port}")
        print(f"    Extension: {ext_dir}")
        if i < len(chromes):
            print(f"    Chrome:    {chrome_name}")
        print()

    # Print installation instructions
    print("=" * 60)
    print("  INSTALLATION GUIDE")
    print("=" * 60)
    print()
    for i in range(count):
        api_port = args.base_port + i
        ext_dir = output_base / f"ext_{api_port}"
        chrome_name = Path(chromes[i]).parent.name if i < len(chromes) else f"Chrome-{i+1}"
        print(f"  {chrome_name}:")
        print(f"    1. Mở Chrome → chrome://extensions")
        print(f"    2. Bật Developer mode")
        print(f"    3. Load unpacked → {ext_dir}")
        print()

    # Print VE3 config
    print("=" * 60)
    print("  VE3 CONFIG (thêm vào settings)")
    print("=" * 60)
    print()
    print("generation_backend: flowkit")
    print("flowkit_server_list:")
    for i in range(count):
        api_port = args.base_port + i
        chrome_name = Path(chromes[i]).parent.name if i < len(chromes) else f"Chrome-{i+1}"
        print(f'  - url: "http://127.0.0.1:{api_port}"')
        print(f'    name: "flowkit-{i+1}"')
        print(f'    enabled: true')
        print(f'    chrome_path: "{chromes[i] if i < len(chromes) else ""}"')


if __name__ == "__main__":
    main()
