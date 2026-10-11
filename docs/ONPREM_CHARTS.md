<!--
발표용: 16:9 슬라이드마다 차트 하나를 배치하고, SVG로 옮겨 비율을 유지한다.
흰 배경 · 기본 24px · 연결선 글자 22px · 맑은 고딕/Noto Sans KR.
구성요소는 파랑, 사용자 승인은 주황, 접속 성공은 초록, 실패 대응은 분홍.
색상 없이도 노드 문구와 화살표로 의미를 구분한다.
근거: docs/ONPREM.md, scripts/onprem_service.py, scripts/onprem.compose.yaml,
infra/worker/local_docker.py, infra/worker/worker.py, front/src/onprem/.
기준: 2026-10-10-managed-domains-v1 / origin/main 8145c21a70466b07717c1780e7f5385b6af5208f.
현재 요청에 따라 구현된 온프레미스 경로만 표현한다. 전체 제품 방향 변경은 아니다.
-->

## 온프레미스 배포 — 구성

```mermaid
%%{init: {"theme":"base","htmlLabels":false,"themeVariables":{"background":"#FFFFFF","fontFamily":"Malgun Gothic, Noto Sans KR, Apple SD Gothic Neo, sans-serif","fontSize":"24px","primaryColor":"#EFF6FF","primaryTextColor":"#0F172A","primaryBorderColor":"#2563EB","lineColor":"#475569","edgeLabelBackground":"#FFFFFF","clusterBkg":"#F8FAFC","clusterBorder":"#94A3B8"},"themeCSS":".edgeLabel { font-size: 22px; } .cluster-label { font-weight: 700; }","flowchart":{"htmlLabels":false,"curve":"linear","nodeSpacing":36,"rankSpacing":52,"padding":20}}}%%
flowchart LR
    USER["사용자<br/>브라우저"]

    subgraph PC["플랫폼 실행 PC"]
        WEB["플랫폼 화면<br/>React"]
        API["플랫폼 API<br/>FastAPI"]
        DB[("플랫폼 DB<br/>MySQL")]
        ANALYZER["구성 분석기<br/>규칙 기반"]
        WORKER["배포 실행기<br/>계획 생성 · 실행"]
        APP["배포 앱<br/>Docker Compose<br/>필요 시 앱 전용 DB"]

        WEB --> API
        API <--> DB
        API <--> ANALYZER
        API <--> WORKER
        WORKER --> APP
    end

    USER --> WEB
    USER -. HTTP 접속 .-> APP

    classDef person fill:#FFF7ED,stroke:#C2410C,color:#0F172A,stroke-width:2px;
    classDef app fill:#ECFDF5,stroke:#047857,color:#0F172A,stroke-width:2px;
    class USER person;
    class APP app;
    linkStyle default stroke:#475569,stroke-width:2px;
```

## 온프레미스 배포 — 흐름

```mermaid
%%{init: {"theme":"base","htmlLabels":false,"themeVariables":{"background":"#FFFFFF","fontFamily":"Malgun Gothic, Noto Sans KR, Apple SD Gothic Neo, sans-serif","fontSize":"24px","primaryColor":"#EFF6FF","primaryTextColor":"#0F172A","primaryBorderColor":"#2563EB","lineColor":"#475569","edgeLabelBackground":"#FFFFFF"},"themeCSS":".edgeLabel { font-size: 22px; }","flowchart":{"htmlLabels":false,"curve":"linear","nodeSpacing":40,"rankSpacing":44,"padding":20}}}%%
flowchart LR
    SOURCE["소스 입력<br/>ZIP / 공개 GitHub"]
    PLAN["구성 분석<br/>계획 생성"]
    APPROVE["사용자 검토 · 승인<br/>구성 · 자원 한도"]
    DEPLOY["빌드 · 앱 실행<br/>Docker Compose"]
    HEALTH{"HTTP<br/>응답 확인"}
    OPEN["배포 앱 접속<br/>로컬 HTTP 주소"]
    FIX["로그 확인 · 수정<br/>소스 재제출"]

    SOURCE --> PLAN --> APPROVE --> DEPLOY --> HEALTH
    HEALTH -->|성공| OPEN
    PLAN -->|분석·계획 실패| FIX
    DEPLOY -->|배포 실패| FIX
    HEALTH -->|실패| FIX
    FIX --> SOURCE

    classDef approval fill:#FFF7ED,stroke:#C2410C,color:#0F172A,stroke-width:2px;
    classDef success fill:#ECFDF5,stroke:#047857,color:#0F172A,stroke-width:2px;
    classDef failure fill:#FFF1F2,stroke:#BE123C,color:#0F172A,stroke-width:2px;
    class APPROVE approval;
    class OPEN success;
    class FIX failure;
    linkStyle default stroke:#475569,stroke-width:2px;
```
