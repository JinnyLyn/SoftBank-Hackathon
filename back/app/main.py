from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4
from starlette.concurrency import run_in_threadpool

import pymysql
from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.database import check_database, connect
from app.schemas import (
    AnalysisIn,
    AnalysisOut,
    ApproveIn,
    ConnectionIn,
    ConnectionOut,
    ConnectionCompleteIn,
    ConnectionFailIn,
    ConnectionRoleCallbackIn,
    PendingConnectionOut,
    DeploymentCreateIn,
    DeploymentDetailOut,
    DeploymentEventOut,
    DeploymentOut,
    FrontDeployStatus,
    GitHubProjectIn,
    PlanIn,
    PlanOut,
    ProjectListOut,
    ProjectOut,
    RollbackCandidateOut,
    RollbackPlanSummaryIn,
    RollbackRequestIn,
    WorkerEventIn,
)
from app.settings import ConfigurationError
from app.uploads import save_github_zip, save_project_zip, save_terraform_plan

app = FastAPI(
    title="Paved Clouds Backend",
    version="0.1.0",
    description="프로젝트 입력, 분석 결과, 승인된 배포 계획과 실행 이력을 관리하는 API입니다.",
)
_cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

_SECRET_KEY = re.compile(
    r"(?i)(^|[_-])(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)([_-]|$)"
)
_KEY_ASSIGNMENT = re.compile(
    r"""(?ix)(?<![A-Z0-9_-])(?P<key_quote>["']?)(?P<key>[A-Z_][A-Z0-9_-]*)(?P=key_quote)\s*[:=]\s*"""
)
_ASSIGNMENT_VALUE = re.compile(
    r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]"']+"""
)
_CREDENTIAL_URL = re.compile(r"(?i)\b(mysql(?:\+pymysql)?|https?)://[^/\s:@]+:[^/\s@]+@")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")


@app.exception_handler(pymysql.MySQLError)
async def database_error_handler(_: Request, exc: pymysql.MySQLError) -> JSONResponse:
    # Database details may contain hostnames or query text; expose only a stable error.
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": "데이터베이스에 연결할 수 없습니다."},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
    return JSONResponse(status_code=exc.status_code, content={"error": message}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in error.get("loc", ())), "message": error.get("msg", "invalid value")}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "요청 형식이 올바르지 않습니다.", "details": details},
    )


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(_: Request, exc: ConfigurationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"error": "백엔드 설정이 완료되지 않았습니다."},
    )


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness probe. This endpoint does not depend on MySQL."""
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
def readiness() -> dict[str, str]:
    """Readiness probe for the API and its configured database."""
    check_database()
    return {"status": "ready", "database": "ok"}


@app.get("/api/connections", response_model=list[ConnectionOut], tags=["connections"])
def list_connections() -> list[ConnectionOut]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connections ORDER BY created_at, id")
        return [_connection_out(row) for row in cursor.fetchall()]


@app.get("/api/worker/connections/pending", response_model=list[PendingConnectionOut], tags=["worker"])
def list_pending_connections(
    x_worker_token: str | None = Header(default=None),
) -> list[PendingConnectionOut]:
    _require_worker(x_worker_token)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, provider, name, external_id, fields, created_at, aws_account_id, role_arn "
            "FROM connections WHERE status = 'pending' ORDER BY created_at, id"
        )
        return [
            PendingConnectionOut(
                id=UUID(row["id"]), provider=row["provider"], name=row["name"],
                external_id=row["external_id"], fields=_json_value(row["fields"]),
                created_at=_as_utc(row["created_at"]),
                account_id=row.get("aws_account_id"), role_arn=row.get("role_arn"),
            )
            for row in cursor.fetchall()
        ]


@app.post("/api/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED, tags=["connections"])
def create_connection(body: ConnectionIn) -> ConnectionOut:
    clean_name = body.name.strip()
    if not clean_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "연결 이름을 입력해 주세요.")
    _reject_secret_fields(body.fields)
    connection_id = uuid4()
    external_id = f"pc-{os.urandom(16).hex()}"
    setup_url = _aws_setup_url(external_id, connection_id)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO connections (id, provider, name, status, detail, setup_url, external_id, fields)
               VALUES (%s, 'aws', %s, 'pending', 'AWS CloudFormation 연결을 기다리는 중입니다.', %s, %s, %s)""",
            (str(connection_id), clean_name, setup_url, external_id, _json(body.fields)),
        )
        connection.commit()
        cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
        row = cursor.fetchone()
    return _connection_out(row)


@app.put("/api/connections/{connection_id}", response_model=ConnectionOut, tags=["connections"])
def update_connection(connection_id: UUID, body: ConnectionIn) -> ConnectionOut:
    clean_name = body.name.strip()
    if not clean_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "연결 이름을 입력해 주세요.")
    _reject_secret_fields(body.fields)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connections WHERE id = %s FOR UPDATE", (str(connection_id),))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "연결을 찾을 수 없습니다.")
        external_id = row["external_id"]
        status_value = "connected" if row["status"] == "connected" else "pending"
        setup_url = row["setup_url"] if status_value == "connected" else _aws_setup_url(external_id, connection_id)
        detail = row["detail"] if status_value == "connected" else "AWS CloudFormation 연결을 기다리는 중입니다."
        cursor.execute(
            """UPDATE connections SET name = %s, status = %s, detail = %s, error = NULL,
                      setup_url = %s, fields = %s WHERE id = %s""",
            (clean_name, status_value, detail, setup_url, _json(body.fields), str(connection_id)),
        )
        connection.commit()
        cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
        updated = cursor.fetchone()
    return _connection_out(updated)


@app.post("/api/connections/{connection_id}/check", response_model=ConnectionOut, tags=["connections"])
def check_connection(connection_id: UUID) -> ConnectionOut:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "연결을 찾을 수 없습니다.")
    return _connection_out(row)


