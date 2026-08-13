"""Phien ban cua SDK. Giu dong bo voi `version` trong pyproject.toml."""

from __future__ import annotations

__version__: str = "0.1.0"

#: Chuoi User-Agent gui kem moi request - CONTRACT.md yeu cau nhan dien SDK.
USER_AGENT: str = "shopapi-python/" + __version__
