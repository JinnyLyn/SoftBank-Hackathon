#!/usr/bin/env python3
"""Run the platform backend's rollback policy contract against a live test stack."""

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
            response = request("/health", expected=200)
            if response == {"status": "ok"}:
                return
        except URLError:
            time.sleep(0.25)
    raise AssertionError("backend startup timeout")


def create_and_queue(project_id: str, suffix: str) -> dict:
    plan_bytes = f"verified-plan-{suffix}".encode("utf-8")
    digest = hashlib.sha256(plan_bytes).hexdigest()
    plan = request(
        "/api/plans", method="POST", expected=201,
        payload={
            "project_id": project_id,
            "target": "aws",
            "module_id": "ecs-web-app",
            "variables": {"image_tag": suffix},
            "summary": f"rollback test {suffix}",
            "cost_estimate": {"amount": "1.00", "currency": "USD", "period": "month", "pricing_as_of": "test"},
            "terraform_plan_sha256": digest,
        },
    )
    request(
        f"/api/worker/plans/{plan['id']}/terraform-plan", method="POST", data=plan_bytes, expected=200,
        headers={"Content-Type": "application/octet-stream", "X-Worker-Token": WORKER_TOKEN},
    )
    request(
        f"/api/plans/{plan['id']}/approve", method="POST", expected=200,
        payload={"expected_fingerprint": plan["fingerprint"]},
    )
    return request(
        "/api/deployments", method="POST", expected=202,
        payload={"plan_id": plan["id"], "expected_fingerprint": plan["fingerprint"]},
    )


def worker_event(deployment_id: str, state: str, *, url: str | None = None) -> dict:
    payload = {
        "status": state,
        "event_type": f"test_{state}",
        "message": f"test transition to {state}",
    }
    if url:
        payload["url"] = url
    return request(
        f"/api/worker/deployments/{deployment_id}/events", method="POST", expected=200,
        payload=payload, headers={"X-Worker-Token": WORKER_TOKEN},
    )


def finish(deployment_id: str, final_state: str, *, url: str | None = None) -> None:
    worker_event(deployment_id, "provisioning")
    worker_event(deployment_id, "deploying")
    worker_event(deployment_id, final_state, url=url)


def main() -> int:
    require(bool(WORKER_TOKEN), "WORKER_API_TOKEN is required")
    wait_for_api()
    project = request(
        f"/api/projects?name=rollback-test-{uuid.uuid4().hex[:8]}&filename=test.zip",
        method="POST", data=source_zip(), expected=201,
        headers={"Content-Type": "application/zip"},
    )

    first_failed = create_and_queue(project["id"], "first-failed")
    finish(first_failed["id"], "failed")
    first_candidate = request(f"/api/deployments/{first_failed['id']}/rollback-candidate", expected=200)
    require(first_candidate == {
        "rollback_available": False,
        "reason": "first_deployment",
        "target_deployment_id": None,
        "target_plan_id": None,
    }, f"first deployment rollback policy mismatch: {first_candidate}")
    request(
        f"/api/deployments/{first_failed['id']}/rollback", method="POST", expected=409,
        payload={"expected_target_deployment_id": str(uuid.uuid4())},
    )

    healthy = create_and_queue(project["id"], "healthy")
    finish(healthy["id"], "healthy", url="https://example.test/healthy")
    later_failed = create_and_queue(project["id"], "later-failed")
    finish(later_failed["id"], "failed")
    candidate = request(f"/api/deployments/{later_failed['id']}/rollback-candidate", expected=200)
    require(candidate["rollback_available"] and candidate["reason"] == "available", f"missing rollback candidate: {candidate}")
    require(candidate["target_deployment_id"] == healthy["id"], f"wrong rollback target: {candidate}")

    rollback = request(
        f"/api/deployments/{later_failed['id']}/rollback", method="POST", expected=202,
        payload={"expected_target_deployment_id": candidate["target_deployment_id"]},
    )
    require(rollback["operation_type"] == "rollback" and rollback["status"] == "queued", f"bad rollback record: {rollback}")
    require(rollback["rollback_from_deployment_id"] == later_failed["id"], f"missing rollback source: {rollback}")
    require(rollback["rollback_to_deployment_id"] == healthy["id"], f"missing rollback target: {rollback}")
    claim = request(
        "/api/worker/deployments/claim", method="POST", expected=200,
        headers={"X-Worker-Token": WORKER_TOKEN},
    )
    require(claim["job"]["deployment_id"] == rollback["id"], f"wrong claimed rollback: {claim}")
    require(claim["job"]["operation_type"] == "rollback", f"missing rollback operation: {claim}")
    require(claim["job"]["rollback_from_deployment_id"] == later_failed["id"], f"missing source in claim: {claim}")
    require(claim["job"]["rollback_to_deployment_id"] == healthy["id"], f"missing target in claim: {claim}")
    finish(rollback["id"], "healthy", url="https://example.test/rolled-back")
    original = request(f"/api/deployments/{later_failed['id']}", expected=200)
    require(original["status"] == "failed", f"failed deployment was mutated: {original}")
    print("PASS: first failure excludes rollback; later failure creates only a user-approved rollback record")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