@app.post("/api/connections/{connection_id}/role-callback", response_model=ConnectionOut, tags=["connections"])
def connection_role_callback(connection_id: UUID, body: ConnectionRoleCallbackIn) -> ConnectionOut:
    """Accept the capability-authenticated CloudFormation output callback.

    The ExternalId is an unguessable per-connection capability. The actual AWS
    role validation remains the worker's responsibility before /complete.
    """
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connections WHERE id = %s FOR UPDATE", (str(connection_id),))
        row = cursor.fetchone()
        if row is None or row["provider"] != "aws" or not hmac.compare_digest(
            str(row["external_id"]), body.external_id
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AWS 연결을 찾을 수 없습니다.")
        stored_account = row.get("aws_account_id")
        stored_role = row.get("role_arn")
        if stored_account is not None or stored_role is not None:
            if stored_account == body.account_id and stored_role == body.role_arn:
                return _connection_out(row)
            raise HTTPException(status.HTTP_409_CONFLICT, "다른 AWS 역할이 이미 보고된 연결입니다.")
        if row["status"] not in {"pending", "connected"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "오류 상태의 연결에는 역할을 등록할 수 없습니다.")
        cursor.execute(
            "UPDATE connections SET aws_account_id = %s, role_arn = %s WHERE id = %s "
            "AND aws_account_id IS NULL AND role_arn IS NULL",
            (body.account_id, body.role_arn, str(connection_id)),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "역할 정보가 동시에 변경되었습니다. 다시 조회해 주세요.")
        connection.commit()
        cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
        updated = cursor.fetchone()
    return _connection_out(updated)


@app.delete("/api/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["connections"])
def delete_connection(connection_id: UUID) -> None:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM connections WHERE id = %s", (str(connection_id),))
        if cursor.rowcount != 1:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "연결을 찾을 수 없습니다.")
        connection.commit()


@app.post("/api/worker/connections/{connection_id}/fail", response_model=ConnectionOut, tags=["worker"])
def fail_connection(
    connection_id: UUID,
    body: ConnectionFailIn,
    x_worker_token: str | None = Header(default=None),
) -> ConnectionOut:
    _require_worker(x_worker_token)
    safe_error = _redact(body.error)[:2000]
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connections WHERE id = %s FOR UPDATE", (str(connection_id),))
        row = cursor.fetchone()
        if row is None or row["provider"] != "aws":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AWS 연결을 찾을 수 없습니다.")
        if row["status"] == "error" and row.get("error") == safe_error:
            return _connection_out(row)
        if row["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, "대기 중인 연결만 실패 처리할 수 있습니다.")
        cursor.execute(
            "UPDATE connections SET status = 'error', detail = %s, error = %s WHERE id = %s",
            ("AWS 역할 연결 확인에 실패했습니다.", safe_error, str(connection_id)),
        )
        connection.commit()
        cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
        updated = cursor.fetchone()
    return _connection_out(updated)


@app.post("/api/worker/connections/{connection_id}/complete", response_model=ConnectionOut, tags=["worker"])
def complete_connection(
    connection_id: UUID,
    body: ConnectionCompleteIn,
    x_worker_token: str | None = Header(default=None),
) -> ConnectionOut:
    _require_worker(x_worker_token)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM connections WHERE id = %s FOR UPDATE", (str(connection_id),))
        row = cursor.fetchone()
        if row is None or row["provider"] != "aws":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "AWS 연결을 찾을 수 없습니다.")
        if row["status"] == "connected":
            if row.get("aws_account_id") == body.account_id and row.get("role_arn") == body.role_arn:
                return _connection_out(row)
            if row.get("aws_account_id") is None and row.get("role_arn") is None:
                cursor.execute(
                    "UPDATE connections SET aws_account_id = %s, role_arn = %s "
                    "WHERE id = %s AND status = 'connected' "
                    "AND aws_account_id IS NULL AND role_arn IS NULL",
                    (body.account_id, body.role_arn, str(connection_id)),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise HTTPException(status.HTTP_409_CONFLICT, "기존 연결 정보가 동시에 변경되었습니다.")
                connection.commit()
                cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
                return _connection_out(cursor.fetchone())
            raise HTTPException(status.HTTP_409_CONFLICT, "이미 다른 AWS 역할로 연결된 항목입니다.")
        if row["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, "대기 중인 연결만 완료 처리할 수 있습니다.")
        if row.get("aws_account_id") is not None or row.get("role_arn") is not None:
            if row.get("aws_account_id") != body.account_id or row.get("role_arn") != body.role_arn:
                raise HTTPException(status.HTTP_409_CONFLICT, "CloudFormation이 보고한 역할 정보와 일치하지 않습니다.")
        cursor.execute(
            """UPDATE connections SET status = 'connected', detail = %s, setup_url = NULL,
                      error = NULL, aws_account_id = %s, role_arn = %s WHERE id = %s""",
            (f"AWS 계정 {body.account_id}", body.account_id, body.role_arn, str(connection_id)),
        )
        connection.commit()
        cursor.execute("SELECT * FROM connections WHERE id = %s", (str(connection_id),))
        row = cursor.fetchone()
    return _connection_out(row)


@app.post("/api/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, tags=["projects"])
async def create_project(
    request: Request,
    name: str = Query(min_length=1, max_length=120),
    filename: str = Query(default="project.zip", min_length=1, max_length=512),
    expected_users: Literal["~100", "~1,000", "~10,000", "10,000+"] | None = None,
    traffic_pattern: Literal["steady", "peak", "unknown"] | None = None,
    monthly_budget_usd: Decimal | None = Query(default=None, ge=0, max_digits=12, decimal_places=4),
    purpose: str | None = Query(default=None, max_length=2000),
) -> ProjectOut:
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type not in {"application/zip", "application/octet-stream"}:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type은 application/zip이어야 합니다.")

    clean_name = name.strip()
    if not clean_name or any(not char.isprintable() for char in clean_name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "프로젝트 이름을 입력해 주세요.")
    clean_purpose = _clean_purpose(purpose)
    if clean_purpose is not None:
        _reject_secret_fields(clean_purpose)
    safe_filename = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    safe_filename = "".join(char for char in safe_filename if char.isprintable())[:255]
    project_id = uuid4()
    stored = await save_project_zip(request.stream(), safe_filename, project_id)
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO projects
                   (id, name, source_filename, source_path, source_sha256, source_size_bytes, source_type,
                    expected_users, traffic_pattern, monthly_budget_usd, purpose)
                   VALUES (%s, %s, %s, %s, %s, %s, 'zip', %s, %s, %s, %s)""",
                (
                    str(project_id), clean_name, safe_filename, str(stored.path),
                    stored.sha256, stored.size_bytes, expected_users, traffic_pattern, monthly_budget_usd,
                    clean_purpose,
                ),
            )
            connection.commit()
            cursor.execute(
                """SELECT id, name, source_filename, source_sha256, source_size_bytes,
                          source_type, source_url, source_ref, expected_users, traffic_pattern,
                          monthly_budget_usd, purpose, created_at
                   FROM projects WHERE id = %s""",
                (str(project_id),),
            )
            row = cursor.fetchone()
        return _project_out(row)
    except Exception:
        stored.path.unlink(missing_ok=True)
        raise


@app.post("/api/projects/github", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, tags=["projects"])
async def create_github_project(body: GitHubProjectIn) -> ProjectOut:
    clean_name = body.name.strip()
    if not clean_name or any(not char.isprintable() for char in clean_name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "프로젝트 이름을 입력해 주세요.")
    clean_purpose = _clean_purpose(body.purpose)
    if clean_purpose is not None:
        _reject_secret_fields(clean_purpose)
    project_id = uuid4()
    stored, source_url, source_ref = await run_in_threadpool(
        save_github_zip, body.repository_url, body.ref, project_id
    )
    filename = f"{source_url.rstrip('/').rsplit('/', 1)[-1]}.zip"
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO projects
                   (id, name, source_filename, source_path, source_sha256, source_size_bytes,
                    source_type, source_url, source_ref, expected_users, traffic_pattern,
                    monthly_budget_usd, purpose)
                   VALUES (%s, %s, %s, %s, %s, %s, 'github', %s, %s, %s, %s, %s, %s)""",
                (str(project_id), clean_name, filename, str(stored.path), stored.sha256,
                 stored.size_bytes, source_url, source_ref, body.expected_users, body.traffic_pattern,
                 body.monthly_budget_usd, clean_purpose),
            )
            connection.commit()
            cursor.execute(
                """SELECT id, name, source_filename, source_sha256, source_size_bytes,
                          source_type, source_url, source_ref, expected_users, traffic_pattern,
                          monthly_budget_usd, purpose, created_at
                   FROM projects WHERE id = %s""",
                (str(project_id),),
            )
            row = cursor.fetchone()
        return _project_out(row)
    except Exception:
        stored.path.unlink(missing_ok=True)
        raise


@app.get("/api/projects", response_model=ProjectListOut, tags=["projects"])
def list_projects(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> ProjectListOut:
    before: tuple[datetime, str] | None = _decode_cursor(cursor) if cursor else None
    with connect() as connection, connection.cursor() as db_cursor:
        if before:
            db_cursor.execute(
                """SELECT id, name, source_filename, source_sha256, source_size_bytes,
                          source_type, source_url, source_ref, expected_users, traffic_pattern,
                          monthly_budget_usd, purpose, created_at
                   FROM projects WHERE (created_at, id) < (%s, %s)
                   ORDER BY created_at DESC, id DESC LIMIT %s""",
                (before[0], before[1], limit + 1),
            )
        else:
            db_cursor.execute(
                """SELECT id, name, source_filename, source_sha256, source_size_bytes,
                          source_type, source_url, source_ref, expected_users, traffic_pattern,
                          monthly_budget_usd, purpose, created_at
                   FROM projects ORDER BY created_at DESC, id DESC LIMIT %s""",
                (limit + 1,),
            )
        rows = db_cursor.fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more and page else None
    return ProjectListOut(items=[_project_out(row) for row in page], next_cursor=next_cursor)


@app.get("/api/projects/{project_id}", response_model=ProjectOut, tags=["projects"])
def get_project(project_id: UUID) -> ProjectOut:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT id, name, source_filename, source_sha256, source_size_bytes,
                      source_type, source_url, source_ref, expected_users, traffic_pattern,
                      monthly_budget_usd, purpose, created_at
               FROM projects WHERE id = %s""",
            (str(project_id),),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")
    return _project_out(row)


@app.post(
    "/api/projects/{project_id}/analyses",
    response_model=AnalysisOut,
    status_code=status.HTTP_201_CREATED,
    tags=["analysis"],
)
def record_analysis(project_id: UUID, body: AnalysisIn) -> AnalysisOut:
    analysis_id = uuid4()
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_sha256 FROM projects WHERE id = %s", (str(project_id),))
        project = cursor.fetchone()
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")
        if not hmac.compare_digest(project["source_sha256"], body.source_sha256):
            raise HTTPException(status.HTTP_409_CONFLICT, "분석한 ZIP이 현재 프로젝트 파일과 일치하지 않습니다.")
        _reject_secret_fields(body.result)
        cursor.execute(
            """INSERT INTO analyses (id, project_id, schema_version, source_sha256, result)
               VALUES (%s, %s, %s, %s, %s)""",
            (str(analysis_id), str(project_id), body.schema_version, body.source_sha256, _json(body.result)),
        )
        connection.commit()
        cursor.execute("SELECT * FROM analyses WHERE id = %s", (str(analysis_id),))
        row = cursor.fetchone()
    return _analysis_out(row)


@app.get("/api/projects/{project_id}/analyses/latest", response_model=AnalysisOut, tags=["analysis"])
def latest_analysis(project_id: UUID) -> AnalysisOut:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM projects WHERE id = %s", (str(project_id),))
        if cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")
        cursor.execute(
            "SELECT * FROM analyses WHERE project_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (str(project_id),),
        )
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "분석 결과가 아직 없습니다.")
    return _analysis_out(row)


@app.post("/api/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED, tags=["plans"])
def create_plan(body: PlanIn) -> PlanOut:
    _reject_secret_fields(body.variables)
    _reject_secret_fields(body.summary)
    if body.target == "aws" and (body.terraform_plan_sha256 is None or body.cost_estimate is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "AWS 계획에는 저장 plan의 SHA-256과 월 비용 추정치가 필요합니다.",
        )
    plan_id = uuid4()
    payload = body.model_dump(mode="json")
    fingerprint = _fingerprint(payload)
    cost_estimate = body.cost_estimate.model_dump(mode="json") if body.cost_estimate else None
    with connect() as connection, connection.cursor() as cursor:
        _lock_project(cursor, str(body.project_id))
        if body.analysis_id:
            cursor.execute(
                "SELECT project_id FROM analyses WHERE id = %s",
                (str(body.analysis_id),),
            )
            analysis = cursor.fetchone()
            if analysis is None or analysis["project_id"] != str(body.project_id):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "분석 결과가 프로젝트와 일치하지 않습니다.")
        cursor.execute(
            "UPDATE deployment_plans SET status = 'superseded' WHERE project_id = %s AND status = 'awaiting_approval'",
            (str(body.project_id),),
        )
        cursor.execute(
            """INSERT INTO deployment_plans
               (id, project_id, analysis_id, target, module_id, variables, summary, cost_estimate,
                terraform_plan_sha256, fingerprint)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                str(plan_id), str(body.project_id), str(body.analysis_id) if body.analysis_id else None,
                body.target, body.module_id, _json(body.variables), body.summary,
                _json(cost_estimate) if cost_estimate is not None else None,
                body.terraform_plan_sha256, fingerprint,
            ),
        )
        connection.commit()
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        row = cursor.fetchone()
    return _plan_out(row)


@app.get("/api/projects/{project_id}/plans", response_model=list[PlanOut], tags=["plans"])
def list_project_plans(project_id: UUID) -> list[PlanOut]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM projects WHERE id = %s", (str(project_id),))
        if cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")
        cursor.execute(
            "SELECT * FROM deployment_plans WHERE project_id = %s ORDER BY created_at DESC, id DESC LIMIT 100",
            (str(project_id),),
        )
        return [_plan_out(row) for row in cursor.fetchall()]


@app.get("/api/plans/{plan_id}", response_model=PlanOut, tags=["plans"])
def get_plan(plan_id: UUID) -> PlanOut:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        row = cursor.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 계획을 찾을 수 없습니다.")
    return _plan_out(row)


@app.post("/api/plans/{plan_id}/approve", response_model=PlanOut, tags=["plans"])
def approve_plan(plan_id: UUID, body: ApproveIn) -> PlanOut:
    with connect() as connection, connection.cursor() as cursor:
        _lock_plan_project(cursor, str(plan_id))
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s FOR UPDATE", (str(plan_id),))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 계획을 찾을 수 없습니다.")
        if row["status"] != "awaiting_approval":
            raise HTTPException(status.HTTP_409_CONFLICT, "승인 대기 중인 계획만 승인할 수 있습니다.")
        if row["target"] != "aws":
            raise HTTPException(status.HTTP_409_CONFLICT, "현재 배포 대상은 AWS 클라우드입니다.")
        if not hmac.compare_digest(row["fingerprint"], body.expected_fingerprint):
            raise HTTPException(status.HTTP_409_CONFLICT, "승인하려는 계획이 표시된 계획과 다릅니다.")
        if row["target"] == "aws" and not _terraform_plan_is_valid(row):
            raise HTTPException(status.HTTP_409_CONFLICT, "승인된 SHA-256과 일치하는 저장 Terraform plan이 없습니다.")
        if row.get("operation_type") == "rollback" and row.get("terraform_plan_summary") is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "롤백 Terraform plan의 diff 요약을 먼저 저장해야 합니다.")
        _validate_rollback_context(cursor, row)
        cursor.execute(
            """UPDATE deployment_plans
               SET status = 'approved', approved_at = CURRENT_TIMESTAMP(6), approved_fingerprint = %s
               WHERE id = %s AND status = 'awaiting_approval' AND fingerprint = %s""",
            (body.expected_fingerprint, str(plan_id), body.expected_fingerprint),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "계획 상태가 변경되어 승인하지 못했습니다.")
        connection.commit()
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        approved = cursor.fetchone()
    return _plan_out(approved)


