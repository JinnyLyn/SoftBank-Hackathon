"""LLM 답 캐시 (AGENTS.md 7: 분석 캐시는 마스킹된 입력, 프롬프트·스키마 버전, 모델·설정을 포함한 키로 구분).

- 키: 가린 뒤의 시스템·사용자 프롬프트, 프롬프트·스키마 버전, 제공자, 모델을 합친 SHA-256. 원문 코드나 비밀값은 키에 쓰지 않음
- 값: LLM이 돌려준 답 텍스트와 stop_reason, 응답 모델. 꺼낸 답도 호출하는 쪽에서 근거 검증을 다시 한다
- 위치: 저장소 밖 (~/.cache/paved-ai/llm, PAVED_AI_CACHE_DIR 로 변경). PAVED_AI_CACHE=off 면 쓰지 않음
- 캐시 오류(디스크·권한)는 분석을 막지 않는다. 그냥 캐시 없이 진행
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


def cache_dir() -> Optional[Path]:
    if os.environ.get("PAVED_AI_CACHE", "").strip().lower() in ("off", "0", "false", "no"):
        return None
    base = os.environ.get("PAVED_AI_CACHE_DIR")
    return Path(base).expanduser() if base else Path.home() / ".cache" / "paved-ai" / "llm"


def cache_key(**parts: str) -> str:
    """이름순으로 합쳐 SHA-256. 순서·구분이 섞이지 않게 JSON으로 묶음"""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def get(key: str) -> Optional[dict]:
    d = cache_dir()
    if d is None:
        return None
    try:
        data = json.loads((d / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("text"), str) else None


def put(key: str, value: dict) -> None:
    d = cache_dir()
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        # 반쯤 쓴 파일을 읽지 않게 임시 파일에 쓰고 바꿈
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, d / f"{key}.json")
    except OSError:
        pass
