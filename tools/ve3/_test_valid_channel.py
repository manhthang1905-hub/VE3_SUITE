"""Test: valid_channel fix — finance channels now found."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

ref_base = Path(r'D:\VE3_SUITE\tools\srt-to-excel\reference_characters')

def valid_channel_OLD(text):
    """Old: only checks psychology/"""
    root = ref_base / 'psychology'
    if (root / text / 'nv1.png').exists() or (root / text / 'style.yaml').exists():
        return text
    return ''

def valid_channel_NEW(text):
    """New: checks psychology/, finance/, success/"""
    for topic_dir in ('psychology', 'finance', 'success'):
        root = ref_base / topic_dir
        if (root / text / 'nv1.png').exists() or (root / text / 'style.yaml').exists():
            return text
    return ''

test_channels = ['TH1-T1', 'TH1-T2', 'TH1-T3', 'TH1-T10', 'TL1-T3', 'TL1-T1']
print("Channel     | OLD (psychology only) | NEW (all topics)")
print("-" * 60)
for ch in test_channels:
    old = valid_channel_OLD(ch) or "FAIL"
    new = valid_channel_NEW(ch) or "FAIL"
    print(f"  {ch:10} | {old:20} | {new}")
