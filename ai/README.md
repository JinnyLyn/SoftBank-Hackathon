# Paved Clouds AI — 구성 추천과 배포 변수값 생성

흐름도 ④ "구성·비용 계산·계획 생성" 담당 (10/8 회의: 구성 추천·배포 코드 생성).

> **임시 위치.** 플랫폼 백엔드(`back/`)가 main에 합쳐지면 `paved_ai/` 를 `back/app/llm/` 으로 옮긴다.
> 그래서 `paved_ai/` 에는 HTTP·DB를 모르는 순수 로직만 두고, 백엔드 API를 부르는 부분은 따로 둔다.

## 하는 일

```
코드 분석 결과 (진서)          사용 규모·월 예산
  container_port                  expectedUsers, pattern
  health_check_path               monthlyBudgetUsd
  use_database                         │
  environment                          ▼
        │                 ① 구성 3단계: task_size · min_tasks · max_tasks
        │                 ② 가격표로 월 비용 계산 (LLM 아님)          ← 지금 여기까지
        │                 ③ 예산 안에서 추천
        └────────────┬────────────┘
                     ▼
     ④ 7개 값을 app-config.schema.json으로 검증 → app.auto.tfvars.json
                     ▼
     ⑤ POST /api/plans (module_id: ecs-web-app) → worker가 terraform plan
```

- LLM은 Terraform을 새로 쓰지 않고 **모듈 변수값까지만** 만든다 (AGENTS.md 4).
- 모듈은 `infra/modules/ecs-web-app` 하나다. 출력 규칙은 그 모듈의 `app-config.schema.json`.

## 비용 계산 (`paved_ai/pricing.py`)

- 가격: AWS Price List API의 서울 리전 Fargate 온디맨드 단가 (`paved_ai/prices.json`, 기준일 2026-09-11)
- 월 비용 = (vCPU × vCPU 시간 단가 + 메모리 GB × GB 시간 단가) × 730시간 × 작업 수
- **앱 추가 비용만** 계산한다. 공용 기반(ALB·RDS·NAT·IPv4)은 `shared_base` 로 따로 표시하고 예산 판단에 넣지 않는다.
  금액(약 $80)은 아키텍처 문서의 참고값이며 인프라 담당 확인 전이다.
- 예산 판단은 **최대 작업 수 기준** (`BUDGET_BASIS = "max"`). 자동 확장으로 늘어나도 예산 안이어야 추천한다.
- 계획에 넣는 `cost_estimate.amount` 도 같은 값이다. 화면·추천·승인이 모두 이 금액 하나를 쓴다.

| 크기 | 사양 | 작업 1개 월 비용 (x86) |
|---|---|---|
| xsmall | 0.25 vCPU, 0.5GB | $10.36 |
| small | 0.5 vCPU, 1GB | $20.72 |
| medium | 1 vCPU, 2GB | $41.45 |

가격 갱신:

```bash
python scripts/update_prices.py            # 공식 가격과 비교만
python scripts/update_prices.py --write    # prices.json 갱신
```

## 테스트

표준 라이브러리 `unittest` 만 쓴다 (팀 CI 규칙: 테스트 패키지를 추가하지 않음).

```bash
cd ai
python -m unittest discover -s tests -t . -v
```

`InfraSyncTests` 는 가격표의 크기·작업 수 규칙이 Terraform 모듈(`main.tf`의 task_sizes)과 LLM 출력 스키마(`app-config.schema.json`)와 같은지 확인한다.
인프라 쪽을 바꾸면 이 테스트가 깨지므로 가격표도 같이 고친다.

## 환경

- Python: 백엔드와 같은 3.13 (`.python-version`). 3.9 이상에서 돌게 짠다.
- 의존성: 현재 표준 라이브러리만 (`requirements.txt`). LLM SDK 등은 쓰기 시작할 때 버전을 고정해 추가한다.
- 아직 CI 검사 대상이 아니다. 팀 CI(`scripts/ci/components.py`)에 등록은 CI 담당과 상의.

## 남은 결정

- 계획 변수 모양: worker가 tfvars로 쓰는 7개 값과 화면용 문구(tier, headline, reason 등)를 `{ "app": {...}, "ui": {...} }` 처럼 나눌지 (인프라·백엔드)
- 공용 기반 비용의 정확한 금액과 표시 방식 (인프라)
- 사용 규모·월 예산을 백엔드가 저장할 위치 (백엔드)
- LLM 모델·키 주입 방식, 분석 담당과 공통 호출 모듈 공유 여부 (진서)
