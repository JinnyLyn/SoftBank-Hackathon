"""Ollama 클라우드(/api/chat) client. 표준 라이브러리만 쓴다.

    POST {base_url}/api/chat  Authorization: Bearer $OLLAMA_API_KEY
    {"model", "messages", "stream": false, "format": <JSON 스키마>, "options": {"temperature": 0}}
    → {"model", "message": {"content", "thinking"?}, "done_reason": "stop" | "length" | ...}

format 에 스키마를 줘도 모델이 그대로 지키지는 않는다(코드 블록으로 감싸거나 필수 필드를 빼먹음).
그래서 답 해석·검증은 llm.py 가 한다. 키 값은 로그·오류 메시지에 넣지 않는다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_BASE_URL = "https://ollama.com"
DEFAULT_MODEL = "glm-5.3"


class OllamaError(Exception):
    """호출 실패. 메시지는 사람이 읽을 이유 (키 값·요청 내용은 담지 않음)"""


@dataclass
class Reply:
    text: str
    stop_reason: str  # "end_turn" | "max_tokens" | 그 밖의 done_reason
    model: str
    request_id: Optional[str] = None


@dataclass
class OllamaClient:
    api_key: str = field(repr=False)  # repr에 찍히지 않게
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 180.0

    def chat(self, model: str, system: str, user: str, schema: dict, max_tokens: int) -> Reply:
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                request_id = resp.headers.get("x-request-id")
        except urllib.error.HTTPError as exc:
            raise OllamaError(_http_reason(exc)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(f"Ollama API에 연결하지 못했습니다: {getattr(exc, 'reason', exc)}") from None
        except ValueError:
            raise OllamaError("Ollama 응답이 JSON이 아닙니다.") from None

        message = data.get("message") if isinstance(data, dict) else None
        text = message.get("content", "") if isinstance(message, dict) else ""
        done = data.get("done_reason", "stop") if isinstance(data, dict) else "stop"
        stop = {"stop": "end_turn", "length": "max_tokens"}.get(done, str(done))
        return Reply(text=text or "", stop_reason=stop, model=str(data.get("model") or model), request_id=request_id)


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
