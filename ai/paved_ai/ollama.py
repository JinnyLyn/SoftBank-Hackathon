"""Ollama 클라우드(/api/chat) client. 표준 라이브러리만 쓴다.

    POST {base_url}/api/chat  Authorization: Bearer $OLLAMA_API_KEY
    {"model", "messages", "stream": false, "format": <JSON 스키마>, "options": {"temperature": 0}}
    → {"model", "message": {"content", "thinking"?}, "done_reason": "stop" | "length" | ...}

format 에 스키마를 줘도 모델이 그대로 지키지는 않는다(코드 블록으로 감싸거나 필수 필드를 빼먹음).
그래서 답 해석·검증은 llm.py 가 한다. 키 값은 로그·오류 메시지에 넣지 않는다.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_BASE_URL = "https://ollama.com"
# 10/11 비교(시험 앱 2개 × 3회, 캐시 끔): gemma4:31b 중간값 1.2초·6/6로 가장 빠름 (glm-5.3 5.6초·6/6)
DEFAULT_MODEL = "gemma4:31b"


class OllamaError(Exception):
    """호출 실패. 메시지는 사람이 읽을 이유 (키 값·요청 내용은 담지 않음).
    transient: 잠깐 뒤 다시 하면 될 수 있는 실패(시간 초과·연결·429·5xx). 인증·요청 형식 오류는 False"""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class Reply:
    text: str
    stop_reason: str  # "end_turn" | "max_tokens" | 그 밖의 done_reason
    model: str
    request_id: Optional[str] = None
    # 토큰 사용량 (Ollama prompt_eval_count / eval_count). 모르면 None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class OllamaClient:
    api_key: str = field(repr=False)  # repr에 찍히지 않게
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 60.0

    def chat(self, model: str, system: str, user: str, schema: dict, max_tokens: int, timeout: Optional[float] = None) -> Reply:
        """timeout: 이 호출의 상한(초). 없으면 self.timeout"""
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_predict": max_tokens},
        }
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                request_id = resp.headers.get("x-request-id")
        except urllib.error.HTTPError as exc:
            raise OllamaError(_http_reason(exc), transient=exc.code == 429 or exc.code >= 500) from None
        except (TimeoutError, socket.timeout):
            raise OllamaError(f"Ollama 응답이 {timeout or self.timeout:g}초 안에 오지 않았습니다.", transient=True) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise OllamaError(f"Ollama 응답이 {timeout or self.timeout:g}초 안에 오지 않았습니다.", transient=True) from None
            raise OllamaError(f"Ollama API에 연결하지 못했습니다: {reason}", transient=True) from None
        except ValueError:
            # 응답이 중간에 끊긴 경우가 대부분이라 다시 시도할 만함
            raise OllamaError("Ollama 응답이 JSON이 아닙니다.", transient=True) from None
        if not isinstance(data, dict):
            raise OllamaError("Ollama 응답 형식이 예상과 다릅니다.", transient=True)
        if data.get("error"):
            # 200으로 오류를 돌려주는 경우 (모델 과부하 등). 서버가 알려 준 이유만 담음
            raise OllamaError(f"Ollama API 오류: {str(data['error'])[:200]}", transient=True)

        message = data.get("message") if isinstance(data, dict) else None
        text = message.get("content", "") if isinstance(message, dict) else ""
        done = data.get("done_reason", "stop") if isinstance(data, dict) else "stop"
        stop = {"stop": "end_turn", "length": "max_tokens"}.get(done, str(done))
        def count(key):
            v = data.get(key) if isinstance(data, dict) else None
            return v if isinstance(v, int) and not isinstance(v, bool) else None

        return Reply(text=text or "", stop_reason=stop, model=str(data.get("model") or model), request_id=request_id,
                     input_tokens=count("prompt_eval_count"), output_tokens=count("eval_count"))


def _http_reason(exc: urllib.error.HTTPError) -> str:
    if exc.code in (401, 403):
        return "Ollama 인증에 실패했습니다. OLLAMA_API_KEY를 확인해 주세요."
    if exc.code == 429:
        return "Ollama 사용 한도에 걸렸습니다. 잠시 뒤 다시 시도합니다."
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
    except (ValueError, AttributeError, OSError):
        detail = ""
    # API가 알려 준 이유만 (모델 이름 오타 등을 바로 알 수 있게). 우리 요청 내용은 담기지 않음
    return f"Ollama API 오류 {exc.code}: {str(detail)[:200]}".rstrip(": ")
