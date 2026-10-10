"""LLM 답 캐시 (AGENTS.md 7: 분석 캐시는 마스킹된 입력, 프롬프트·스키마 버전, 모델·설정을 포함한 키로 구분).

- 키: 가린 뒤의 시스템·사용자 프롬프트, 프롬프트·스키마 버전, 제공자, 모델을 합친 SHA-256. 원문 코드나 비밀값은 키에 쓰지 않음
- 값: LLM이 돌려준 답 텍스트와 stop_reason, 응답 모델. 꺼낸 답도 호출하는 쪽에서 근거 검증을 다시 한다
- 위치: 저장소 밖 (~/.cache/paved-ai/llm, PAVED_AI_CACHE_DIR 로 변경). PAVED_AI_CACHE=off 면 쓰지 않음
- 캐시 오류(디스크·권한)는 값을 포함하지 않는 경고를 남기고 캐시 없이 진행한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


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
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("LLM 캐시를 읽지 못해 다시 분석합니다 (%s).", type(exc).__name__)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        log.warning("LLM 캐시 형식이 잘못되어 다시 분석합니다.")
        return None
    return data


def put(key: str, value: dict) -> None:
    d = cache_dir()
    if d is None:
        return
    tmp = None
    try:
        d.mkdir(parents=True, exist_ok=True)
        # 반쯤 쓴 파일을 읽지 않게 임시 파일에 쓰고 바꿈
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, d / f"{key}.json")
    except OSError as exc:
        log.warning("LLM 캐시를 저장하지 못했습니다 (%s).", type(exc).__name__)
    finally:
        if tmp is not None:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("LLM 캐시 임시 파일을 정리하지 못했습니다 (%s).", type(exc).__name__)
