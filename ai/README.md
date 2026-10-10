# Paved Clouds AI — 코드 분석

흐름도 ②·③ "비밀값 가리기 · AI 분석" 담당. 업로드한 소스에서 배포에 필요한 값을 찾아 분석 결과로 기록한다.

> **임시 위치.** 플랫폼 백엔드(`back/`)와 같은 저장소 안에서 작업 충돌을 피하려고 따로 뒀다.
> 나중에 `paved_ai/` 를 `back/app/llm/` 으로 옮긴다. 그래서 `paved_ai/` 에는 HTTP·DB를 모르는 순수 로직만 둔다.

## 전체 흐름에서의 위치

```
사용자 ─ZIP─▶ 백엔드 ─ back/data/uploads/{project_id}.zip
                  │
                  ▼
       [ai] ① 소스 읽기 (source.py)
            ② 비밀값 가리기 (masking.py)          ← LLM에 보내기 전
            ③ 규칙 분석 (analysis.py)
            ④ 규칙이 못 찾은 값만 LLM으로 (llm.py)
                  │
                  ▼  POST /api/projects/{id}/analyses  (result)
       [infra/worker] 구성 단계·비용(cost.py) → terraform plan → 계획 등록
                  │
                  ▼
       [front] 비교표·코드 검토·승인 → [worker] 빌드·배포
```

구성 단계(lean/balanced/roomy)·월 비용·계획 등록은 **인프라 worker가 한다** (`infra/worker/README.md`). 이 폴더는 비용을 계산하지 않는다.

## 분석 결과 (`result`)

읽는 쪽이 둘이라 한 결과에 둘 다 담는다.

| 필드 | 읽는 쪽 | 내용 |
|---|---|---|
| `app_config` | 인프라 worker | `container_port`, `health_check_path`, `use_database`, `environment`, `init_command`(있을 때). `app-config.schema.json` 규칙 |
| `dockerfile` | 인프라 worker | 소스 안 Dockerfile 상대 경로 |
| `scale` | 인프라 worker | 사용 규모·예산 `{expected_users, traffic_pattern, monthly_budget_usd}`. 아직 받을 곳이 없어 `null` |
| `stack`, `findings`, `evidence` | 프런트 | "코드에서 찾은 것" 화면 |
| `supported`, `unsupported_reasons` | 둘 다 | 배포할 수 없는 앱이면 이유 |
| `unresolved` | 다음 단계(LLM)·화면 | 못 찾은 값과 이유. 추측하지 않음 |
| `sizing_hints`, `has_dockerfile`, `masking` | 참고 | 크기 판단 힌트, 가린 파일·횟수 |

`schema_version` 은 `analysis/1`.

## runner (`runner.py`)

분석 결과가 없는 프로젝트를 찾아 분석하고 기록한다. 인프라 worker처럼 **백엔드와 같은 PC에서** 돌린다(업로드 ZIP을 직접 읽음).

```bash
python ai/runner.py           # 계속 돌며 5초마다 점검
python ai/runner.py --once    # 한 번만 점검
```

| 설정 | 기본값 |
|---|---|
| `PLATFORM_API_URL` / `--api-url` | `http://127.0.0.1:8000` |
| `UPLOAD_DIR` / `--upload-dir` | `back/data/uploads` (백엔드와 같은 변수·기본값) |

1. `GET /api/projects` 로 프로젝트를 읽고(`next_cursor` 따라 최대 10페이지), `GET /analyses/latest` 가 404인 것만 처리
2. `{UPLOAD_DIR}/{project_id}.zip` 을 읽고 **SHA-256이 프로젝트에 기록된 값과 같을 때만** 분석 (다르면 멈춤)
3. 분석 결과에 비밀값이 남지 않았는지 확인한 뒤 `POST /api/projects/{id}/analyses`
4. 실패하면 2분 뒤 다시, 3번 실패하면 그 프로젝트는 멈춤. 지문 불일치·삭제·형식 오류처럼 다시 보내도 같은 실패는 바로 멈춤
5. 로그에는 프로젝트 ID·이름·결과 요약만 남긴다

