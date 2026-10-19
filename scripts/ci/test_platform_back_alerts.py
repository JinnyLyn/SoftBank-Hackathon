#!/usr/bin/env python3
"""Run the platform backend's capacity-alert contract against a live test stack.

새 배포가 healthy가 되거나 최신 배포가 실패하면 이전 배포의 켜진 알림이 풀리고(worker는 프로젝트마다 가장 최근의
healthy 배포만 확인하므로 그러지 않으면 영원히 켜져 있다), 그 해제가 SSE로도 전달되는지 확인한다. AWS·LLM은 실행하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler

import test_platform_back_rollbacks as api

TOKEN = {"X-Worker-Token": api.WORKER_TOKEN}
ALERT = {
    "kind": "capacity", "active": True, "message": "서버 사용률이 높습니다.",
    "metrics": {"cpu_percent": 91.2, "running_tasks": 2, "max_tasks": 2, "window_minutes": 5},
}


def new_project() -> dict:
    return api.request(
        f"/api/projects?name=alerts-{uuid.uuid4().hex[:8]}&filename=test.zip",
        method="POST", data=api.source_zip(), expected=201, headers={"Content-Type": "application/zip"},
    )


def deploy(project_id: str, suffix: str, deploy_id: str, final_state: str) -> dict:
    content = f"alert-plan-{suffix}".encode("utf-8")
    plan = api.request(
        "/api/plans", method="POST", expected=201,
        payload={
            "project_id": project_id, "target": "aws", "module_id": "ecs-web-app",
            "variables": {"deploy_id": deploy_id, "app": {"min_tasks": 1, "max_tasks": 2}},
            "summary": f"alert test {suffix}",
            "cost_estimate": {"amount": "1.00", "currency": "USD", "period": "month", "pricing_as_of": "test"},
            "terraform_plan_sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    api.request(
        f"/api/worker/plans/{plan['id']}/terraform-plan", method="POST", data=content, expected=200,
        headers={"Content-Type": "application/octet-stream", **TOKEN},
    )
    api.request(f"/api/plans/{plan['id']}/approve", method="POST", expected=200,
                payload={"expected_fingerprint": plan["fingerprint"]})
    deployment = api.queue_plan(plan)
    api.finish(deployment["id"], final_state, url=f"https://{suffix}.example.test" if final_state == "healthy" else None)
    return deployment


def raise_alert(deployment_id: str) -> None:
    api.request(f"/api/worker/deployments/{deployment_id}/alerts", method="POST", expected=200,
                headers=TOKEN, payload=ALERT)


def active_ids() -> set[str]:
    return {a["deployment_id"] for a in api.request("/api/alerts", expected=200)}


def read_stream(until, last_event_id: str | None = None, seconds: float = 8.0) -> str:
    """이벤트 스트림을 until(text)이 참이 되거나 seconds가 지날 때까지 읽는다."""
    headers = {"Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    response = build_opener(ProxyHandler({})).open(Request(api.BASE_URL + "/api/alerts/stream", headers=headers), timeout=10)
    api.require(response.headers.get("Content-Type", "").startswith("text/event-stream"), "/api/alerts/stream: not an event stream")
    deadline, text = time.time() + seconds, ""
    with response:
        while time.time() < deadline and not until(text):
            line = response.readline()
            if not line:
                break
            text += line.decode("utf-8", "replace")
    return text


def stream_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def main() -> int:
    api.require(bool(api.WORKER_TOKEN), "WORKER_API_TOKEN is required")
    api.wait_for_api()
    api.request("/api/worker/deployments/monitored", expected=401)

    # 1. 새 배포가 healthy가 되면 이전 배포의 켜진 알림이 풀리고, 자기 알림과 다른 프로젝트의 알림은 그대로다
    project, other = new_project(), new_project()
    first = deploy(project["id"], "first", "aaaa1111", "healthy")
    bystander = deploy(other["id"], "other", "cccc3333", "healthy")
    raise_alert(first["id"])
    raise_alert(bystander["id"])
    api.require({first["id"], bystander["id"]} <= active_ids(), "alerts were not listed as active")
    seen = read_stream(lambda t: {first["id"], bystander["id"]} <= {e["deployment_id"] for e in stream_events(t)})
    api.require(first["id"] in seen and bystander["id"] in seen, f"active alerts were not sent on first connect: {seen!r}")
    last_event_id = [line[4:].strip() for line in seen.splitlines() if line.startswith("id: ")][-1]
    second = deploy(project["id"], "second", "bbbb2222", "healthy")
    raise_alert(second["id"])
    ids = active_ids()
    api.require(first["id"] not in ids, "superseded deployment's alert stayed active after a new healthy deployment")
    api.require(second["id"] in ids, "the new deployment's own alert must stay active")
    api.require(bystander["id"] in ids, "another project's alert must not be closed")
    print("PASS: a new healthy deployment closes the previous deployment's alert only")

    # 늦게 도착한 알림이 옛 배포에 다시 켜지면 worker가 다시 보지 않아 영원히 켜져 있게 된다 → 최신 healthy AWS 배포만 켤 수 있다
    api.request(f"/api/worker/deployments/{first['id']}/alerts", method="POST", expected=409, headers=TOKEN, payload=ALERT)
    api.require(first["id"] not in active_ids(), "a rejected late alert must not be stored as active")
    api.request(f"/api/worker/deployments/{first['id']}/alerts", method="POST", expected=200, headers=TOKEN,
                payload={**ALERT, "active": False, "message": "해제"})
    api.request(f"/api/worker/deployments/{uuid.uuid4()}/alerts", method="POST", expected=404, headers=TOKEN, payload=ALERT)
    print("PASS: only the project's latest healthy AWS deployment can raise an alert; resolving stays allowed")

    # 2. 해제는 SSE로도 전달된다(Last-Event-ID로 이어 받은 연결은 변경분만 받는다)
    resumed = read_stream(
        lambda t: any(e["deployment_id"] == first["id"] and not e["active"] for e in stream_events(t)), last_event_id)
    closed = [e for e in stream_events(resumed) if e["deployment_id"] == first["id"]]
    api.require(closed and closed[-1]["active"] is False, f"close was not delivered over SSE: {resumed!r}")
    print("PASS: the close reaches connected clients over SSE")

    # 3. 최신 배포가 실패하면 그 프로젝트의 켜진 알림이 모두 풀린다(worker는 더 이상 확인하지 않는다)
    deploy(project["id"], "third", "dddd4444", "failed")
    ids = active_ids()
    api.require(second["id"] not in ids, "alert stayed active after the latest deployment failed")
    api.require(bystander["id"] in ids, "another project's alert must not be closed by a failure")
    print("PASS: a failed latest deployment closes the project's alerts")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