@app.post(
    "/api/deployments",
    response_model=DeploymentOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["deployments"],
)
def queue_deployment(body: DeploymentCreateIn) -> DeploymentOut:
    deployment_id = uuid4()
    with connect() as connection, connection.cursor() as cursor:
        _lock_plan_project(cursor, str(body.plan_id))
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s FOR UPDATE", (str(body.plan_id),))
        plan = cursor.fetchone()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 계획을 찾을 수 없습니다.")
        if plan["status"] != "approved" or not plan["approved_at"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "승인된 배포 계획만 실행 대기열에 넣을 수 있습니다.")
        if plan["target"] != "aws":
            raise HTTPException(status.HTTP_409_CONFLICT, "현재 배포 대상은 AWS 클라우드입니다.")
        if not hmac.compare_digest(plan["fingerprint"], body.expected_fingerprint) or not hmac.compare_digest(
            plan["approved_fingerprint"] or "", body.expected_fingerprint
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "승인 후 배포 계획이 변경되었습니다.")
        if plan["target"] == "aws" and not _terraform_plan_is_valid(plan):
            raise HTTPException(status.HTTP_409_CONFLICT, "승인된 Terraform plan이 없거나 SHA-256 검증에 실패했습니다.")
        if plan.get("operation_type") == "rollback" and plan.get("terraform_plan_summary") is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "롤백 Terraform plan의 diff 요약이 없어 실행 대기열에 넣을 수 없습니다.")
        cursor.execute(
            """SELECT id FROM deployments WHERE project_id = %s AND operation_type = 'rollback'
               AND status IN ('provisioning', 'deploying') LIMIT 1 FOR UPDATE""",
            (plan["project_id"],),
        )
        if cursor.fetchone() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "실행 중인 롤백이 완료된 후 새 배포를 등록하세요.")
        _validate_rollback_context(cursor, plan)
        cursor.execute(
            """UPDATE deployment_plans SET status = 'consumed'
               WHERE id = %s AND status = 'approved' AND approved_fingerprint = %s""",
            (str(body.plan_id), body.expected_fingerprint),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "이 승인으로 이미 배포가 등록되었거나 계획 상태가 변경되었습니다.")
        cursor.execute(
            """INSERT INTO deployments
               (id, plan_id, project_id, target, operation_type, rollback_from_deployment_id,
                rollback_to_deployment_id, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued')""",
            (
                str(deployment_id), plan["id"], plan["project_id"], plan["target"], plan.get("operation_type", "deploy"),
                plan.get("rollback_from_deployment_id"), plan.get("rollback_to_deployment_id"),
            ),
        )
        _insert_event(cursor, deployment_id, "info", "queued", "승인된 배포 계획을 실행 대기열에 등록했습니다.", None)
        connection.commit()
        cursor.execute("SELECT * FROM deployments WHERE id = %s", (str(deployment_id),))
        row = cursor.fetchone()
    return _deployment_out(row)


