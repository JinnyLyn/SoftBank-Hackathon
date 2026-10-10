# infra/platform — 플랫폼 자체를 AWS에 올리기

플랫폼(백엔드 `back/` + 프런트 `front/`)을 **배포 파이프라인이 쓰는 같은 모듈**(`modules/ecs-web-app`)로 올린다. 컨테이너 1개가 FastAPI와
빌드한 프런트를 같은 출처로 서비스한다(`serve.py`). 배포 대상 앱이 아니라 플랫폼이라서 `AGENTS.md` §2의 "플랫폼은 현재 발표자 PC에서 실행·검증한다"와
다르다. **다중 사용자 공개 운영이 아니라 시연용 서버 실행**이다(아래 "한계").

| 파일 | 역할 |
|---|---|
| `Dockerfile` | 프런트 빌드(`VITE_USE_MOCK=false`) → 백엔드 이미지에 `web/`으로 복사. 파이썬 버전은 `back/Dockerfile`과 같다 |
| `serve.py` | `app.main:app`에 정적 파일을 붙이고 보안 헤더·CSP를 더한다. 백엔드·프런트 코드는 고치지 않는다 |
| `app.json` | 배포 앱 설정(포트 8000, `/health`, 앱 전용 DB, 마이그레이션 초기화 명령, `max_tasks=1`) |
| `build-source.sh` | 빌드 컨텍스트를 `back/ front/ infra/platform/`의 필요한 파일로만 모은다(`.git`·`node_modules`·`.env` 제외) |

## 올리는 순서

```bash
export AWS_REGION=sa-east-1 PAVED_STATE_BUCKET=<state 버킷>

# 1) 워커 토큰을 SSM SecureString으로 만든다(값은 화면에 남기지 않는다). 이름 규칙: /<프로젝트>/apps/<id>/secrets/<환경 변수 이름>
#    deploy.sh의 put_secret_param 을 쓰면 값이 명령줄에 나오지 않는다. 예) 아래 한 줄을 bash로 실행
bash -c 'source infra/scripts/deploy.sh; export_foundation; v=$("$(pick_python)" -c "import secrets;print(secrets.token_hex(24))"); \
  put_secret_param "$(region)" "$(secret_param_name platform WORKER_API_TOKEN)" "Worker API token for platform" "$v"'

# 2) 이미지 계획(승인용 plan) → 빌드 → 적용
bash infra/platform/build-source.sh /tmp/platform-src
IMG=$(bash infra/scripts/deploy.sh image-ref platform)
bash infra/scripts/deploy.sh up --id platform --image "$IMG" --app infra/platform/app.json --secret WORKER_API_TOKEN --plan-only
bash infra/scripts/deploy.sh build --id platform --source /tmp/platform-src --dockerfile infra/platform/Dockerfile --tag "${IMG##*:}"
bash infra/scripts/deploy.sh apply platform
```

접속 주소는 `https://platform.<와일드카드 도메인>:<포트>`다(ALB 기본 주소로 열면 인증서 이름이 달라 경고가 뜬다). 포트는 자동 할당된다.

## 서버 백엔드에 워커·분석기 연결하기

서버 백엔드의 `WORKER_API_TOKEN`은 SSM 비밀로 주입된다(`deploy.sh up --secret`, 모듈의 `secret_parameter_arns`). 평문 환경 변수에는 없다.
워커와 `ai/runner.py`는 **내 PC**에서 같은 값으로 돌린다.

```bash
export WORKER_API_TOKEN=$(aws ssm get-parameter --region sa-east-1 --with-decryption \
  --name /paved-clouds/apps/platform/secrets/WORKER_API_TOKEN --query Parameter.Value --output text)
export PLATFORM_API_URL=https://platform.<도메인>:<포트>
python infra/worker/worker.py          # 소스 ZIP은 API에서 내려받는다(infra/worker/README.md). PR #31 이상이 필요하다
python ai/runner.py                    # 같은 환경 변수
```

워커가 서버에 붙어 있는 동안에는 **누구나 서버의 화면에서 배포를 승인할 수 있고, 그 승인은 이 PC의 워커가 AWS에 실제로 적용한다**(아래 "한계"). 시연이 끝나면 워커를 끈다.

## 한계 (정직하게)

- **로그인이 없다.** 화면과 사용자용 API는 주소를 아는 누구나 쓴다(`front/README.md`: SSO를 범위에서 뺐다). 서버에서 막히는 것은 worker 전용 API(`/api/worker/*`, 토큰)뿐이다.
  업로드 크기는 `MAX_UPLOAD_BYTES`(50 MiB)로 줄였다. 공개 서비스 전에 사용자 인증·소유권·작업 격리가 필요하다(`docs/PRODUCT_DIRECTION.md` §4).
- **업로드 ZIP과 plan 파일은 컨테이너 디스크에 있다.** 태스크가 다시 뜨면 사라지고(Fargate 임시 저장소), 그때 승인 대기 중이던 계획은 plan 파일을 잃어 배포가 실패한다.
  그래서 `max_tasks=1`이다. 영구 저장이 필요하면 백엔드의 S3 모드(`ARTIFACT_S3_BUCKET`)를 쓰되, 태스크 역할(S3 권한)이 모듈에 아직 없어 이번에는 쓰지 않았다.
- **워커는 서버에 올리지 않는다.** `terraform`·`docker`·AWS 자격 증명이 필요한 실행기라서 내 PC에서 돈다.
- 프런트 개발 서버가 아니라 **빌드한 정적 파일**을 서비스한다. 화면을 바꾸면 이미지를 다시 빌드해 `update --image`로 올린다.
- 정리: `deploy.sh destroy platform`은 앱만 지운다. 앱 전용 DB와 SSM 비밀(`/paved-clouds/apps/platform/secrets/*`)은 남는다. 비밀은
  `aws ssm delete-parameter`로 직접 지우고, DB는 `deploy.sh drop-db platform`으로 지운다.
