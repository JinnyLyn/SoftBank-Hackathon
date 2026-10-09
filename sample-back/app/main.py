import hashlib
import hmac
import os
import secrets
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "sample-front"
SESSION_COOKIE = "launchpad_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7
EMAIL_ERROR = "올바른 이메일 주소를 입력해 주세요."
LOGIN_ERROR = "이메일 또는 비밀번호가 올바르지 않습니다."
DUMMY_PASSWORD_HASH = "pbkdf2_sha256$600000$00000000000000000000000000000000$a44c04bc8cc043f1c9805d516f79c272e6e2c623381b763e1a8232f2f97c54d4"
SIGNUP_RATE_LIMIT = 5
SIGNUP_RATE_WINDOW_SECONDS = 60
MAX_SIGNUP_RATE_LIMIT_KEYS = 10_000
signup_attempts: dict[str, deque[float]] = {}
signup_lock = threading.Lock()
password_hash_slots = threading.BoundedSemaphore(4)


def database_settings() -> dict[str, object]:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL 환경 변수가 필요합니다.")
    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    if parsed.scheme != "mysql" or not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError("DATABASE_URL은 mysql://user:password@host:3306/database 형식이어야 합니다.")
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.strip("/"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }


def connection():
    return pymysql.connect(**database_settings())


def error(status: int, message: str) -> None:
    raise HTTPException(status_code=status, detail=message)


def public_user(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, count, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(count))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def signup_allowed(request: Request) -> bool:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with signup_lock:
        for address, attempts in list(signup_attempts.items()):
            while attempts and attempts[0] <= now - SIGNUP_RATE_WINDOW_SECONDS:
                attempts.popleft()
            if not attempts:
                del signup_attempts[address]
        if client not in signup_attempts and len(signup_attempts) >= MAX_SIGNUP_RATE_LIMIT_KEYS:
            return False
        attempts = signup_attempts.setdefault(client, deque())
        while attempts and attempts[0] <= now - SIGNUP_RATE_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= SIGNUP_RATE_LIMIT:
            return False
        attempts.append(now)
        return True


def limited_password_hash(password: str) -> str:
    if not password_hash_slots.acquire(blocking=False):
        error(429, "요청이 많습니다. 잠시 후 다시 시도해 주세요.")
    try:
        return password_hash(password)
    finally:
        password_hash_slots.release()


def cleaned(value: str) -> str:
    return value.strip()


def valid_email(email: str) -> bool:
    if not email or len(email) > 190 or any(ch.isspace() for ch in email):
        return False
    local, separator, domain = email.partition("@")
    return bool(local and separator and domain and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def current_user(request: Request, required: bool = False) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        if required:
            error(401, "로그인이 필요합니다.")
        return None
    with connection() as db, db.cursor() as cursor:
        cursor.execute(
            """SELECT u.id, u.name, u.email
                 FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token = %s AND s.expires_at > UTC_TIMESTAMP()""",
            (token,),
        )
        row = cursor.fetchone()
    if not row:
        if required:
            error(401, "로그인이 필요합니다.")
        return None
    return row


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def iso_time(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class SignupInput(BaseModel):
    name: str = ""
    email: str = ""
    password: str = ""


class LoginInput(BaseModel):
    email: str = ""
    password: str = ""


class IdeaInput(BaseModel):
    title: str = ""
    description: str = ""


app = FastAPI()


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.exception_handler(StarletteHTTPException)
async def framework_http_error(_: Request, exc: StarletteHTTPException):
    message = "지원하지 않는 요청입니다." if exc.status_code == 404 else "요청을 처리하지 못했습니다."
    return JSONResponse(status_code=exc.status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "요청 형식이 올바르지 않습니다."})


@app.exception_handler(Exception)
async def unexpected_error(_: Request, __: Exception):
    return JSONResponse(status_code=500, content={"error": "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})


@app.get("/health")
def health():
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/me")
def me(request: Request):
    user = current_user(request, required=True)
    return public_user(user)


@app.post("/api/signup", status_code=201)
def signup(payload: SignupInput, request: Request, response: Response):
    if not signup_allowed(request):
        error(429, "가입 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    name, email, password = cleaned(payload.name), cleaned(payload.email).lower(), payload.password
    if not 1 <= len(name) <= 30:
        error(400, "이름은 1~30자로 입력해 주세요.")
    if not valid_email(email):
        error(400, EMAIL_ERROR)
    if not 8 <= len(password) <= 128:
        error(400, "비밀번호는 8자 이상 128자 이하로 입력해 주세요.")
    token = secrets.token_urlsafe(32)
    try:
        with connection() as db, db.cursor() as cursor:
            cursor.execute("INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)", (name, email, limited_password_hash(password)))
            user_id = cursor.lastrowid
            cursor.execute("DELETE FROM sessions WHERE expires_at <= UTC_TIMESTAMP()")
            cursor.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 DAY))",
                (token, user_id),
            )
            db.commit()
    except pymysql.err.IntegrityError:
        error(409, "이미 가입된 이메일입니다.")
    set_session_cookie(response, token)
    return {"id": user_id, "name": name, "email": email}


@app.post("/api/login")
def login(payload: LoginInput, response: Response):
    email = cleaned(payload.email).lower()
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT id, name, email, password_hash FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
    encoded_password = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    if not verify_password(payload.password, encoded_password):
        error(401, LOGIN_ERROR)
    token = secrets.token_urlsafe(32)
    with connection() as db, db.cursor() as cursor:
        cursor.execute("DELETE FROM sessions WHERE expires_at <= UTC_TIMESTAMP()")
        cursor.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 DAY))",
            (token, user["id"]),
        )
        db.commit()
    set_session_cookie(response, token)
    return public_user(user)


