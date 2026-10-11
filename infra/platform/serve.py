"""플랫폼 컨테이너의 진입점. 백엔드 API(app.main)에 빌드한 프런트(web/)를 같은 출처로 붙인다.

백엔드 코드는 고치지 않는다. 이 파일이 하는 일은 둘뿐이다.
  1) 프런트 정적 파일을 `/` 에 붙인다. API·`/health`·`/docs` 같은 기존 경로가 먼저 처리되고, 나머지가 정적 파일로 간다.
  2) 화면(HTML·JS·CSS)에 보안 헤더와 CSP를 붙인다. front/vite.config.ts의 SECURITY_HEADERS·CSP와 같은 값이다
     (그 파일이 "운영 서버에도 같은 헤더를 붙여야 함"이라고 적어 두었다). API 응답과 /docs(외부 CDN 스크립트를 쓴다)에는 CSP를 붙이지 않는다.

주의: 이 플랫폼에는 로그인이 없다(front/README.md: SSO를 범위에서 뺌). 서버에 올려도 화면과 사용자용 API는 누구나 쓸 수 있고,
worker 전용 API만 WORKER_API_TOKEN으로 막힌다(토큰이 비어 있으면 항상 401). 자세한 한계는 infra/platform/README.md.
"""
from __future__ import annotations

import os
from pathlib import Path

from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.main import app

WEB_DIR = Path(os.getenv("WEB_DIR", str(Path(__file__).resolve().parent / "web")))

# front/vite.config.ts 의 CSP 와 같은 값
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://cdn.jsdelivr.net",
    "font-src 'self' https://cdn.jsdelivr.net",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
}

# CSP를 붙이지 않는 경로: API 응답(JSON)과 Swagger UI(/docs, /redoc는 CDN 스크립트를 쓴다)
NO_CSP_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json", "/health", "/ready")

# 브라우저가 주소를 보고 고르는 화면(front/src/main.tsx). web/에 이 이름의 파일이 없어 StaticFiles가 404를 내므로 index.html을 준다.
# 모르는 경로 전체를 index.html로 돌리지 않는다(없는 /api/...·assets는 지금처럼 404). main.tsx에 화면을 추가하면 여기에도 적는다
PAGE_PATHS = ("/onprem",)


class SecurityHeaders:
    """순수 ASGI 미들웨어. BaseHTTPMiddleware를 쓰지 않는 이유: 응답과 요청 본문(최대 200 MiB의 ZIP 업로드)을 한 번 더 감싸지 않으려고."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        path = scope.get("path", "")
        with_csp = not path.startswith(NO_CSP_PREFIXES)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for name, value in SECURITY_HEADERS.items():
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))
                if with_csp and b"content-security-policy" not in present:
                    headers.append((b"content-security-policy", CSP.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.inner(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)

# 기존 라우트(API·/health·/docs)가 모두 등록된 뒤에 마지막으로 붙인다. 알 수 없는 /api/... 는 백엔드의 JSON 404가 먼저 처리한다
if WEB_DIR.is_dir():
    async def page_index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    for page_path in PAGE_PATHS:
        app.add_api_route(page_path, page_index, methods=["GET", "HEAD"], include_in_schema=False)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
