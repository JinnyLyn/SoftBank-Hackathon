# 플랫폼(백엔드 API + 프런트)을 컨테이너 1개로 묶는다. 배포 모듈이 "태스크 1개 = 컨테이너 1개"를 전제로 해서
# 프런트를 빌드한 정적 파일을 백엔드(FastAPI)가 같은 출처로 같이 서비스한다(sample-back과 같은 방식).
#
# 빌드 컨텍스트(저장소 루트가 아니라 아래만 모은 폴더 — infra/platform/README.md의 build-source.sh 참고):
#   back/(app, migrations, requirements.txt)   front/(node_modules·dist·.env 제외)   infra/platform/(serve.py)
#   예) deploy.sh build --id platform --source <폴더> --dockerfile infra/platform/Dockerfile --tag <태그>

# 1) 프런트 빌드. VITE_USE_MOCK=false: 화면이 가짜 응답(MOCK) 대신 같은 출처의 /api 를 부른다
FROM node:22-slim AS front
WORKDIR /front
COPY front/package.json front/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY front/ ./
ARG VITE_USE_MOCK=false
ENV VITE_USE_MOCK=$VITE_USE_MOCK
RUN npm run build

# 2) 백엔드 + 정적 파일. 파이썬 버전은 back/Dockerfile과 같게 유지한다
FROM python:3.13.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY back/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --create-home --uid 10001 appuser

COPY back/app ./app
COPY back/migrations ./migrations
COPY infra/platform/serve.py ./serve.py
COPY --from=front /front/dist ./web
RUN mkdir -p /var/lib/paved-clouds/uploads /var/lib/paved-clouds/plans \
    && chown -R appuser:appuser /app /var/lib/paved-clouds

USER appuser
EXPOSE 8000
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8000"]