@app.post("/api/logout", status_code=204)
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with connection() as db, db.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE token = %s", (token,))
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return None


@app.get("/api/ideas")
def list_ideas(request: Request):
    user = current_user(request)
    user_id = user["id"] if user else 0
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS count FROM users")
        users = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) AS count FROM ideas")
        ideas_count = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) AS count FROM votes")
        votes_count = cursor.fetchone()["count"]
        cursor.execute(
            """SELECT i.id, i.title, i.description, u.name AS author, i.created_at,
                      COUNT(v.idea_id) AS votes,
                      EXISTS(SELECT 1 FROM votes mine WHERE mine.idea_id = i.id AND mine.user_id = %s) AS voted
                 FROM ideas i JOIN users u ON u.id = i.user_id LEFT JOIN votes v ON v.idea_id = i.id
                GROUP BY i.id, i.title, i.description, u.name, i.created_at
                ORDER BY votes DESC, i.created_at DESC LIMIT 30""",
            (user_id,),
        )
        ideas = cursor.fetchall()
    for idea in ideas:
        idea["votes"] = int(idea["votes"])
        idea["voted"] = bool(idea["voted"])
        idea["created_at"] = iso_time(idea["created_at"])
    return {"stats": {"users": users, "ideas": ideas_count, "votes": votes_count}, "ideas": ideas}


@app.post("/api/ideas", status_code=201)
def create_idea(payload: IdeaInput, request: Request):
    user = current_user(request, required=True)
    title, description = cleaned(payload.title), cleaned(payload.description)
    if not 1 <= len(title) <= 80 or len(description) > 300:
        error(400, "제목은 1~80자, 설명은 300자 이하로 입력해 주세요.")
    with connection() as db, db.cursor() as cursor:
        cursor.execute("INSERT INTO ideas (user_id, title, description) VALUES (%s, %s, %s)", (user["id"], title, description))
        idea_id = cursor.lastrowid
        cursor.execute("SELECT created_at FROM ideas WHERE id = %s", (idea_id,))
        created_at = cursor.fetchone()["created_at"]
        db.commit()
    return {"id": idea_id, "title": title, "description": description, "author": user["name"], "votes": 0, "voted": False, "created_at": iso_time(created_at)}


@app.post("/api/ideas/{idea_id}/vote")
def vote(idea_id: int, request: Request):
    user = current_user(request, required=True)
    with connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT id FROM ideas WHERE id = %s FOR UPDATE", (idea_id,))
        if not cursor.fetchone():
            error(404, "존재하지 않는 아이디어입니다.")
        cursor.execute("SELECT 1 FROM votes WHERE idea_id = %s AND user_id = %s FOR UPDATE", (idea_id, user["id"]))
        voted = not bool(cursor.fetchone())
        if voted:
            cursor.execute("INSERT INTO votes (idea_id, user_id) VALUES (%s, %s)", (idea_id, user["id"]))
        else:
            cursor.execute("DELETE FROM votes WHERE idea_id = %s AND user_id = %s", (idea_id, user["id"]))
        cursor.execute("SELECT COUNT(*) AS count FROM votes WHERE idea_id = %s", (idea_id,))
        votes = cursor.fetchone()["count"]
        db.commit()
    return {"voted": voted, "votes": votes}


@app.get("/api/info")
def info():
    return {"hostname": socket.gethostname()}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def unknown_api(path: str):
    error(404, "지원하지 않는 요청입니다.")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