@app.get("/api/deployments", tags=["deployments"])
def list_deployments(
    project_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, Any]]:
    with connect() as connection, connection.cursor() as cursor:
        query = """SELECT d.*, p.module_id, p.variables, p.cost_estimate, pr.name AS project_name
                   FROM deployments d
                   JOIN deployment_plans p ON p.id = d.plan_id
                   JOIN projects pr ON pr.id = d.project_id"""
        if project_id:
            cursor.execute(
                query + " WHERE d.project_id = %s ORDER BY d.created_at DESC, d.id DESC LIMIT %s",
                (str(project_id), limit),
            )
        else:
            cursor.execute(query + " ORDER BY d.created_at DESC, d.id DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        variables = _json_value(row["variables"]) or {}
        cost = _json_value(row["cost_estimate"]) or {}
        status_alias = "running" if row["status"] in {"queued", "provisioning", "deploying", "rolling_back"} else (
            "success" if row["status"] == "healthy" else "failed"
        )
        result.append({
            **_deployment_out(row).model_dump(mode="json"),
            "app": row["project_name"],
            "version": str(row["id"])[:8],
            "tier": variables.get("tier", row["module_id"]),
            "provider": "aws" if row["target"] == "aws" else "onprem",
            "target": variables.get("connection_name", "AWS"),
            "monthlyUsd": float(cost.get("amount", 0)),
            "status": status_alias,
            "createdAt": _as_utc(row["created_at"]).isoformat(),
        })
    return result


@app.get(
    "/api/deployments/{deployment_id}/rollback-candidate",
    response_model=RollbackCandidateOut,
    tags=["deployments"],
)
def rollback_candidate(deployment_id: UUID) -> RollbackCandidateOut:
    """Return only the immediately previous healthy AWS deployment as a rollback candidate."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM deployments WHERE id = %s", (str(deployment_id),))
        failed = cursor.fetchone()
        if failed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 이력을 찾을 수 없습니다.")
        if failed["operation_type"] != "deploy" or failed["status"] != "failed":
            return RollbackCandidateOut(rollback_available=False, reason="not_failed")
        cursor.execute(
            "SELECT id FROM deployments WHERE project_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (failed["project_id"],),
        )
        latest = cursor.fetchone()
        if latest is None or latest["id"] != str(deployment_id):
            return RollbackCandidateOut(rollback_available=False, reason="not_failed")
        cursor.execute(
            "SELECT id FROM deployments WHERE project_id = %s AND created_at < %s LIMIT 1",
            (failed["project_id"], failed["created_at"]),
        )
        if cursor.fetchone() is None:
            return RollbackCandidateOut(rollback_available=False, reason="first_deployment")
        cursor.execute(
            """SELECT d.*, p.terraform_plan_sha256, p.terraform_plan_path
               FROM deployments d
               JOIN deployment_plans p ON p.id = d.plan_id
               WHERE d.project_id = %s AND d.target = 'aws' AND d.status = 'healthy'
                 AND d.updated_at <= %s
               ORDER BY d.updated_at DESC, d.id DESC LIMIT 1""",
            (failed["project_id"], failed["updated_at"]),
        )
        candidate = cursor.fetchone()
    if candidate is None:
        return RollbackCandidateOut(rollback_available=False, reason="no_previous_healthy")
    return RollbackCandidateOut(
        rollback_available=True,
        reason="available",
        target_deployment_id=UUID(candidate["id"]),
        target_plan_id=UUID(candidate["plan_id"]),
    )


@app.post(
    "/api/deployments/{deployment_id}/rollback",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    tags=["deployments"],
)
def queue_rollback(deployment_id: UUID, body: RollbackRequestIn) -> PlanOut:
    """Create a fresh rollback plan draft; approval happens only after worker uploads its plan and diff."""
    plan_id = uuid4()
    with connect() as connection, connection.cursor() as cursor:
        _lock_deployment_project(cursor, str(deployment_id))
        cursor.execute("SELECT * FROM deployments WHERE id = %s FOR UPDATE", (str(deployment_id),))
        failed = cursor.fetchone()
        if failed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 이력을 찾을 수 없습니다.")
        if failed["operation_type"] != "deploy" or failed["status"] != "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "실패한 일반 배포에 대해서만 롤백을 요청할 수 있습니다.")
        cursor.execute(
            """SELECT id FROM deployments WHERE project_id = %s
               ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE""",
            (failed["project_id"],),
        )
        latest = cursor.fetchone()
        if latest is None or latest["id"] != str(deployment_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "후속 배포가 있어 이전 실패 배포의 롤백 승인이 무효화되었습니다.",
            )
        cursor.execute(
            """SELECT d.*, p.module_id, p.variables, p.cost_estimate
               FROM deployments d
               JOIN deployment_plans p ON p.id = d.plan_id
               WHERE d.project_id = %s AND d.target = 'aws' AND d.status = 'healthy'
                 AND d.updated_at <= %s
               ORDER BY d.updated_at DESC, d.id DESC LIMIT 1 FOR UPDATE""",
            (failed["project_id"], failed["updated_at"]),
        )
        candidate = cursor.fetchone()
        if candidate is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "검증된 이전 정상 버전이 없어 롤백할 수 없습니다. 분석·수정 후 새 계획을 승인해 재배포하세요.",
            )
        if candidate["id"] != str(body.expected_target_deployment_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "사용자가 확인한 롤백 대상이 현재 이전 정상 버전과 다릅니다.")
        cursor.execute(
            """SELECT id FROM deployment_plans
               WHERE rollback_from_deployment_id = %s AND status IN ('awaiting_approval', 'approved')
               LIMIT 1 FOR UPDATE""",
            (str(deployment_id),),
        )
        if cursor.fetchone() is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "이 실패 배포에 대한 새 롤백 계획이 이미 승인 대기 또는 승인 상태입니다.")
        variables = _json_value(candidate["variables"])
        cost_estimate = _json_value(candidate["cost_estimate"])
        payload = {
            "project_id": failed["project_id"], "target": "aws", "operation_type": "rollback",
            "rollback_from_deployment_id": str(deployment_id), "rollback_to_deployment_id": candidate["id"],
            "module_id": candidate["module_id"], "variables": variables,
            "cost_estimate": cost_estimate, "terraform_plan_sha256": None,
        }
        cursor.execute(
            """INSERT INTO deployment_plans
               (id, project_id, target, operation_type, rollback_from_deployment_id, rollback_to_deployment_id,
                module_id, variables, summary, cost_estimate, fingerprint)
               VALUES (%s, %s, 'aws', 'rollback', %s, %s, %s, %s, %s, %s, %s)""",
            (
                str(plan_id), failed["project_id"], str(deployment_id), candidate["id"], candidate["module_id"],
                _json(variables), "사용자가 요청한 이전 정상 버전 롤백 계획", _json(cost_estimate), _fingerprint(payload),
            ),
        )
        _insert_event(
            cursor, deployment_id, "info", "rollback_plan_requested",
            "사용자가 롤백 계획 생성을 요청했습니다. 새 Terraform plan과 diff 승인이 필요합니다.",
            {"rollback_plan_id": str(plan_id), "target_deployment_id": candidate["id"]},
        )
        connection.commit()
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        rollback_plan = cursor.fetchone()
    return _plan_out(rollback_plan)


@app.get("/api/deployments/{deployment_id}", response_model=DeploymentDetailOut, tags=["deployments"])
def get_deployment(deployment_id: UUID) -> DeploymentDetailOut:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM deployments WHERE id = %s", (str(deployment_id),))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 이력을 찾을 수 없습니다.")
        cursor.execute(
            "SELECT * FROM deployment_events WHERE deployment_id = %s ORDER BY created_at, id",
            (str(deployment_id),),
        )
        events = cursor.fetchall()
    payload = _deployment_out(row).model_dump()
    payload["events"] = [_event_out(event) for event in events]
    return DeploymentDetailOut(**payload)


@app.get("/api/projects/{project_id}/status", response_model=FrontDeployStatus, tags=["deployments"])
def project_deployment_status(project_id: UUID) -> FrontDeployStatus:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM projects WHERE id = %s", (str(project_id),))
        if cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")
        cursor.execute(
            "SELECT * FROM deployments WHERE project_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (str(project_id),),
        )
        deployment = cursor.fetchone()
        if deployment is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 이력이 아직 없습니다.")
        cursor.execute(
            "SELECT message FROM deployment_events WHERE deployment_id = %s ORDER BY created_at, id",
            (deployment["id"],),
        )
        log = [_redact(row["message"]) for row in cursor.fetchall()]
    state = "running" if deployment["status"] in {"queued", "provisioning", "deploying", "rolling_back"} else (
        "success" if deployment["status"] == "healthy" else "failed"
    )
    return FrontDeployStatus(state=state, log=log, url=deployment["url"])


@app.post("/api/worker/deployments/claim", status_code=status.HTTP_200_OK, tags=["worker"])
def claim_deployment(x_worker_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_worker(x_worker_token)
    with connect() as connection, connection.cursor() as cursor:
        while True:
            # Discover without taking child-row locks; every lifecycle writer
            # acquires the project first, then re-reads mutable rows with locks.
            cursor.execute(
                """SELECT d.project_id FROM deployments d
                   JOIN deployment_plans p ON p.id = d.plan_id
                   WHERE d.status = 'queued' AND d.target = 'aws' AND p.target = 'aws'
                   ORDER BY d.created_at, d.id LIMIT 1"""
            )
            candidate = cursor.fetchone()
            if candidate is None:
                connection.rollback()
                return {"job": None}
            _lock_project(cursor, candidate["project_id"])
            cursor.execute(
                """SELECT d.id AS deployment_id, d.status, d.target, d.operation_type,
                          d.rollback_from_deployment_id, d.rollback_to_deployment_id, d.plan_id, d.project_id,
                          p.module_id, p.variables, p.summary, p.cost_estimate, p.terraform_plan_sha256,
                          p.terraform_plan_path,
                          pr.source_path, pr.source_filename, pr.source_sha256
                   FROM deployments d
                   JOIN deployment_plans p ON p.id = d.plan_id
                   JOIN projects pr ON pr.id = d.project_id
                   WHERE d.status = 'queued' AND d.target = 'aws' AND p.target = 'aws'
                     AND d.project_id = %s
                   ORDER BY d.created_at, d.id
                   LIMIT 1 FOR UPDATE""",
                (candidate["project_id"],),
            )
            job = cursor.fetchone()
            if job is None:
                connection.rollback()
                continue
            failure_message = None
            failure_event = "rollback_context_invalidated"
            try:
                _validate_rollback_context(cursor, job, exclude_deployment_id=job["deployment_id"])
            except HTTPException as error:
                if error.status_code != status.HTTP_409_CONFLICT:
                    raise
                failure_message = error.detail
            if failure_message is None and job["target"] == "aws" and not _terraform_plan_is_valid(job):
                failure_message = "대기 작업의 Terraform plan 파일이 없거나 SHA-256 검증에 실패했습니다."
                failure_event = "terraform_plan_validation_failed"
            if failure_message is not None:
                cursor.execute(
                    "UPDATE deployments SET status = 'failed', url = NULL WHERE id = %s AND status = 'queued'",
                    (job["deployment_id"],),
                )
                if cursor.rowcount == 1:
                    _insert_event(
                        cursor,
                        UUID(job["deployment_id"]),
                        "error",
                        failure_event,
                        failure_message,
                        None,
                    )
                    connection.commit()
                else:
                    connection.rollback()
                # Quarantine the bad job and keep looking so it cannot block later queued work.
                continue
            cursor.execute(
                "UPDATE deployments SET status = 'provisioning' WHERE id = %s AND status = 'queued'",
                (job["deployment_id"],),
            )
            _insert_event(
                cursor,
                UUID(job["deployment_id"]),
                "info",
                "provisioning",
                "롤백 작업자가 실행을 시작했습니다." if job["operation_type"] == "rollback" else "배포 작업자가 실행을 시작했습니다.",
                None,
            )
            connection.commit()
            job["status"] = "provisioning"
            job["variables"] = _json_value(job["variables"])
            job["cost_estimate"] = _json_value(job["cost_estimate"])
            return {"job": _json_safe(job)}


@app.post("/api/worker/plans/{plan_id}/terraform-plan", tags=["worker"])
async def upload_terraform_plan(
    plan_id: UUID,
    request: Request,
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_worker(x_worker_token)
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type은 application/octet-stream이어야 합니다.")

    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        plan = cursor.fetchone()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 계획을 찾을 수 없습니다.")
    if plan["target"] != "aws" or plan["status"] != "awaiting_approval" or not plan["terraform_plan_sha256"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "plan artifact를 받을 수 있는 AWS 승인 대기 계획이 아닙니다.")
    if plan["terraform_plan_path"] and _terraform_plan_is_valid(plan):
        return {
            "plan_id": str(plan_id),
            "sha256": plan["terraform_plan_sha256"],
            "ready": True,
            "reused": True,
        }

    stored = await save_terraform_plan(request.stream(), plan_id, plan["terraform_plan_sha256"])
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE deployment_plans SET terraform_plan_path = %s
                   WHERE id = %s AND target = 'aws' AND status = 'awaiting_approval'
                     AND terraform_plan_sha256 = %s AND terraform_plan_path <=> %s""",
                (str(stored.path), str(plan_id), stored.sha256, plan["terraform_plan_path"]),
            )
            if cursor.rowcount != 1:
                cursor.execute("SELECT * FROM deployment_plans WHERE id = %s FOR UPDATE", (str(plan_id),))
                current = cursor.fetchone()
                if (
                    current is not None
                    and current["target"] == "aws"
                    and current["status"] == "awaiting_approval"
                    and current["terraform_plan_sha256"] == stored.sha256
                    and _terraform_plan_is_valid(current)
                ):
                    connection.rollback()
                    stored.path.unlink(missing_ok=True)
                    return {
                        "plan_id": str(plan_id),
                        "sha256": stored.sha256,
                        "ready": True,
                        "reused": True,
                    }
                connection.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, "plan 저장 중 계획 상태가 바뀌었습니다.")
            connection.commit()
    except Exception:
        stored.path.unlink(missing_ok=True)
        raise
    return {"plan_id": str(plan_id), "sha256": stored.sha256, "size_bytes": stored.size_bytes, "ready": True}


