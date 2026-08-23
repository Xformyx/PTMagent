"""Ollama API 베이스 URL: 환경변수 OLLAMA_BASE_URL 또는 config.yaml의 ollama.base_url."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
_DEFAULT = "http://localhost:11434"
_cached: str | None = None


def get_ollama_base_url() -> str:
    """OLLAMA_BASE_URL이 있으면 최우선. 없으면 config.yaml, 없으면 localhost."""
    env = os.environ.get("OLLAMA_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    global _cached
    if _cached is not None:
        return _cached
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        ollama = cfg.get("ollama") or {}
        base = str(ollama.get("base_url") or _DEFAULT).strip().rstrip("/")
        _cached = base
        return base
    _cached = _DEFAULT
    return _cached
