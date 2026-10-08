# Launchpad 백엔드

FastAPI가 `sample-front` 정적 파일과 `/api`를 함께 제공합니다. DB는 **MySQL 8.4**이며, 테이블은 앱 시작 시 자동 생성됩니다.

로그인 세션은 MySQL의 `sessions` 테이블에 저장됩니다. 세션 쿠키는 HttpOnly이며 7일 뒤 만료됩니다. 따라서 앱 컨테이너가 재시작되거나 새 ECS 태스크로 교체되어도 만료 전 로그인 상태를 유지합니다.

## 로컬 실행

```bash
cd sample-back
docker compose up --build
```

브라우저에서 http://localhost:8000 을 열면 됩니다. 종료할 때는 `docker compose down`을 사용합니다. 데이터를 함께 지우려면 `docker compose down -v`를 사용하세요.

로컬 MySQL은 호스트의 기존 `3306` 사용을 피하기 위해 `localhost:3307`에 열립니다. 앱 컨테이너는 Docker 내부 네트워크에서 표준 포트 `db:3306`으로 접속합니다.

## 배포 정보

- 컨테이너: 1개 (FastAPI가 화면과 `/api`를 모두 제공, MySQL은 AWS RDS 사용)
- 포트: `8000`
- 헬스체크: `GET /health`
- DB 환경 변수: `DATABASE_URL` 하나 (`mysql://user:password@host:3306/database`)
- RDS와 로컬 모두 MySQL `8.4`, 문자셋 `utf8mb4`

`COOKIE_SECURE=true`로 설정하면 HTTPS에서만 세션 쿠키가 전송됩니다. 로컬 compose에서는 HTTP 테스트를 위해 `false`입니다.
