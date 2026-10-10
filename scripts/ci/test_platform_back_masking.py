#!/usr/bin/env python3
"""격리된 실제 API/DB에서 비밀 저장 거부와 로그 마스킹을 검사한다."""

import hashlib
import json
import sys
import uuid

from test_platform_back_rollbacks import (
    WORKER_TOKEN, create_and_queue, finish, request, require, source_zip, wait_for_api,
)


def main() -> int:
    require(bool(WORKER_TOKEN), "WORKER_API_TOKEN is required")
    wait_for_api()
    project = request(
        f"/api/projects?name=masking-test-{uuid.uuid4().hex[:8]}&filename=test.zip",
        method="POST", data=source_zip(), expected=201,
        headers={"Content-Type": "application/zip"},
    )
    analysis = {
        "schema_version": "ci-v1", "source_sha256": project["source_sha256"],
        "result": {"description": "image: python:3.12"},
    }
    safe_analysis = request(f"/api/projects/{project['id']}/analyses", method="POST", payload=analysis, expected=201)
    plan = {
        "project_id": project["id"], "target": "aws", "module_id": "ecs-web-app",
        "variables": {"image": "python:3.12"}, "summary": "image: python:3.12",
        "terraform_plan_sha256": hashlib.sha256(b"synthetic-plan").hexdigest(),
        "cost_estimate": {"amount": "1.00", "currency": "USD", "period": "month", "pricing_as_of": "test"},
    }
    request("/api/plans", method="POST", payload=plan, expected=201)
    healthy = create_and_queue(project["id"], "masking-healthy")
    finish(healthy["id"], "healthy", url="https://example.test/masking")
    failed = create_and_queue(project["id"], "masking-failed")
    finish(failed["id"], "failed")
    rollback = request(
        f"/api/deployments/{failed['id']}/rollback", method="POST", expected=201,
        payload={"expected_target_deployment_id": healthy["id"]},
    )
    artifact = b"synthetic-fresh-rollback"
    digest = hashlib.sha256(artifact).hexdigest()
    worker_headers = {"X-Worker-Token": WORKER_TOKEN}
    request(
        f"/api/worker/rollback-plans/{rollback['id']}/terraform-plan", method="POST", data=artifact,
        headers={**worker_headers, "Content-Type": "application/octet-stream", "X-Terraform-Plan-SHA256": digest},
        expected=200,
    )
    sentinel = "SYNTHETIC_CI_SECRET"
    secret_texts = (f"environment: API_KEY={sentinel}", f'message="PASSWORD={sentinel}"',
                    f'config={{"password":"{sentinel}"}}', f"description: apiKey={sentinel}",
                    "config=" + json.dumps(json.dumps({"password": sentinel})),
                    f'PASSWORD={{"value":"{sentinel}"}}', f'PASSWORD=["{sentinel}"]')
    for text in secret_texts:
        response = request(
            f"/api/projects/{project['id']}/analyses", method="POST", expected=422,
            payload={**analysis, "result": {"description": text}},
        )
        require(sentinel not in json.dumps(response), "analysis rejection echoed secret")
        for field, value in (("variables", {"description": text}), ("summary", text)):
            request("/api/plans", method="POST", payload={**plan, field: value}, expected=422)
        request(
            "/api/connections", method="POST", expected=422,
            payload={"provider": "aws", "name": "ci", "fields": {"description": text}},
        )
        request(
            f"/api/worker/rollback-plans/{rollback['id']}/summary", method="POST", expected=422,
            headers=worker_headers, payload={"terraform_plan_sha256": digest, "summary": {"description": text}},
        )
        request(
            f"/api/worker/deployments/{failed['id']}/events", method="POST", expected=200,
            headers=worker_headers,
            payload={"status": "failed", "event_type": "masking_test", "message": text,
                     "details": {"nested": [text], "apiKey": sentinel, "image": "python:3.12"}},
        )
    latest = request(f"/api/projects/{project['id']}/analyses/latest", expected=200)
    require(latest["id"] == safe_analysis["id"], "rejected analysis was persisted")
    logs = request(f"/api/deployments/{failed['id']}", expected=200)
    require(sentinel not in json.dumps(logs), "deployment logs exposed synthetic secret")
    events = [event for event in logs["events"] if event["event_type"] == "masking_test"]
    require(len(events) == len(secret_texts), "masking events missing")
    require(all(event["details"]["image"] == "python:3.12" for event in events), "Docker tag was changed")
    require(all("[REDACTED]" in event["message"] for event in events), "secret message was not masked")
    print("PASS: 중첩 비밀값은 분석·계획·연결·rollback diff 저장에서 거부; 이벤트 마스킹과 Docker 태그 보존")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
