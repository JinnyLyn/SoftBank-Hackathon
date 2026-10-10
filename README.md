# Paved Clouds — SoftBank Hackathon 2026

term2_team_diamond의 관리형 앱 배포 시스템이다. 클라우드를 모르는 비전공자·예비 창업자가 앱과 사용 규모·예산, 원하는 독립 도메인을 선택하면 플랫폼이 배포·DNS·HTTPS 연결을 처리하는 것을 목표로 한다. 사용자에게 AWS 계정·IAM 키·DNS 설정 지식을 요구하지 않는다.

**현재 개발 기준: `2026-10-10-managed-domains-v1` — 제품 방향은 팀 확정, 도메인 시연 범위는 확인 필요.** 운영자가 AWS를 준비하고 사용자는 자신의 독립 도메인을 선택한다. 플랫폼 하위 도메인을 강제하지 않는다. **미리 확보한 도메인 연결 시연과 신규 구매부터의 자동화는 별개**이며, 구매 자동화를 이번 필수 구현으로 확정하지 않았다. 프런트의 도메인 MOCK·제안 API를 실제 등록·연결 성공으로 보지 않는다.

## 먼저 읽기

- [제품 방향·시연 범위·구현 후보](docs/PRODUCT_DIRECTION.md): 확정 결정과 제안, 코드 검증 근거, 완료 조건
- [공통 에이전트 지침](AGENTS.md) · [작업·동기화 절차](WORKFLOW.md): 진행 중 에이전트도 새 기준을 다시 읽고 개인 브랜치에 반영
- [남은 결정](docs/OPEN_QUESTIONS.md) · [회의 변경 기록](docs/MEETING_UPDATES.md)
- [운영자 AWS 준비·이전](docs/OPERATOR_AWS.md): 사용자 온보딩과 분리된 운영 절차

## 구성과 실행

- [front/](front/README.md): React·TypeScript·Vite. 기존 연결 화면은 전환 대상이며 기본 MOCK과 실제 API 모드를 구분한다.
- [back/](back/README.md) · [API 계약](back/API.md): FastAPI·MySQL, 소스·분석·계획·승인·배포 이력 저장.
- [infra/](infra/README.md): Terraform, ECS Fargate·ECR·ALB·RDS와 배포 스크립트. 제품 배포 대상은 AWS만이다.
- [sample-back/](sample-back/README.md) · [sample-front/](sample-front/README.md): 시연용 앱. 플랫폼과 구분한다.
- [CI 범위](docs/CI.md): 기존 검사 성공이 도메인 자동화나 실제 AWS 전체 배포 성공을 뜻하지 않는다.

플랫폼은 현재 발표자 PC에서 검증하며 Docker Compose는 개발용이다. 실행 명령은 각 README를 따른다. 제품 방향 확정을 실제 AWS 변경·도메인 구매·공개 서비스 전환 승인으로 해석하지 않는다.