@app.post("/api/worker/rollback-plans/{plan_id}/terraform-plan", tags=["worker"])
async def upload_rollback_terraform_plan(
    plan_id: UUID,
    request: Request,
    x_terraform_plan_sha256: str | None = Header(default=None),
    x_worker_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Store a newly generated rollback plan before it can be shown for approval."""
    _require_worker(x_worker_token)
    if not x_terraform_plan_sha256 or not re.fullmatch(r"[a-f0-9]{64}", x_terraform_plan_sha256):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "X-Terraform-Plan-SHA256은 64자리 소문자 hex여야 합니다.")
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type은 application/octet-stream이어야 합니다.")
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        plan = cursor.fetchone()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "롤백 계획을 찾을 수 없습니다.")
    if plan.get("operation_type") != "rollback" or plan["status"] != "awaiting_approval" or plan["terraform_plan_path"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "새 Terraform plan을 받을 수 있는 롤백 승인 대기 계획이 아닙니다.")
    stored = await save_terraform_plan(request.stream(), plan_id, x_terraform_plan_sha256)
    try:
        with connect() as connection, connection.cursor() as cursor:
            payload = _plan_fingerprint_payload(plan, terraform_plan_sha256=stored.sha256)
            cursor.execute(
                """UPDATE deployment_plans SET terraform_plan_sha256 = %s, terraform_plan_path = %s, fingerprint = %s
                   WHERE id = %s AND operation_type = 'rollback' AND status = 'awaiting_approval'
                     AND terraform_plan_path IS NULL""",
                (stored.sha256, str(stored.path), _fingerprint(payload), str(plan_id)),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                stored.path.unlink(missing_ok=True)
                raise HTTPException(status.HTTP_409_CONFLICT, "롤백 계획 상태가 변경되어 Terraform plan을 저장하지 못했습니다.")
            connection.commit()
    except Exception:
        stored.path.unlink(missing_ok=True)
        raise
    return {"plan_id": str(plan_id), "sha256": stored.sha256, "size_bytes": stored.size_bytes, "ready": False}


@app.post("/api/worker/rollback-plans/{plan_id}/summary", response_model=PlanOut, tags=["worker"])
def save_rollback_plan_summary(
    plan_id: UUID, body: RollbackPlanSummaryIn, x_worker_token: str | None = Header(default=None)
) -> PlanOut:
    _require_worker(x_worker_token)
    _reject_secret_fields(body.summary)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s FOR UPDATE", (str(plan_id),))
        plan = cursor.fetchone()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "롤백 계획을 찾을 수 없습니다.")
        if plan.get("operation_type") != "rollback" or plan["status"] != "awaiting_approval":
            raise HTTPException(status.HTTP_409_CONFLICT, "diff를 저장할 수 있는 롤백 승인 대기 계획이 아닙니다.")
        if not _terraform_plan_is_valid(plan) or not hmac.compare_digest(plan["terraform_plan_sha256"] or "", body.terraform_plan_sha256):
            raise HTTPException(status.HTTP_409_CONFLICT, "저장된 Terraform plan과 일치하는 diff만 기록할 수 있습니다.")
        payload = _plan_fingerprint_payload(plan, terraform_plan_summary=body.summary)
        cursor.execute(
            "UPDATE deployment_plans SET terraform_plan_summary = %s, fingerprint = %s WHERE id = %s",
            (_json(body.summary), _fingerprint(payload), str(plan_id)),
        )
        connection.commit()
        cursor.execute("SELECT * FROM deployment_plans WHERE id = %s", (str(plan_id),))
        updated = cursor.fetchone()
    return _plan_out(updated)


@app.post("/api/worker/deployments/{deployment_id}/events", response_model=DeploymentOut, tags=["worker"])
def record_worker_event(
    deployment_id: UUID,
    body: WorkerEventIn,
    x_worker_token: str | None = Header(default=None),
) -> DeploymentOut:
    _require_worker(x_worker_token)
    body_dict = body.model_dump(mode="json")
    body_dict["message"] = _redact(body.message)
    body_dict["details"] = _redact_tree(body.details) if body.details is not None else None
    with connect() as connection, connection.cursor() as cursor:
        _lock_deployment_project(cursor, str(deployment_id))
        cursor.execute("SELECT * FROM deployments WHERE id = %s FOR UPDATE", (str(deployment_id),))
        current = cursor.fetchone()
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 이력을 찾을 수 없습니다.")
        allowed = {
            "queued": {"provisioning", "failed"},
            "provisioning": {"deploying", "failed"},
            "deploying": {"healthy", "failed"},
            "failed": {"rolling_back"},
            "rolling_back": {"rolled_back", "failed"},
            "healthy": set(),
            "rolled_back": set(),
        }
        same_status = body.status == current["status"]
        if (current["operation_type"] == "rollback" and not same_status
                and body.status in {"provisioning", "deploying", "healthy"}):
            _validate_rollback_context(cursor, current, exclude_deployment_id=str(deployment_id))
        if current["operation_type"] == "rollback" and body.status in {"rolling_back", "rolled_back"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "롤백 실행은 일반 배포와 같은 상태 흐름을 사용합니다.")
        if not same_status and body.status not in allowed[current["status"]]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"현재 상태({current['status']})에서 {body.status} 상태로 변경할 수 없습니다.",
            )
        if body.status == "healthy" and not same_status and not body.url:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "healthy 상태로 변경하려면 url이 필요합니다.")
        if not same_status:
            cursor.execute(
                """UPDATE deployments SET status = %s, url = %s WHERE id = %s""",
                (body.status, body.url if body.status == "healthy" else None, str(deployment_id)),
            )
        _insert_event(
            cursor,
            deployment_id,
            body.level,
            body.event_type,
            body_dict["message"],
            body_dict["details"],
        )
        connection.commit()
        cursor.execute("SELECT * FROM deployments WHERE id = %s", (str(deployment_id),))
        row = cursor.fetchone()
    return _deployment_out(row)


def _lock_project(cursor: Any, project_id: str) -> None:
    cursor.execute("SELECT id FROM projects WHERE id = %s FOR UPDATE", (project_id,))
    if cursor.fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "프로젝트를 찾을 수 없습니다.")


def _lock_plan_project(cursor: Any, plan_id: str) -> None:
    cursor.execute("SELECT project_id FROM deployment_plans WHERE id = %s", (plan_id,))
    plan = cursor.fetchone()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 계획을 찾을 수 없습니다.")
    _lock_project(cursor, plan["project_id"])


def _lock_deployment_project(cursor: Any, deployment_id: str) -> None:
    cursor.execute("SELECT project_id FROM deployments WHERE id = %s", (deployment_id,))
    deployment = cursor.fetchone()
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "배포 이력을 찾을 수 없습니다.")
    _lock_project(cursor, deployment["project_id"])


def _validate_rollback_context(
    cursor: Any, plan: dict[str, Any], *, exclude_deployment_id: str = "",
) -> None:
    """Revalidate under the project lock using current (locking) reads."""
    if plan.get("operation_type") != "rollback":
        return
    cursor.execute(
        """SELECT id FROM deployments WHERE project_id = %s AND id <> %s
           AND status IN ('queued', 'provisioning', 'deploying', 'rolling_back')
           LIMIT 1 FOR UPDATE""",
        (plan["project_id"], exclude_deployment_id),
    )
    if cursor.fetchone() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "다른 대기 또는 실행 중인 배포가 있어 롤백할 수 없습니다.")
    cursor.execute(
        """SELECT * FROM deployments WHERE project_id = %s AND id <> %s
           ORDER BY created_at DESC, id DESC LIMIT 1 FOR UPDATE""",
        (plan["project_id"], exclude_deployment_id),
    )
    failed = cursor.fetchone()
    if (failed is None or failed["id"] != plan.get("rollback_from_deployment_id")
            or failed["operation_type"] != "deploy" or failed["status"] != "failed"
            or failed["target"] != "aws"):
        raise HTTPException(status.HTTP_409_CONFLICT, "후속 배포 또는 상태 변경으로 롤백 승인이 무효화되었습니다.")
    cursor.execute(
        """SELECT id FROM deployments
           WHERE project_id = %s AND target = 'aws' AND status = 'healthy' AND updated_at <= %s
           ORDER BY updated_at DESC, id DESC LIMIT 1 FOR UPDATE""",
        (plan["project_id"], failed["updated_at"]),
    )
    candidate = cursor.fetchone()
    if candidate is None or candidate["id"] != plan.get("rollback_to_deployment_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "이전 정상 버전이 변경되어 롤백 승인이 무효화되었습니다.")


def _require_worker(token: str | None) -> None:
    configured = os.getenv("WORKER_API_TOKEN", "")
    if not configured or not token or not hmac.compare_digest(configured, token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "작업자 인증에 실패했습니다.")


def _terraform_plan_is_valid(row: dict[str, Any]) -> bool:
    artifact_path = row.get("terraform_plan_path")
    expected = row.get("terraform_plan_sha256")
    if not artifact_path or not expected:
        return False
    digest = hashlib.sha256()
    try:
        with open(artifact_path, "rb") as artifact:
            while chunk := artifact.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return False
    return hmac.compare_digest(digest.hexdigest(), expected)


def _insert_event(cursor: Any, deployment_id: UUID, level: str, event_type: str, message: str, details: Any) -> None:
    cursor.execute(
        """INSERT INTO deployment_events (deployment_id, level, event_type, message, details)
           VALUES (%s, %s, %s, %s, %s)""",
        (
            str(deployment_id), level, _redact(event_type)[:80], _redact(message)[:4000],
            _json(_redact_tree(details)) if details is not None else None,
        ),
    )


def _reject_secret_fields(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _is_secret_key(str(key)):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"비밀값으로 보이는 필드는 저장할 수 없습니다: {path}{key}",
                )
            _reject_secret_fields(nested, f"{path}{key}.")
    elif isinstance(value, list):
        for item in value:
            _reject_secret_fields(item, path)
    elif isinstance(value, str) and (
        _contains_inline_secret(value)
        or _CREDENTIAL_URL.search(value)
        or _BEARER.search(value)
        or _AWS_ACCESS_KEY.search(value)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"비밀값으로 보이는 내용은 저장할 수 없습니다: {path.rstrip('.') or 'value'}",
        )


def _redact(text: str) -> str:
    text = _CREDENTIAL_URL.sub(lambda match: f"{match.group(1)}://[REDACTED]@", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _AWS_ACCESS_KEY.sub("[REDACTED_AWS_ACCESS_KEY]", text)

    parts = []
    end = 0
    for start, stop, redacted in _inline_secret_values(text):
        if start < end:
            continue
        parts.extend((text[end:start], redacted))
        end = stop
    parts.append(text[end:])
    return "".join(parts)


def _inline_secret_values(text: str):
    # Scan assignment keys without consuming nonsensitive outer values. Otherwise
    # `environment: API_KEY=...` hides its inner assignment from finditer().
    for assignment in _KEY_ASSIGNMENT.finditer(text):
        start = assignment.end()
        value = _ASSIGNMENT_VALUE.match(text, start)
        if not value:
            continue
        decoded = None
        stop = value.end()
        if text[start] in '{["':
            try:
                decoded, stop = json.JSONDecoder().raw_decode(text, start)
            except (ValueError, RecursionError):
                # A malformed structured secret still must not expose its tail.
                if text[start] in "{[":
                    stop = len(text)
        if _is_secret_key(assignment.group("key")):
            raw = text[start:stop]
            quote = raw[0] if len(raw) >= 2 and raw[0] in {"'", '"'} and raw[-1] == raw[0] else ""
            yield start, stop, f"{quote}[REDACTED]{quote}"
        elif isinstance(decoded, str) and _contains_inline_secret(decoded):
            # Decode serialized log/JSON strings so escaped inner keys are seen.
            yield start, stop, json.dumps(_redact(decoded), ensure_ascii=False)


def _contains_inline_secret(text: str) -> bool:
    """Detect sensitive assignments while preserving ordinary values like python:3.12."""
    return next(_inline_secret_values(text), None) is not None


def _is_secret_key(key: str) -> bool:
    # Normalize camelCase/PascalCase and acronym boundaries before checking
    # the same sensitive-name list used for snake_case and kebab-case keys.
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    separated = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", separated)
    return _SECRET_KEY.search(separated) is not None


def _redact_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_secret_key(str(key)) else _redact_tree(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_tree(item) for item in value]
    if isinstance(value, str):
        return _redact(value)
    return value


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_fingerprint_payload(
    row: dict[str, Any], *, terraform_plan_sha256: str | None = None, terraform_plan_summary: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The exact fields a user approves; rollback artifacts receive their digest only after generation."""
    return {
        "project_id": row["project_id"], "analysis_id": row.get("analysis_id"), "target": row["target"],
        "operation_type": row.get("operation_type", "deploy"),
        "rollback_from_deployment_id": row.get("rollback_from_deployment_id"),
        "rollback_to_deployment_id": row.get("rollback_to_deployment_id"),
        "module_id": row["module_id"], "variables": _json_value(row["variables"]),
        "summary": row["summary"], "cost_estimate": _json_value(row["cost_estimate"]),
        "terraform_plan_sha256": terraform_plan_sha256 if terraform_plan_sha256 is not None else row.get("terraform_plan_sha256"),
        "terraform_plan_summary": (terraform_plan_summary if terraform_plan_summary is not None
                                   else (_json_value(row["terraform_plan_summary"])
                                         if row.get("terraform_plan_summary") else None)),
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _json_safe(value: Any) -> Any:
    return jsonable_encoder(value)


def _project_out(row: dict[str, Any]) -> ProjectOut:
    return ProjectOut(
        id=UUID(row["id"]), name=row["name"], source_filename=row["source_filename"],
        source_sha256=row["source_sha256"], source_size_bytes=row["source_size_bytes"],
        source_type=row["source_type"], source_url=row.get("source_url"), source_ref=row.get("source_ref"),
        expected_users=row.get("expected_users"), traffic_pattern=row.get("traffic_pattern"),
        monthly_budget_usd=row.get("monthly_budget_usd"), purpose=row.get("purpose"),
        created_at=_as_utc(row["created_at"]),
    )


def _clean_purpose(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if any(not char.isprintable() and char not in "\r\n\t" for char in cleaned):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "서비스 설명에 제어 문자를 사용할 수 없습니다.")
    return cleaned or None


def _connection_out(row: dict[str, Any]) -> ConnectionOut:
    return ConnectionOut(
        id=UUID(row["id"]), provider="aws", name=row["name"], status=row["status"],
        detail=row["detail"], error=row.get("error"), setupUrl=row.get("setup_url"),
        checkedAt=_as_utc(row.get("updated_at") or row["created_at"]),
        fields=_json_value(row["fields"]),
        accountId=row.get("aws_account_id"), roleArn=row.get("role_arn"),
    )


def _aws_setup_url(external_id: str, connection_id: UUID | None = None) -> str | None:
    template_url = os.getenv("AWS_CONNECTION_TEMPLATE_URL", "").strip()
    if not template_url:
        return None
    parsed = urlsplit(template_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigurationError("AWS_CONNECTION_TEMPLATE_URL must be a credential-free HTTPS URL")
    params = {
        "templateURL": template_url,
        "stackName": "PavedCloudsConnection",
        "param_ExternalId": external_id,
    }
    public_api_base_url = os.getenv("PUBLIC_API_BASE_URL", "").strip().rstrip("/")
    if public_api_base_url:
        callback_base = urlsplit(public_api_base_url)
        if (callback_base.scheme != "https" or not callback_base.hostname or callback_base.username
                or callback_base.password or callback_base.query or callback_base.fragment):
            raise ConfigurationError("PUBLIC_API_BASE_URL must be a credential-free HTTPS origin or base path")
        if connection_id is None:
            raise ConfigurationError("connection_id is required when PUBLIC_API_BASE_URL is configured")
        params["param_RoleCallbackUrl"] = f"{public_api_base_url}/api/connections/{connection_id}/role-callback"
    platform_account_id = os.getenv("PLATFORM_AWS_ACCOUNT_ID", "").strip()
    if platform_account_id:
        if not re.fullmatch(r"\d{12}", platform_account_id):
            raise ConfigurationError("PLATFORM_AWS_ACCOUNT_ID must be a 12-digit AWS account ID")
        params["param_PlatformAccountId"] = platform_account_id
    query = urlencode(params)
    region = os.getenv("AWS_REGION", "sa-east-1").strip()
    if not re.fullmatch(r"[a-z0-9-]+-\d", region):
        raise ConfigurationError("AWS_REGION must be an AWS region name")
    return f"https://console.aws.amazon.com/cloudformation/home?region={region}#/stacks/create/review?{query}"


def _analysis_out(row: dict[str, Any]) -> AnalysisOut:
    return AnalysisOut(
        id=UUID(row["id"]), project_id=UUID(row["project_id"]), schema_version=row["schema_version"],
        source_sha256=row["source_sha256"], result=_json_value(row["result"]), created_at=_as_utc(row["created_at"]),
    )


def _plan_out(row: dict[str, Any]) -> PlanOut:
    return PlanOut(
        id=UUID(row["id"]), project_id=UUID(row["project_id"]),
        analysis_id=UUID(row["analysis_id"]) if row["analysis_id"] else None,
        target=row["target"], operation_type=row.get("operation_type", "deploy"),
        rollback_from_deployment_id=(UUID(row["rollback_from_deployment_id"])
                                     if row.get("rollback_from_deployment_id") else None),
        rollback_to_deployment_id=(UUID(row["rollback_to_deployment_id"])
                                   if row.get("rollback_to_deployment_id") else None),
        module_id=row["module_id"], variables=_json_value(row["variables"]),
        summary=row["summary"], cost_estimate=_json_value(row["cost_estimate"]),
        terraform_plan_sha256=row["terraform_plan_sha256"],
        terraform_plan_summary=_json_value(row["terraform_plan_summary"]) if row.get("terraform_plan_summary") else None,
        fingerprint=row["fingerprint"],
        terraform_plan_ready=(
            row["target"] != "aws"
            or bool(row.get("terraform_plan_path") and Path(row["terraform_plan_path"]).is_file())
        ),
        status=row["status"], created_at=_as_utc(row["created_at"]),
        approved_at=_as_utc(row["approved_at"]) if row["approved_at"] else None,
    )


def _deployment_out(row: dict[str, Any]) -> DeploymentOut:
    return DeploymentOut(
        id=UUID(row["id"]), plan_id=UUID(row["plan_id"]), project_id=UUID(row["project_id"]),
        target=row["target"], operation_type=row.get("operation_type", "deploy"),
        rollback_from_deployment_id=(UUID(row["rollback_from_deployment_id"])
                                     if row.get("rollback_from_deployment_id") else None),
        rollback_to_deployment_id=(UUID(row["rollback_to_deployment_id"])
                                   if row.get("rollback_to_deployment_id") else None),
        status=row["status"], url=row["url"],
        created_at=_as_utc(row["created_at"]), updated_at=_as_utc(row["updated_at"]),
    )


def _event_out(row: dict[str, Any]) -> DeploymentEventOut:
    return DeploymentEventOut(
        id=row["id"], level=row["level"], event_type=row["event_type"],
        message=_redact(row["message"]),
        details=_redact_tree(_json_value(row["details"])) if row["details"] is not None else None,
        created_at=_as_utc(row["created_at"]),
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _encode_cursor(created_at: datetime, identifier: str) -> str:
    raw = f"{created_at.isoformat()}|{identifier}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        timestamp, identifier = raw.rsplit("|", maxsplit=1)
        return datetime.fromisoformat(timestamp), str(UUID(identifier))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "페이지 커서 형식이 올바르지 않습니다.") from exc
