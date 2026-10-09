#!/usr/bin/env python3
"""격리된 CI 샘플 앱의 실제 HTTP 계약 검사. 테스트 데이터는 임시 DB에 남긴다."""

import argparse
import ipaddress
import json
import sys
from datetime import datetime
from http.cookiejar import CookieJar
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


TIMEOUT = 10
COOKIE = "launchpad_session"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url):
        self.url = url
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), NoRedirect())

    def request(self, path, status=200, method="GET", payload=None, headers=None):
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=data, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=TIMEOUT)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError) as error:
            raise AssertionError(f"{method} {path}: connection failed ({type(error).__name__})") from None
        with response:
            require(response.status == status, f"{method} {path}: expected HTTP {status}, got {response.status}")
            body = response.read(1_000_001)
            require(len(body) <= 1_000_000, f"{path}: response too large")
            return body, response.headers

    def json(self, path, status=200, method="GET", payload=None, headers=None):
        body, response_headers = self.request(path, status, method, payload, headers)
        require(response_headers.get_content_type() == "application/json", f"{path}: expected JSON, HTML/mock fallback is not accepted")
        try:
            result = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise AssertionError(f"{path}: invalid JSON") from None
        require(isinstance(result, dict), f"{path}: expected JSON object")
        if status >= 400:
            require(isinstance(result.get("error"), str) and bool(result["error"]), f"{path}: missing JSON error")
        return result, response_headers


def session_header(headers):
    cookies = SimpleCookie()
    for value in headers.get_all("Set-Cookie", []):
        cookies.load(value)
    require(COOKIE in cookies, "session cookie missing")
    require(bool(cookies[COOKIE]["httponly"]), "session cookie must be HttpOnly")
    return f"{COOKIE}={cookies[COOKIE].value}"


def listing(client):
    result, _ = client.json("/api/ideas")
    require(isinstance(result.get("ideas"), list), "ideas list missing")
    stats = result.get("stats", {})
    require(all(type(stats.get(key)) is int and stats[key] >= 0 for key in ("users", "ideas", "votes")), "invalid idea stats")
    return result


def listed_idea(client, idea_id):
    matches = [idea for idea in listing(client)["ideas"] if idea.get("id") == idea_id]
    require(len(matches) == 1, "created idea missing from independent API read")
    return matches[0]


