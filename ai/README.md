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
            ③ 규칙 분석 (analysis.py)             ← 지금 여기까지
            ④ LLM으로 못 찾은 값 채우기·문구        ← 다음
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

## 비밀값 가리기 (`masking.py`)

- `.env`·키 파일(`*.pem`, `id_rsa` 등)은 내용을 보내지 않는다. `.env` 는 변수 이름만 남긴다.
- 주소 속 `아이디:비밀번호@`, AWS 키, `sk-ant-…`, `ghp_…`, Bearer 토큰, 개인 키 블록은 어디서든 가린다.
- 설정 파일(`.env*`, YAML, compose 등)의 `비밀이름=값` 은 값만 가린다. 코드에서는 문자열로 박아 둔 값만 가린다(`os.getenv("SECRET_KEY")` 같은 줄은 그대로).
- 원본은 바꾸지 않는다.
- 결과를 백엔드에 기록하기 전, 백엔드의 비밀값 거절 규칙(`back/app/main.py`)과 같은 검사를 먼저 한다.
  **2026-10-10 기준 백엔드 `_reject_secret_fields` 는 이름과 상관없이 모든 "이름=값"을 거절하는 문제가 있어** 수정을 요청했다(`masking.py` 주석).

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
- 의존성: 현재 표준 라이브러리만 (`requirements.txt`). LLM SDK는 쓰기 시작할 때 버전을 고정해 추가한다.
- Anthropic 키는 환경 변수 `ANTHROPIC_API_KEY` 또는 `ai/.env`(커밋 안 됨)로만 넣는다. 로그·문서·채팅에 값을 남기지 않는다.
- 아직 팀 CI 검사 대상이 아니다. 등록은 CI 담당과 상의.

## 남은 일

- runner: 분석 결과가 없는 프로젝트를 찾아 업로드 ZIP을 읽고(SHA-256 확인) 분석해 기록
- LLM: `unresolved` 값 채우기, 화면 문구 다듬기, 근거가 실제 파일·줄인지 확인
- 백엔드: 사용 규모·예산 저장 위치, `_reject_secret_fields` 수정
