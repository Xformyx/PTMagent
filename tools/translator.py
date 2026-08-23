"""
중국어 <-> 한국어 번역 도구
Ollama(gemma3:27b)를 사용해 로컬에서 번역. API 비용 없음.
프롬프트는 config.yaml의 translator 섹션에서 관리.
"""
from pathlib import Path

import yaml
from langchain_community.llms import Ollama

from ollama_settings import get_ollama_base_url

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

_DEFAULT_ZH_TO_KO = """다음 중국어 텍스트를 자연스러운 대화체 한국어로 번역해주세요.
규칙:
- 번역은 구어체/대화체로 자연스럽게.
- 주요 단어(핵심 어휘)는 괄호 안에 한어병음과 뜻을 함께 표기. 예: 糊塗(hútu, 어리석다)
- 번역만 출력하고 다른 설명은 하지 마세요."""

_DEFAULT_KO_TO_ZH = """다음 한국어 텍스트를 자연스러운 중국어(간체)로 번역해주세요.
번역만 출력하고 다른 설명은 하지 마세요."""


def _load_prompts() -> tuple[str, str]:
    """config.yaml에서 번역 프롬프트 로드"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        trans = cfg.get("translator", {})
        zh_to_ko = trans.get("zh_to_ko", _DEFAULT_ZH_TO_KO)
        ko_to_zh = trans.get("ko_to_zh", _DEFAULT_KO_TO_ZH)
        return zh_to_ko.strip(), ko_to_zh.strip()
    return _DEFAULT_ZH_TO_KO, _DEFAULT_KO_TO_ZH


def _get_llm(model: str = "gemma3:27b"):
    return Ollama(model=model, base_url=get_ollama_base_url())


def detect_language(text: str) -> str:
    """간단 휴리스틱: 한자 비율로 중국어/한국어 추정"""
    if not text.strip():
        return "unknown"
    hanja = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    hangul = sum(1 for c in text if "\uac00" <= c <= "\ud7a3")
    if hanja > hangul:
        return "zh"
    return "ko"


def translate_to_korean(text: str, model: str = "gemma3:27b") -> str:
    """중국어 → 한국어 번역 (config.yaml의 prompt 사용)"""
    zh_to_ko, _ = _load_prompts()
    prompt = f"{zh_to_ko}\n\n{text}"
    return _get_llm(model).invoke(prompt)


def translate_to_chinese(text: str, model: str = "gemma3:27b") -> str:
    """한국어 → 중국어 번역 (config.yaml의 prompt 사용)"""
    _, ko_to_zh = _load_prompts()
    prompt = f"{ko_to_zh}\n\n{text}"
    return _get_llm(model).invoke(prompt)


def translate_auto(text: str, model: str = "gemma3:27b") -> str:
    """
    자동 감지 후 번역.
    중국어 → 한국어, 한국어 → 중국어
    """
    lang = detect_language(text)
    if lang == "zh":
        return translate_to_korean(text, model)
    return translate_to_chinese(text, model)
