#!/usr/bin/env bash
# 플랫폼 이미지의 빌드 컨텍스트를 만든다. 저장소 전체가 아니라 back/ front/ infra/platform/ 의 필요한 파일만 깨끗하게 모은다
# (.git, node_modules, dist, .env, 업로드 데이터, 캐시가 이미지에 들어가지 않게 한다).
#
# 사용: bash infra/platform/build-source.sh <비어 있는(또는 없는) 대상 폴더>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out="${1:-}"
[ -n "$out" ] || { echo "사용법: build-source.sh <대상 폴더>" >&2; exit 2; }
if [ -e "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ]; then
  echo "오류: 대상 폴더가 비어 있지 않습니다: $out" >&2
  exit 1
fi
mkdir -p "$out/back" "$out/front" "$out/infra/platform"

# 백엔드: 코드·마이그레이션·의존성 목록만(로컬 data/, .env, 캐시 제외)
tar -C "$ROOT/back" --exclude='__pycache__' --exclude='*.pyc' -cf - app migrations requirements.txt | tar -C "$out/back" -xf -
# 프런트: 소스 전체에서 설치물·빌드물·환경 파일 제외
tar -C "$ROOT/front" --exclude='node_modules' --exclude='dist' --exclude='*.tsbuildinfo' --exclude='.env' --exclude='.env.local' -cf - . | tar -C "$out/front" -xf -
# 플랫폼 이미지 정의
cp "$ROOT/infra/platform/Dockerfile" "$ROOT/infra/platform/serve.py" "$out/infra/platform/"

echo "$out"