def check(url, expect_unhealthy):
    anonymous = Client(url)
    if expect_unhealthy:
        anonymous.json("/health", status=500)
        print("PASS: /health returns JSON 500 with database unavailable")
        return
    health, _ = anonymous.json("/health")
    require(health.get("status") == "ok", "health did not report ok")
    print("PASS: real API + MySQL via /health (DB engine/version supplied by CI runner)")
    for path, content_types, marker in (
        ("/", ("text/html",), b"<html"),
        ("/login.html", ("text/html",), b"<html"),
        ("/signup.html", ("text/html",), b"<html"),
        ("/app.js", ("text/javascript", "application/javascript"), b""),
        ("/mock-api.js", ("text/javascript", "application/javascript"), b""),
        ("/style.css", ("text/css",), b""),
    ):
        body, headers = anonymous.request(path)
        require(headers.get_content_type() in content_types and bool(body), f"{path}: invalid static asset")
        require(marker in body.lower(), f"{path}: expected HTML page")
    before = listing(anonymous)["stats"]
    anonymous.json("/api/me", status=401)
    anonymous.json("/api/ideas", status=401, method="POST", payload={"title": "비로그인"})
    anonymous.json("/api/ideas/1/vote", status=401, method="POST")
    anonymous.json("/api/ci-unknown-" + uuid4().hex, status=404)
    anonymous.json("/api/signup", status=400, method="POST", payload={"name": "", "email": "invalid", "password": "short"})

    identifier = uuid4().hex
    email = f"ci-{identifier}@example.test"
    password = uuid4().hex
    credentials = {"email": email, "password": password}
    client = Client(url)
    user, headers = client.json("/api/signup", status=201, method="POST", payload={"name": " CI 사용자 ", "email": f" {email.upper()} ", "password": password})
    require(user.get("name") == "CI 사용자" and user.get("email") == email and type(user.get("id")) is int, "signup normalization/user contract failed")
    old_cookie = session_header(headers)
    me, _ = client.json("/api/me")
    require(me == user, "signup session is not authenticated")
    anonymous.json("/api/signup", status=409, method="POST", payload={"name": "중복", **credentials})
    bad, _ = anonymous.json("/api/login", status=401, method="POST", payload={"email": email, "password": uuid4().hex})
    absent, _ = anonymous.json("/api/login", status=401, method="POST", payload={"email": f"absent-{identifier}@example.test", "password": password})
    require(bad == absent, "login errors disclose whether email exists")
    body, _ = client.request("/api/logout", status=204, method="POST")
    require(not body, "logout 204 must have an empty body")
    require(not any(cookie.name == COOKIE for cookie in client.cookies), "logout did not expire the client cookie")
    client.json("/api/me", status=401)
    Client(url).json("/api/me", status=401, headers={"Cookie": old_cookie})
    logged_in, headers = client.json("/api/login", method="POST", payload=credentials)
    require(logged_in == user, "login returned a different user")
    session_header(headers)
    print("PASS: authentication, normalized signup, duplicate/error contracts and session invalidation")

    title = f"한글 <b>아이디어</b> {identifier[:8]}"
    description = "실제 DB에 저장되는 설명 <script>example</script>"
    idea, _ = client.json("/api/ideas", status=201, method="POST", payload={"title": title, "description": description})
    require(type(idea.get("id")) is int and idea["id"] > 0, "invalid idea id")
    require(idea.get("title") == title and idea.get("description") == description and idea.get("author") == user["name"], "idea text/author changed")
    require(type(idea.get("votes")) is int and idea["votes"] == 0 and idea.get("voted") is False, "invalid initial vote state")
    try:
        created_at = datetime.fromisoformat(idea["created_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AssertionError("idea created_at is not an ISO timestamp") from None
    require(created_at.tzinfo is not None, "idea timestamp must include timezone")
    reader = Client(url)
    reader.json("/api/login", method="POST", payload=credentials)
    require(listed_idea(reader, idea["id"]) == idea, "idea not persisted across independent authenticated clients")
    after = listing(reader)["stats"]
    require(after == {"users": before["users"] + 1, "ideas": before["ideas"] + 1, "votes": before["votes"]}, "create/list stats mismatch")
    for voted, count in ((True, 1), (False, 0)):
        vote, _ = client.json(f"/api/ideas/{idea['id']}/vote", method="POST")
        require(vote == {"voted": voted, "votes": count}, "vote toggle response mismatch")
        stored = listed_idea(reader, idea["id"])
        require(stored.get("voted") is voted and stored.get("votes") == count, "vote state not persisted")
        require(listing(reader)["stats"]["votes"] == before["votes"] + count, "global vote count mismatch")
    for payload in ({"title": ""}, {"title": "x" * 81}, {"title": "valid", "description": "x" * 301}, {"title": []}):
        client.json("/api/ideas", status=400, method="POST", payload=payload)
    client.json("/api/ideas/not-an-integer/vote", status=400, method="POST")
    client.json("/api/ideas/9223372036854775807/vote", status=404, method="POST")
    require(listing(reader)["stats"] == after, "invalid requests changed persisted counts")
    print("PASS: Korean/HTML-looking text persistence, authors, timestamps, stats and vote/unvote")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="격리된 CI 앱의 loopback HTTP URL")
    parser.add_argument("--expect-unhealthy", action="store_true", help="DB 중지 후 /health의 JSON 500 응답 검사")
    args = parser.parse_args()
    try:
        parsed = urlsplit(args.url)
        # IP literals only: no DNS resolution or redirect can send writes remotely.
        require(parsed.scheme == "http" and ipaddress.ip_address(parsed.hostname).is_loopback, "--url must use a loopback HTTP IP address")
        require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in ("", "/"), "--url must be a bare loopback origin")
        require(parsed.port is not None, "--url must include the CI app port")
        check(args.url.rstrip("/"), args.expect_unhealthy)
    except (AssertionError, ValueError, TypeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
