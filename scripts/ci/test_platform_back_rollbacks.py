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
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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


def create_approved_plan(project_id: str, suffix: str) -> dict:
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
    return plan


def queue_plan(plan: dict, *, expected: int = 202) -> dict:
    return request(
        "/api/deployments", method="POST", expected=expected,
        payload={"plan_id": plan["id"], "expected_fingerprint": plan["fingerprint"]},
    )


def create_and_queue(project_id: str, suffix: str) -> dict:
    return queue_plan(create_approved_plan(project_id, suffix))


def rollback_draft(failed: dict, healthy: dict) -> dict:
    plan = request(
        f"/api/deployments/{failed['id']}/rollback", method="POST", expected=201,
        payload={"expected_target_deployment_id": healthy["id"]},
    )
    content = b"fresh rollback regression plan"
    digest = hashlib.sha256(content).hexdigest()
    request(
        f"/api/worker/rollback-plans/{plan['id']}/terraform-plan", method="POST", data=content, expected=200,
        headers={"Content-Type": "application/octet-stream", "X-Worker-Token": WORKER_TOKEN,
                 "X-Terraform-Plan-SHA256": digest},
    )
    return request(
        f"/api/worker/rollback-plans/{plan['id']}/summary", method="POST", expected=200,
        headers={"X-Worker-Token": WORKER_TOKEN},
        payload={"terraform_plan_sha256": digest, "summary": {"resource_changes": 1}},
    )


def approve(plan: dict, *, expected: int = 200) -> dict:
    return request(
        f"/api/plans/{plan['id']}/approve", method="POST", expected=expected,
        payload={"expected_fingerprint": plan["fingerprint"]},
    )


def stale_fixture() -> tuple[dict, dict]:
    project = request(
        f"/api/projects?name=stale-{uuid.uuid4().hex[:8]}&filename=test.zip",
        method="POST", data=source_zip(), expected=201,
        headers={"Content-Type": "application/zip"},
    )
    healthy = create_and_queue(project["id"], "healthy")
    finish(healthy["id"], "healthy", url="https://example.test/healthy")
    failed = create_and_queue(project["id"], "failed")
    finish(failed["id"], "failed")
    # Prepare this before the rollback draft: creating a plan supersedes drafts.
    newer = create_approved_plan(project["id"], "newer")
    return rollback_draft(failed, healthy), newer


def check_stale_rollbacks() -> None:
    draft, newer = stale_fixture()
    replacement = queue_plan(newer)
    finish(replacement["id"], "healthy", url="https://example.test/newer")
    approve(draft, expected=409)

    draft, newer = stale_fixture()
    approve(draft)
    replacement = queue_plan(newer)
    queue_plan(draft, expected=409)
    retained = request(f"/api/plans/{draft['id']}", expected=200)
    require(retained["status"] == "approved", "stale queue rejection consumed the approval")
    finish(replacement["id"], "failed")

    for direct_start in (False, True):
        draft, newer = stale_fixture()
        approve(draft)
        stale = queue_plan(draft)
        replacement = queue_plan(newer)
        if direct_start:
            request(
                f"/api/worker/deployments/{stale['id']}/events", method="POST", expected=409,
                headers={"X-Worker-Token": WORKER_TOKEN},
                payload={"status": "provisioning", "event_type": "direct_start", "message": "start"},
            )
        claim = request("/api/worker/deployments/claim", method="POST", expected=200,
                        headers={"X-Worker-Token": WORKER_TOKEN})
        require(claim["job"]["deployment_id"] == replacement["id"], "stale rollback blocked the next claim")
        rejected = request(f"/api/deployments/{stale['id']}", expected=200)
        require(rejected["status"] == "failed", "stale rollback was not quarantined")
        finish(replacement["id"], "healthy", url="https://example.test/replacement")

    # A deployment in another project must not invalidate a valid rollback.
    draft, _ = stale_fixture()
    _, other = stale_fixture()
    replacement = queue_plan(other)
    finish(replacement["id"], "failed")
    approve(draft)
    valid = queue_plan(draft)
    claim = request("/api/worker/deployments/claim", method="POST", expected=200,
                    headers={"X-Worker-Token": WORKER_TOKEN})
    require(claim["job"]["deployment_id"] == valid["id"], "other project invalidated rollback")
    finish(valid["id"], "healthy", url="https://example.test/control")

    # Concurrent queue requests must serialize. If rollback queues first, claim
    # must quarantine it; if the normal deployment wins, rollback returns 409.
    draft, newer = stale_fixture()
    approve(draft)
    queue_barrier = Barrier(2)
    def race_rollback() -> dict | None:
        queue_barrier.wait(timeout=10)
        try:
            return queue_plan(draft)
        except AssertionError as error:
            require("got 409:" in str(error), f"unexpected queue race failure: {error}")
            return None
    def race_normal() -> dict:
        queue_barrier.wait(timeout=10)
        return queue_plan(newer)
    with ThreadPoolExecutor(max_workers=2) as executor:
        rollback_future = executor.submit(race_rollback)
        normal_future = executor.submit(race_normal)
        raced = rollback_future.result()
        replacement = normal_future.result()
    claim = request("/api/worker/deployments/claim", method="POST", expected=200,
                    headers={"X-Worker-Token": WORKER_TOKEN})
    require(claim["job"]["deployment_id"] == replacement["id"], "queue race executed stale rollback")
    if raced:
        rejected = request(f"/api/deployments/{raced['id']}", expected=200)
        require(rejected["status"] == "failed", "raced rollback was not quarantined")
    finish(replacement["id"], "healthy", url="https://example.test/race")
    print("PASS: stale rollback approval, queue, claim, direct start, project isolation, and concurrent queue checks")


