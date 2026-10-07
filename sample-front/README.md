# sample-front: Launchpad 아이디어 보드 화면

Paved Clouds 배포 시연에 쓸 **샘플 웹의 프런트(HTML/CSS/JavaScript)**다. 서버 코드는 없다.
백엔드와 MySQL 연결은 샘플 백엔드 담당이 이 화면에 맞춰 붙인다.

## 화면 미리보기

백엔드 없이 예시 데이터 모드로 찍은 화면이다. 맨 위 안내 줄과 푸터의 `mock-browser`는 예시 모드에서만 보이고, 실제 백엔드가 연결되면 사라진다.

**메인 (PC)** — 라이트 / 다크 모드는 브라우저 설정을 따른다.

| 라이트 | 다크 |
|---|---|
| <img src="screenshots/main-light.png" alt="메인 화면 라이트 모드" width="440"> | <img src="screenshots/main-dark.png" alt="메인 화면 다크 모드" width="440"> |

**로그인 / 회원가입 / 모바일**

| 로그인 | 회원가입 | 모바일 메인 |
|---|---|---|
| <img src="screenshots/login.png" alt="로그인 화면" width="300"> | <img src="screenshots/signup.png" alt="회원가입 화면" width="300"> | <img src="screenshots/main-mobile.jpg" alt="모바일 메인 화면" width="180"> |

## 파일

| 파일 | 역할 |
|---|---|
| `index.html` | 메인: 소개, 통계, 아이디어 목록(응원 순), 작성 폼 |
| `login.html`, `signup.html` | 로그인, 회원가입 |
| `style.css` | 디자인. 외부 폰트·CDN 없이 시스템 글꼴만 쓰고, 라이트·다크 모드와 모바일을 지원한다 |
| `app.js` | 화면 동작. `fetch`로 `/api`를 호출한다 |
| `mock-api.js` | `MOCK:` 백엔드가 없을 때만 쓰이는 브라우저 안의 가짜 API. 연결이 끝나면 지워도 된다 |

## 화면 보는 법

- `index.html`을 브라우저로 열면 된다. 백엔드가 없으면 예시 데이터로 동작하고, 화면 맨 위에 "예시 데이터 모드" 안내가 뜬다.
- 가짜 데이터는 그 브라우저의 `localStorage`에만 있다. 가입, 로그인, 글쓰기, 응원이 모두 동작한다. 초기화하려면 브라우저 개발자 도구에서 저장소를 지운다.
- 서버로 열고 싶으면 이 폴더에서 `python -m http.server 8080`을 실행하고 http://localhost:8080 을 연다.

## 백엔드가 지켜야 할 것

이 폴더를 서버가 **루트(`/`)에서 정적 파일로** 내려주고, 아래 `/api`가 JSON으로 응답하면 화면은 그대로 동작한다. `/api`가 JSON으로 응답하는 순간부터 `mock-api.js`는 쓰이지 않는다. 언어와 프레임워크는 자유다.

- 요청·응답 본문은 JSON(UTF-8), 시간은 ISO 8601(UTC)이다.
- 로그인 상태는 서버가 내려주는 쿠키 `session`(`HttpOnly`)으로 유지한다. 화면은 쿠키를 직접 읽지 않는다.
- 오류는 `{"error": "사용자에게 보여줄 한국어 문장"}`이다. 화면은 이 문장을 그대로 보여준다.

| 메서드·경로 | 요청 본문 | 성공 | 오류 |
|---|---|---|---|
| `GET /api/me` | — | `200 {id, name, email}` | 로그인 안 됨 `401` |
| `POST /api/signup` | `{name, email, password}` | `201 {id, name, email}` + 세션 쿠키 | 입력 오류 `400`, 이미 가입된 이메일 `409` |
| `POST /api/login` | `{email, password}` | `200 {id, name, email}` + 세션 쿠키 | 틀림 `401` |
| `POST /api/logout` | — | `204` + 쿠키 삭제 | — |
| `GET /api/ideas` | — | `200 {stats, ideas}` (아래) | — |
| `POST /api/ideas` | `{title, description}` | `201` | 로그인 안 됨 `401`, 입력 오류 `400` |
| `POST /api/ideas/{id}/vote` | — | `200 {voted, votes}` (이미 응원했으면 취소) | 로그인 안 됨 `401`, 없는 글 `404` |
| `GET /api/info` | — | `200 {hostname}` | 선택. 없으면 푸터의 컨테이너 표시를 숨긴다 |

`GET /api/ideas` 응답 예시:

```json
{
  "stats": { "users": 5, "ideas": 5, "votes": 8 },
  "ideas": [
    { "id": 6, "title": "스타트업 지원사업 알리미", "description": "내 업종에 맞는 …",
      "author": "이도윤", "votes": 3, "voted": false, "created_at": "2026-10-07T01:55:59Z" }
  ]
}
```

- `ideas`는 응원 수 내림차순, 같으면 최신순으로 최대 30개다. `voted`는 요청한 사용자가 응원했는지이고, 로그인하지 않았으면 `false`다.
- 입력 규칙(서버에서 검증한다): 이름 1~30자, 이메일 형식·190자 이하(소문자로 저장), 비밀번호 8~128자(해시로 저장), 제목 1~80자, 설명 0~300자. 앞뒤 공백은 제거한다.
- 로그인 실패는 이메일이 없는 경우와 비밀번호가 틀린 경우에 같은 문구를 쓴다.

## 배포할 때 필요한 정보

백엔드는 어떤 언어·프레임워크로 만들어도 된다. 배포 쪽(Terraform 모듈)을 완성된 백엔드의 구성에 맞춘다. 기존 `ecs-web-app` 모듈이 맞지 않으면 이 앱에 맞는 모듈을 하나 더 만든다.

백엔드가 완성되면 아래를 알려 주면 된다.

| 알려 줄 것 | 모듈에 미치는 영향 |
|---|---|
| 컨테이너 개수 (앱 하나 / nginx + 앱 등) | 한 태스크에 컨테이너 여러 개를 넣을지 |
| 사용하는 포트와 헬스체크 경로 | `container_port`, `health_check_path` |
| DB 접속 정보를 받는 방식 (`DATABASE_URL` 하나 / 호스트·아이디·비밀번호 따로) | 비밀 값을 넣는 방식(`secrets`) |
| 파일을 저장하는지 (업로드 등) | 디스크 볼륨(EFS)이 필요한지 |
| 정적 파일과 `/api`를 한 서버가 주는지 | 대상 그룹과 리스너 구성 |

- 10/6 미팅에서 정해진 것: 샘플 웹은 MySQL을 쓰고, 로컬과 AWS에서 같은 엔진을 쓴다. 접속 정보를 `DATABASE_URL` 환경 변수로 주입하는 방식이 제안되어 있다.
- 비어 있는 DB에 테이블을 만드는 주체(DB 마이그레이션)는 팀 결정 전이다.