키(`ANTHROPIC_API_KEY`)와 SDK가 있으면 규칙이 못 찾은 값만 LLM에 묻는다. 없거나 실패하면 규칙 결과만 기록한다.


## LLM 보조 (`llm.py`)

| 항목 | 값 |
|---|---|
| 묻는 것 | `unresolved` 중 `container_port`, `health_check_path`, `dockerfile`, `framework` 만. 규칙이 찾은 값은 덮어쓰지 않음 |
| 모델 | `claude-opus-5-5` (`PAVED_AI_MODEL` 로 변경), effort `medium` |
| 출력 | `output_config.format` JSON 스키마 (`answers`, `notes`) |
| 거절 대비 | 서버 측 대체 모델 `fallbacks: "default"` (베타 `server-side-fallback-2026-07-01`) |
| 검증 | 근거 파일·줄이 실제로 있고 그 줄에 값이 들어 있어야 받아들임. 아니면 버리고 `unresolved` 유지 |
| 비밀값 | 보내기 직전에 다시 가림. 업로드 코드 속 문장은 지시가 아니라 데이터라고 프롬프트에 밝힘 |
| 표시 | 결과의 `ai.filled` 에 LLM이 채운 값 이름, `findings` 에 "AI가 채운 값" 안내 |
| 실패 | 키 없음·인증·한도·연결 오류는 규칙 결과만 기록 (`findings` 에 이유) |

설치 (LLM을 쓸 때만):

```bash
cd ai
py -3.12 -m venv .venv                       # 백엔드와 맞추려면 3.13 권장, 3.10 이상
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env                       # ANTHROPIC_API_KEY 채우기 (커밋 안 됨)
.venv\Scripts\python runner.py --once
```

## 비밀값 가리기 (`masking.py`)

- `.env`·키 파일(`*.pem`, `id_rsa` 등)은 내용을 보내지 않는다. `.env` 는 변수 이름만 남긴다.
- 주소 속 `아이디:비밀번호@`, AWS 키, `sk-ant-…`, `ghp_…`, Bearer 토큰, 개인 키 블록은 어디서든 가린다.
- 설정 파일(`.env*`, YAML, compose 등)의 `비밀이름=값` 은 값만 가린다. 코드에서는 문자열로 박아 둔 값만 가린다(`os.getenv("SECRET_KEY")` 같은 줄은 그대로).
- 원본은 바꾸지 않는다.
- 결과를 백엔드에 기록하기 전, 백엔드의 비밀값 거절 규칙(`back/app/main.py`)과 같은 검사를 먼저 한다.
  백엔드는 "이름=값" 중 이름이 비밀스러운 것만 거절한다(`_contains_inline_secret`). `python:3.12` 같은 근거 문장은 그대로 기록된다.

## 테스트

표준 라이브러리 `unittest` 만 쓴다 (팀 CI 규칙: 테스트 패키지를 추가하지 않음).

```bash
cd ai
python -m unittest discover -s tests -t . -v
```

- 실제 샘플 앱(`sample-back` + `sample-front`)의 결과가 정답과 같고 인프라 스키마를 통과하는지
- compose 파일의 비밀번호가 결과에 남지 않는지
- 백엔드 비밀값 규칙이 바뀌면 깨지는지 (백엔드 소스의 정규식과 비교)

## 환경

- Python: 백엔드와 같은 3.13 (`.python-version`). 3.9 이상에서 돌게 짠다.
- 의존성: 분석·runner·테스트는 표준 라이브러리만. LLM 보조만 `anthropic==1.13.0` (`requirements.txt`).
  테스트는 가짜 client로 돌아서 SDK 없이도 통과한다.
- Anthropic 키는 환경 변수 `ANTHROPIC_API_KEY` 또는 `ai/.env`(커밋 안 됨)로만 넣는다. 로그·문서·채팅에 값을 남기지 않는다.
- 아직 팀 CI 검사 대상이 아니다. 등록은 CI 담당과 상의.

## 남은 일

- 백엔드: 사용 규모·예산 저장 위치
