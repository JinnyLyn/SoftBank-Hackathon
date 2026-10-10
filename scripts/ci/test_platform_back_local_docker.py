#!/usr/bin/env python3
"""Run the local_docker (presenter-PC Docker) plan, approval, and claim contract against a live test stack.

Docker와 AWS는 실행하지 않는다. 백엔드가 저장·승인·대기열만 다루는 계약을 확인한다(실행은 infra/worker).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


BASE_URL = os.environ.get("PLATFORM_BACK_URL", "http://127.0.0.1:8000").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_API_TOKEN", "")
WORKER = {"X-Worker-Token": WORKER_TOKEN}
COMPOSE = 'services:\n  app:\n    image: "pc-local/ab12cd34:s0123456789ab"\n    pull_policy: never\n'


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request(path: str, *, method: str = "GET", payload: object | None = None,
            data: bytes | None = None, headers: dict[str, str] | None = None,
            expected: int) -> dict:
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(BASE_URL + path, data=data, method=method, headers=request_headers)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        body = json.loads(response.read())
        require(response.status == expected, f"{method} {path}: expected {expected}, got {response.status}: {body}")
        return body


def source_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.py", "print('safe test fixture')\n")
    return buffer.getvalue()


def wait_for_api() -> None:
    for _ in range(60):
        try:
            if request("/health", expected=200) == {"status": "ok"}:
                return
        except URLError:
            time.sleep(0.25)
    raise AssertionError("backend startup timeout")


def new_project() -> dict:
    return request(
        f"/api/projects?name=local-docker-{uuid.uuid4().hex[:8]}&filename=test.zip",
        method="POST", data=source_zip(), expected=201, headers={"Content-Type": "application/zip"},
    )


def local_body(project_id: str, compose: str = COMPOSE, **over: object) -> dict:
    body = {
        "project_id": project_id, "target": "local_docker", "module_id": "local-docker-compose",
        "variables": {"deploy_id": "ab12cd34", "tier": "balanced", "connection_name": "발표자 PC (로컬 Docker)",
                      "compose_yaml": compose},
        "summary": "local docker plan",
        "cost_estimate": {"amount": "0.0000", "currency": "USD", "period": "month", "pricing_as_of": "해당 없음(로컬 Docker)"},
        "terraform_plan_sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
    }
    body.update(over)
    return body


def aws_body(project_id: str) -> dict:
    return {
        "project_id": project_id, "target": "aws", "module_id": "ecs-web-app", "variables": {"tier": "lean"},
        "summary": "aws plan",
        "cost_estimate": {"amount": "1.00", "currency": "USD", "period": "month", "pricing_as_of": "test"},
        "terraform_plan_sha256": hashlib.sha256(b"aws-plan").hexdigest(),
    }


def plan_statuses(project_id: str) -> dict[str, str]:
    plans = request(f"/api/projects/{project_id}/plans", expected=200)
    return {f"{p['target']}:{p['id']}": p["status"] for p in plans}


def status_of(plan: dict) -> str:
    return request(f"/api/plans/{plan['id']}", expected=200)["status"]


def worker_event(deployment_id: str, state: str, *, url: str | None = None, details: object | None = None) -> dict:
    payload: dict = {"status": state, "event_type": f"test_{state}", "message": f"test transition to {state}"}
    if url:
        payload["url"] = url
    if details is not None:
        payload["details"] = details
    return request(f"/api/worker/deployments/{deployment_id}/events", method="POST", expected=200,
                   payload=payload, headers=WORKER)


def claim(targets: str | None, *, expected: int = 200) -> dict:
    suffix = "" if targets is None else f"?targets={targets}"
    return request(f"/api/worker/deployments/claim{suffix}", method="POST", expected=expected, headers=WORKER)


def approve_and_queue(plan: dict) -> dict:
    approved = request(f"/api/plans/{plan['id']}/approve", method="POST", expected=200,
                       payload={"expected_fingerprint": plan["fingerprint"]})
    return request("/api/deployments", method="POST", expected=202,
                   payload={"plan_id": approved["id"], "expected_fingerprint": approved["fingerprint"]})


def check_plan_contract() -> None:
    project = new_project()
    pid = project["id"]
    # compose 파일의 SHA-256(terraform_plan_sha256)이 없으면 거부한다. 승인 대상을 가리킬 수 없기 때문이다.
    request("/api/plans", method="POST", expected=422, payload=local_body(pid, terraform_plan_sha256=None))
    request("/api/plans", method="POST", expected=422, payload=local_body(pid, terraform_plan_sha256="nothex"))
    # 비밀로 보이는 값은 계획에 저장하지 않는다. compose 본문에 비밀번호 환경 변수 이름이 들어가면 거부되므로 비밀은 env_file로만 넘긴다.
    request("/api/plans", method="POST", expected=422, payload=local_body(pid, COMPOSE + "    environment:\n      MYSQL_PASSWORD: hunter2\n"))
    request("/api/plans", method="POST", expected=422,
            payload=local_body(pid, variables={"database_password": "x", "compose_yaml": COMPOSE}))
    # env_file 참조와 DB 이름 같은 비밀이 아닌 설정은 통과한다
    safe = COMPOSE + '    env_file:\n      - "./secrets/app.env"\n    environment:\n      MYSQL_DATABASE: "app_ab12cd34"\n'
    local = request("/api/plans", method="POST", expected=201, payload=local_body(pid, safe))
    require(local["target"] == "local_docker" and local["status"] == "awaiting_approval", f"bad local plan: {local}")
    require(local["terraform_plan_ready"] is True, "local plan has no plan artifact to wait for")
    require(local["terraform_plan_sha256"] == hashlib.sha256(safe.encode("utf-8")).hexdigest(), "digest was not stored")
    # 같은 프로젝트가 AWS 계획을 함께 가질 수 있고, 서로 대체하지 않는다
    aws = request("/api/plans", method="POST", expected=201, payload=aws_body(pid))
    require(status_of(local) == "awaiting_approval", "AWS plan superseded the local plan")
    # 같은 대상의 새 계획은 이전 계획만 대체한다
    newer = request("/api/plans", method="POST", expected=201, payload=local_body(pid))
    require(status_of(local) == "superseded", "same-target plan was not superseded")
    require(status_of(aws) == "awaiting_approval", "local plan superseded the AWS plan")
    require(status_of(newer) == "awaiting_approval", "new local plan is not awaiting approval")
    # plan 파일 업로드는 AWS 계획만 받는다
    request(f"/api/worker/plans/{newer['id']}/terraform-plan", method="POST", data=b"x", expected=409,
            headers={"Content-Type": "application/octet-stream", **WORKER})
    # 알 수 없는 대상은 거부한다
    request("/api/plans", method="POST", expected=422, payload=local_body(pid, target="azure"))
    print("PASS: local_docker plans require a compose digest, reject secrets, and coexist with AWS plans")


def check_approval_and_claim() -> None:
    project = new_project()
    pid = project["id"]
    local = request("/api/plans", method="POST", expected=201, payload=local_body(pid))
    aws = request("/api/plans", method="POST", expected=201, payload=aws_body(pid))
    request(f"/api/plans/{local['id']}/approve", method="POST", expected=409, payload={"expected_fingerprint": "0" * 64})
    deployment = approve_and_queue(local)
    require(deployment["target"] == "local_docker" and deployment["status"] == "queued", f"bad deployment: {deployment}")
    # 하나를 승인하면 같은 프로젝트의 다른 대상 승인 대기 계획은 대체된다(쓰지 않을 AWS 계획이 용량을 예약하지 않게)
    require(status_of(aws) == "superseded", "unchosen AWS plan stayed awaiting approval")

    # targets를 모르는 옛 worker(기본 aws)는 로컬 작업을 가져가지 않는다. 알 수 없는 대상은 거부한다
    default = claim(None)
    require(default["job"] is None or default["job"]["deployment_id"] != deployment["id"], "default worker claimed a local_docker job")
    claim("azure", expected=422)
    claim("", expected=422)
    claim("aws,,local_docker,bogus", expected=422)

    job = claim("local_docker")["job"]
    require(job is not None and job["deployment_id"] == deployment["id"], f"local worker did not get the job: {job}")
    require(job["target"] == "local_docker" and job["status"] == "provisioning", f"bad claimed job: {job}")
    require(job["operation_type"] == "deploy", f"bad operation: {job}")
    require(job["terraform_plan_sha256"] == local["terraform_plan_sha256"], "claim lost the approved digest")
    require(job["variables"]["compose_yaml"] == COMPOSE, "claim lost the approved compose text")
    require("terraform_plan_download_path" not in job, "local job advertises a Terraform plan download")
    require(job["source_download_path"] == f"/api/worker/projects/{pid}/source", "source download path missing")
    require(claim("local_docker")["job"] is None, "job was claimed twice")

    worker_event(deployment["id"], "deploying", details={"image": "pc-local/ab12cd34:s0123456789ab"})
    request(f"/api/worker/deployments/{deployment['id']}/events", method="POST", expected=422, headers=WORKER,
            payload={"status": "healthy", "event_type": "healthy", "message": "no url"})
    worker_event(deployment["id"], "healthy", url="http://localhost:18123")

    status = request(f"/api/projects/{pid}/status", expected=200)
    require(status["state"] == "success" and status["url"] == "http://localhost:18123", f"bad status: {status}")
    history = [row for row in request(f"/api/deployments?project_id={pid}", expected=200) if row["id"] == deployment["id"]]
    require(len(history) == 1, "deployment missing from history")
    row = history[0]
    require(row["provider"] == "onprem" and row["target"] == "발표자 PC (로컬 Docker)", f"bad history row: {row}")
    require(row["monthlyUsd"] == 0 and row["status"] == "success" and row["tier"] == "balanced", f"bad history row: {row}")
    print("PASS: local_docker approval supersedes the other target; only a local worker claims it; healthy needs a URL")


def check_no_local_rollback() -> None:
    project = new_project()
    pid = project["id"]
    first = approve_and_queue(request("/api/plans", method="POST", expected=201, payload=local_body(pid)))
    for state in ("provisioning", "deploying"):
        worker_event(first["id"], state)
    worker_event(first["id"], "healthy", url="http://localhost:18123")
    second = approve_and_queue(request("/api/plans", method="POST", expected=201, payload=local_body(pid)))
    for state in ("provisioning", "deploying", "failed"):
        worker_event(second["id"], state)
    candidate = request(f"/api/deployments/{second['id']}/rollback-candidate", expected=200)
    require(candidate["rollback_available"] is False and candidate["reason"] == "unsupported_target", f"local rollback offered: {candidate}")
    request(f"/api/deployments/{second['id']}/rollback", method="POST", expected=409,
            payload={"expected_target_deployment_id": first["id"]})
    print("PASS: a failed local_docker deployment offers no rollback")


def check_aws_flow_unchanged() -> None:
    """AWS 배포는 예전처럼 승인에 저장된 Terraform plan이 필요하고 기본 claim으로 가져간다."""
    project = new_project()
    pid = project["id"]
    plan = request("/api/plans", method="POST", expected=201, payload=aws_body(pid))
    require(plan["terraform_plan_ready"] is False, "AWS plan must wait for its plan file")
    request(f"/api/plans/{plan['id']}/approve", method="POST", expected=409, payload={"expected_fingerprint": plan["fingerprint"]})
    request(f"/api/worker/plans/{plan['id']}/terraform-plan", method="POST", data=b"aws-plan", expected=200,
            headers={"Content-Type": "application/octet-stream", **WORKER})
    deployment = approve_and_queue(plan)
    job = claim(None)["job"]
    require(job is not None and job["deployment_id"] == deployment["id"] and job["target"] == "aws", f"AWS job not claimed: {job}")
    require(job["terraform_plan_download_path"] == f"/api/worker/plans/{plan['id']}/terraform-plan", "AWS plan download path missing")
    both = claim("aws,local_docker")
    require(both["job"] is None, "AWS job claimed twice")
    for state in ("deploying", "failed"):
        worker_event(deployment["id"], state)
    print("PASS: AWS plans still need their stored Terraform plan and are claimed by default workers")


def main() -> int:
    require(bool(WORKER_TOKEN), "WORKER_API_TOKEN is required")
    wait_for_api()
    check_plan_contract()
    check_approval_and_claim()
    check_no_local_rollback()
    check_aws_flow_unchanged()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
