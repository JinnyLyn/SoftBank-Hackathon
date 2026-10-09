# 10/8~10/9 변경 사항과 구현 상태

2026-10-09에 Notion의 미팅·미팅 준비 원문과 저장소를 확인했다. 준비 문서와 코드 예시는 확정 결정과 구분했다. 개인 연락처, 비밀값, 원문 전체는 옮기지 않았다.

## 회의에서 확인한 변경

| 근거 | 확인한 내용 | 저장소에 적용하는 해석 |
|---|---|---|
| [10/8 미팅](https://app.notion.com/p/3f38bee9ada48076add5f4705ad6a825) | 연결된 대상 × lean/balanced/roomy를 비교하고 조합 하나를 추천. `targets`는 비교 후보이며 둘 다 배포하는 것이 아님 | 두 환경 시연 목표와 개별 요청의 선택 대상 배포를 구분 |
| 같은 문서 | 분석·추천 시 추천 조합 코드를 미리 생성. 다른 조합 선택 시 그 조합만 생성. 코드 검토 화면에서는 AI를 다시 호출하지 않음 | 향후 플랫폼 통합 테스트의 흐름 기준. 현재 CI가 실제로 이 흐름을 검증한다고 표시하지 않음 |
| 같은 문서 | 금액은 코드·가격표로 계산. AI 코드 분석·실패 진단은 진서, 구성 추천·배포 코드 생성은 동현 | AGENTS의 분담 보완. 가격표·추천 알고리즘·모델 결정은 여전히 별도 확인 |
| [10/9 미팅](https://app.notion.com/p/3f48bee9ada480acb3decf0c237f56b2) | 웹 이름·비용, GitHub repo URL, 사용 규모, 접속 패턴, 선택적 설명·배포 주소 목록 | ZIP만 받는다는 기존 설명을 갱신. 정확한 필수 필드·API는 미결 |
| 같은 문서 | 도메인을 UI에서 받고 없으면 구매 안내 | 입력 방향을 기록. 인증서·도메인 라우팅 구현 완료의 증거로 보지 않음 |

## 제안·예시·미결

- [10/8 미팅 준비](https://app.notion.com/p/3f38bee9ada48008a39df4f3b86d82e8)는 빈 진행 체크리스트다. 별도 결정의 근거로 쓰지 않았다.
- [10/9 미팅 준비](https://app.notion.com/p/3f48bee9ada480f5be10ec63b337df6c)는 README의 수동 절차를 창업자 관점에서 비교한다. ID 생성·이미지 빌드/푸시·아키텍처/리전 자동 판별이 이미 구현됐다는 뜻은 아니다.
- 10/8에는 HCL 전체 생성 예시와 Terraform 실패 시 2~3회 재시도가 나온다. [10/7의 변수값 우선 결정](https://app.notion.com/p/3f28bee9ada480cea94acca757178e8b)을 대체하는 실행·검증·승인 기준은 확인되지 않았다.
- PostgreSQL·다중 클라우드·SQLite 변환 예시는 샘플 DB의 MySQL 8.4 계약을 변경하는 결정이 아니다. 리전·가격표·추천 방법·모델은 원문에서도 정할 항목으로 남아 있다.
- [10/9 QA 페이지](https://app.notion.com/p/3f48bee9ada480479dd8e65e04589a7c)는 비어 있었다. 전체 앱 테스트 완료 근거가 아니다.

## 코드와 실제 검증의 경계

확인 기준 main은 `14cb324`다. 아래 PR들은 이 확인 시점에 열려 있었으며 이번 CI PR이 대신 합치지 않는다.

| 영역 | 확인한 코드 | 이번 CI |
|---|---|---|
| `sample-front/` | main의 HTML·JavaScript, API 부재 시 브라우저 MOCK 자동 전환 | 실제 Chrome에서 MOCK 사용자 흐름, 실제 연동으로 오인하지 않는지 검사 |
| `sample-back/` | [PR #4](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/4), `d5816c1`: Python 3.12·FastAPI·MySQL 8.4, 별도 `init-db` 작업으로 테이블 초기화, 같은 서버의 정적 파일·API | 합쳐지면 실제 Docker/API/DB/브라우저 검사 활성화. 샘플의 구현을 플랫폼 전체 마이그레이션 정책으로 일반화하지 않음 |
| `front/` | [PR #7](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/7), `f5646f9`: React·TypeScript·Vite, 기본 MOCK, AWS/온프레미스, 예산·실패 수정 후 재승인. 최신 README는 SSO를 범위에서 제외 | 합쳐지면 잠긴 의존성으로 타입 검사·두 설정의 빌드. 실제 API·배포 검증 아님 |
| `infra/` | [PR #6](https://github.com/JinnyLyn/SoftBank-Hackathon/pull/6): bootstrap/foundation/앱 모듈·스크립트 | 합쳐지면 AWS 자격 증명 없는 정적 검사. 실제 배포 시험과 구분 |
| `back/` | main·확인한 개인 원격 브랜치에서 골격만 있음 | 전체 플랫폼 통합은 미검증. 코드가 생기면 검사 계약을 추가해야 함 |

[Terraform 설명](https://app.notion.com/p/3f48bee9ada480d0a30bfa849fd559a0)은 담당자의 AWS 시험 결과와 미적용 변경을 구분한다. 그 기록은 이번 CI가 AWS를 검증했다는 증거가 아니다. 공유 보안 그룹 소유권·신규 foundation 역할의 적용·실제 도메인·플랫폼 연동은 해당 문서의 미결/미검증 항목을 유지한다.