def check_active_rollbacks() -> None:
    for final_state in ("healthy", "failed"):
        draft, newer = stale_fixture()
        approve(draft)
        rollback = queue_plan(draft)
        claim = request("/api/worker/deployments/claim", method="POST", expected=200,
                        headers={"X-Worker-Token": WORKER_TOKEN})
        require(claim["job"]["deployment_id"] == rollback["id"], "valid rollback was not claimed")
        queue_plan(newer, expected=409)
        worker_event(rollback["id"], "deploying")
        queue_plan(newer, expected=409)
        worker_event(rollback["id"], final_state,
                     url="https://example.test/recovered" if final_state == "healthy" else None)
        replacement = queue_plan(newer)
        finish(replacement["id"], "healthy", url="https://example.test/after-rollback")

    # An earlier ordinary deployment can still be pending when a later one
    # fails. Its age must not permit a rollback to overlap its execution.
    for older_state in ("queued", "provisioning", "deploying", "rolling_back"):
        project = request(
            f"/api/projects?name=active-{uuid.uuid4().hex[:8]}&filename=test.zip",
            method="POST", data=source_zip(), expected=201,
            headers={"Content-Type": "application/zip"},
        )
        older = create_and_queue(project["id"], "older")
        if older_state in {"provisioning", "deploying"}:
            worker_event(older["id"], "provisioning")
        if older_state == "deploying":
            worker_event(older["id"], "deploying")
        if older_state == "rolling_back":
            worker_event(older["id"], "failed")
        healthy = create_and_queue(project["id"], "healthy")
        finish(healthy["id"], "healthy", url="https://example.test/healthy")
        failed = create_and_queue(project["id"], "failed")
        finish(failed["id"], "failed")
        draft = rollback_draft(failed, healthy)
        if older_state == "rolling_back":
            # Become active after approval to also exercise queue-time checks.
            approve(draft)
            worker_event(older["id"], "rolling_back")
            queue_plan(draft, expected=409)
        else:
            approve(draft, expected=409)
        worker_event(older["id"], "failed")
        if older_state == "rolling_back":
            rollback = queue_plan(draft)
            claim = request("/api/worker/deployments/claim", method="POST", expected=200,
                            headers={"X-Worker-Token": WORKER_TOKEN})
            require(claim["job"]["deployment_id"] == rollback["id"], "rollback did not resume after older failure")
            for next_state in ("deploying", "healthy"):
                worker_event(older["id"], "rolling_back")
                request(
                    f"/api/worker/deployments/{rollback['id']}/events", method="POST", expected=409,
                    headers={"X-Worker-Token": WORKER_TOKEN},
                    payload={"status": next_state, "event_type": "forward", "message": "advance",
                             "url": "https://example.test/blocked"},
                )
                worker_event(older["id"], "failed")
                if next_state == "deploying":
                    worker_event(rollback["id"], "deploying")
            # Failure reports remain accepted even while the context is stale.
            worker_event(older["id"], "rolling_back")
            worker_event(rollback["id"], "failed")
            worker_event(older["id"], "failed")
    print("PASS: active rollback blocks new queues until terminal; older active deployments block rollback")


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

    rollback_plan = request(
        f"/api/deployments/{later_failed['id']}/rollback", method="POST", expected=201,
        payload={"expected_target_deployment_id": candidate["target_deployment_id"]},
    )
    require(rollback_plan["operation_type"] == "rollback" and rollback_plan["status"] == "awaiting_approval", f"bad rollback plan: {rollback_plan}")
    require(rollback_plan["rollback_from_deployment_id"] == later_failed["id"], f"missing rollback source: {rollback_plan}")
    require(rollback_plan["rollback_to_deployment_id"] == healthy["id"], f"missing rollback target: {rollback_plan}")
    rollback_bytes = b"fresh rollback plan"
    rollback_digest = hashlib.sha256(rollback_bytes).hexdigest()
    request(
        f"/api/worker/rollback-plans/{rollback_plan['id']}/terraform-plan", method="POST", data=rollback_bytes, expected=200,
        headers={"Content-Type": "application/octet-stream", "X-Worker-Token": WORKER_TOKEN,
                 "X-Terraform-Plan-SHA256": rollback_digest},
    )
    rollback_plan = request(
        f"/api/worker/rollback-plans/{rollback_plan['id']}/summary", method="POST", expected=200,
        headers={"X-Worker-Token": WORKER_TOKEN},
        payload={"terraform_plan_sha256": rollback_digest, "summary": {"resource_changes": 1}},
    )
    rollback_plan = request(
        f"/api/plans/{rollback_plan['id']}/approve", method="POST", expected=200,
        payload={"expected_fingerprint": rollback_plan["fingerprint"]},
    )
    rollback = request(
        "/api/deployments", method="POST", expected=202,
        payload={"plan_id": rollback_plan["id"], "expected_fingerprint": rollback_plan["fingerprint"]},
    )
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
    print("PASS: first failure excludes rollback; later failure requires a fresh rollback plan, diff approval, and queues a linked rollback")
    check_stale_rollbacks()
    check_active_rollbacks()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
