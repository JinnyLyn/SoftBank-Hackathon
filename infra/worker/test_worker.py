"""worker(planner·executor)와 cost 시험. 표준 라이브러리만 쓴다.

실행: python infra/worker/test_worker.py
실제 AWS·Terraform·Docker는 쓰지 않는다. 백엔드는 시험용 HTTP 서버로, deploy.sh는 같은 인터페이스의 가짜 bash 스크립트로 대신한다.
이 시험이 통과해도 실제 백엔드(PR #10)·실제 AWS와의 연동이 확인된 것은 아니다.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cost  # noqa: E402
import worker  # noqa: E402

TOKEN = "test-token-value-123"

FAKE_DEPLOY = r'''#!/usr/bin/env bash
# deploy.sh와 같은 인터페이스의 가짜. 호출 기록은 $FAKE_LOG 에 남긴다
echo "$*" >> "$FAKE_LOG"
cmd="$1"; shift
case "$cmd" in
  foundation-info)
    # 최신 foundation 출력(compact JSON, 마지막 줄). FAIL이면 실패, FAKE_FOUNDATION_JSON이 있으면 그 값, 없으면 캐시 파일, 아무것도 없으면 {}
    [ "${FAKE_FOUNDATION_FAIL:-0}" = "0" ] || { echo "foundation 출력을 읽지 못했습니다" >&2; exit 1; }
    if [ -n "${FAKE_FOUNDATION_JSON:-}" ]; then echo "$FAKE_FOUNDATION_JSON"
    elif [ -f "$FAKE_DEPLOYMENTS/foundation.json" ]; then tr -d '\n' < "$FAKE_DEPLOYMENTS/foundation.json"; echo
    else echo '{}'; fi ;;
  make-id) echo "fake0001" ;;
  image-ref) echo "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps:$1-r1" ;;
  up)
    while [ $# -gt 0 ]; do case "$1" in --id) id="$2"; shift 2;; *) shift;; esac; done
    [ "${FAKE_UP_RC:-0}" = "0" ] || { echo "plan 실패" >&2; exit "$FAKE_UP_RC"; }
    mkdir -p "$FAKE_DEPLOYMENTS/$id"; echo "plan-bytes-$id" > "$FAKE_DEPLOYMENTS/$id/tfplan" ;;
  build)
    echo "빌드 로그" >&2
    # 빌드가 실제로 받은 소스의 경로와 SHA-256(없으면 MISSING)을 남긴다. 내려받은 파일이 승인된 소스인지 시험이 확인한다
    if [ -n "${FAKE_BUILD_SRC_LOG:-}" ]; then
      src=""; while [ $# -gt 0 ]; do case "$1" in --source) src="$2"; shift 2;; *) shift;; esac; done
      { echo "$src"
        if [ -f "$src" ]; then (sha256sum "$src" 2>/dev/null || shasum -a 256 "$src") | cut -d' ' -f1; else echo MISSING; fi
      } > "$FAKE_BUILD_SRC_LOG"
    fi
    [ "${FAKE_BUILD_RC:-0}" = "0" ] || { echo "빌드 실패 password=hunter2" >&2; exit "$FAKE_BUILD_RC"; }
    echo "${FAKE_BUILD_IMAGE}" ;;
  apply)
    echo "[00:00:01] apply (저장된 계획)"
    echo "[00:00:02] 헬스체크 대기 (최대 300초)"
    if [ -n "${FAKE_HANG:-}" ]; then
      ( while true; do date +%s%N > "$FAKE_HEARTBEAT"; sleep 0.2; done ) &
      sleep 60
    fi
    [ "${FAKE_APPLY_RC:-0}" = "0" ] || { echo "[00:00:09] 헬스체크 실패" ; exit "$FAKE_APPLY_RC"; }
    echo '{"url": {"value": "http://alb.example:8001"}}' > "$FAKE_DEPLOYMENTS/$1/outputs.json" ;;
  diagnose) echo '{"deployId": "fake0001", "targets": []}' ;;
  *) echo "unknown $cmd" >&2; exit 9 ;;
esac
'''

# aws CLI의 가짜(sts assume-role, Fargate 할당량 조회). 호출 인자는 $FAKE_AWS_LOG 에 남긴다
#   sts assume-role                : 동작은 $FAKE_AWS_MODE 로 고른다
#   service-quotas, cloudwatch     : 할당량 FAKE_QUOTA_VALUE(기본 1000), 사용 중 vCPU FAKE_QUOTA_USED(기본 0, none이면 지표 없음).
#                                    FAKE_QUOTA_FAIL=all(할당량 조회 전부 실패)|applied(적용된 값만 없음), FAKE_USAGE_FAIL=1(지표 조회 실패),
#                                    FAKE_QUOTA_NOMETRIC=1(응답에 UsageMetric 없음)
FAKE_AWS = r'''#!/usr/bin/env bash
echo "$*" >> "$FAKE_AWS_LOG"
case "$1" in
  service-quotas)
    case "${FAKE_QUOTA_FAIL:-0}" in
      all) echo "An error occurred (AccessDeniedException) when calling the $2 operation: not authorized" >&2; exit 254 ;;
      applied) if [ "$2" = "get-service-quota" ]; then echo "An error occurred (NoSuchResourceException) when calling the GetServiceQuota operation" >&2; exit 254; fi ;;
    esac
    metric=', "UsageMetric": {"MetricNamespace": "AWS/Usage", "MetricName": "ResourceCount", "MetricDimensions": {"Class": "Standard/OnDemand", "Resource": "vCPU", "Service": "Fargate", "Type": "Resource"}, "MetricStatisticRecommendation": "Maximum"}'
    [ "${FAKE_QUOTA_NOMETRIC:-0}" = "0" ] || metric=""
    printf '{"Quota": {"ServiceCode": "fargate", "QuotaCode": "L-3032A538", "QuotaName": "Fargate On-Demand vCPU resource count", "Value": %s%s}}\n' "${FAKE_QUOTA_VALUE:-1000}" "$metric"
    exit 0 ;;
  cloudwatch)
    [ "${FAKE_USAGE_FAIL:-0}" = "0" ] || { echo "An error occurred (AccessDenied) when calling the GetMetricStatistics operation: not authorized" >&2; exit 254; }
    if [ "${FAKE_QUOTA_USED:-0}" = "none" ]; then echo '{"Label": "ResourceCount", "Datapoints": []}'
    else printf '{"Label": "ResourceCount", "Datapoints": [{"Timestamp": "2026-10-11T00:00:00+00:00", "Maximum": %s, "Unit": "None"}, {"Timestamp": "2026-10-11T00:05:00+00:00", "Maximum": 0, "Unit": "None"}]}\n' "${FAKE_QUOTA_USED:-0}"; fi
    exit 0 ;;
esac
case "${FAKE_AWS_MODE:-ok}" in
  ok) echo "${FAKE_AWS_ARN:-arn:aws:sts::123456789012:assumed-role/PavedCloudsReadOnlyRole/paved-clouds-verify}" ;;
  denied) echo "An error occurred (AccessDenied) when calling the AssumeRole operation: User: arn:aws:iam::999999999999:user/operator is not authorized to perform: sts:AssumeRole on resource: arn:aws:iam::123456789012:role/PavedCloudsReadOnlyRole" >&2; exit 254 ;;
  expired) echo "An error occurred (ExpiredToken) when calling the AssumeRole operation: The security token included in the request is expired" >&2; exit 254 ;;
  invalid) echo "An error occurred (ValidationError) when calling the AssumeRole operation: 1 validation error detected" >&2; exit 254 ;;
  hang) exec sleep 30 ;;
esac
'''


class FakeBackend:
    """back/API.md의 필요한 부분만 흉내 내는 서버."""

    def __init__(self):
        self.projects = []
        self.plans = {}          # project_id → [PlanOut]
        self.analyses = {}       # project_id → AnalysisOut
        self.created_plans = []
        self.uploads = []        # (plan_id, bytes, token 헤더)
        self.events = []         # (deployment_id, body)
        self.claims = []         # claim 요청에 대한 응답 큐
        self.headers_seen = []   # (path, X-Worker-Token)
        self.plan_status = 201
        self.event_failures = 0   # 앞으로 몇 번의 이벤트 보고를 503으로 거절할지
        self.pages = None         # 목록 페이지(리스트의 리스트). 지정하면 cursor로 넘긴다
        self.upload_failures = 0  # 앞으로 몇 번의 plan 파일 업로드를 503으로 거절할지
        self.lose_plan_response = 0   # 앞으로 몇 번의 계획 등록을 "서버에는 등록하고 응답 없이 연결을 닫는" 방식으로 처리할지
        self.plans_get_failures = 0   # 앞으로 몇 번의 계획 목록 조회를 503으로 거절할지
        self.connections = []         # GET /api/worker/connections/pending 의 응답
        self.pending_status = 200     # 200이 아니면 대기 연결 조회를 그 코드로 거절한다(백엔드에 연결 API가 없는 경우 등)
        self.conn_posts = []          # (경로, 본문, X-Worker-Token) — 연결 complete·fail 보고
        self.conn_post_code = 200     # complete·fail 보고에 돌려줄 코드
        self.conn_post_failures = 0   # 앞으로 몇 번의 연결 보고를 503으로 거절할지
        self.order = []               # worker 경로 호출 순서: "claim", "event", "pending", "conn"
        self.sources = {}             # project_id → 소스 ZIP 바이트(GET /api/worker/projects/{id}/source). 없으면 404
        self.source_failures = []     # 앞으로의 소스 요청에 돌려줄 HTTP 코드(먼저 넣은 것부터). 비어 있으면 정상 응답
        self.source_redirect = None   # 지정하면 소스 요청을 이 주소로 302 리다이렉트한다
        self.source_truncate = 0      # 앞으로 몇 번의 소스 응답을 "Content-Length만큼 보내지 않고 연결을 닫는" 방식으로 처리할지
        self.source_no_length = False   # True면 Content-Length 없이 보내고 연결을 닫아 끝을 알린다(길이를 미리 알 수 없는 응답)
        self.source_gets = 0          # 소스 요청을 받은 횟수
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                data = json.dumps(obj if obj is not None else {}).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                outer.headers_seen.append((self.path, self.headers.get("X-Worker-Token")))
                p = self.path
                if p == "/api/worker/connections/pending":
                    outer.order.append("pending")
                    if self.headers.get("X-Worker-Token") != TOKEN:
                        return self._send(401, {"error": "인증 실패"})
                    if outer.pending_status != 200:
                        return self._send(outer.pending_status, {"error": "없음"})
                    return self._send(200, outer.connections)
                if p.startswith("/api/projects?"):
                    if outer.pages is not None:
                        cur = p.split("cursor=")[1] if "cursor=" in p else "0"
                        idx = int(cur)
                        nxt = str(idx + 1) if idx + 1 < len(outer.pages) else None
                        return self._send(200, {"items": outer.pages[idx], "next_cursor": nxt})
                    return self._send(200, {"items": outer.projects, "next_cursor": None})
                if p.endswith("/plans"):
                    if outer.plans_get_failures > 0:
                        outer.plans_get_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    return self._send(200, outer.plans.get(p.split("/")[3], []))
                if p.endswith("/analyses/latest"):
                    a = outer.analyses.get(p.split("/")[3])
                    return self._send(200, a) if a else self._send(404, {"error": "분석 결과가 없습니다."})
                m = re.match(r"^/api/worker/projects/([^/]+)/source$", p)
                if m:
                    outer.source_gets += 1
                    if self.headers.get("X-Worker-Token") != TOKEN:
                        return self._send(401, {"error": "인증 실패"})
                    if outer.source_redirect:
                        self.send_response(302)
                        self.send_header("Location", outer.source_redirect)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if outer.source_failures:
                        return self._send(outer.source_failures.pop(0), {"error": "일시 오류"})
                    data = outer.sources.get(m.group(1))
                    if data is None:
                        return self._send(404, {"error": "소스를 찾을 수 없습니다."})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    if not outer.source_no_length:
                        self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if outer.source_truncate > 0:
                        outer.source_truncate -= 1
                        self.wfile.write(data[: len(data) // 2])
                        self.close_connection = True   # 약속한 길이를 채우지 못하고 끊는다
                        return
                    self.wfile.write(data)
                    return
                return self._send(404, {"error": "없음"})

            def do_POST(self):
                outer.headers_seen.append((self.path, self.headers.get("X-Worker-Token")))
                body = self._body()
                p = self.path
                if p == "/api/plans":
                    obj = json.loads(body)
                    outer.created_plans.append(obj)
                    if outer.plan_status != 201:
                        return self._send(outer.plan_status, {"error": "거부"})
                    created = {"id": "plan-1", "fingerprint": "f" * 64, "status": "awaiting_approval", "terraform_plan_ready": False, **obj}
                    outer.plans.setdefault(obj["project_id"], []).append(created)
                    if outer.lose_plan_response > 0:
                        outer.lose_plan_response -= 1
                        self.close_connection = True   # 서버는 등록을 끝냈지만 응답을 보내지 못한다
                        return
                    return self._send(201, created)
                if p.startswith("/api/worker/plans/") and p.endswith("/terraform-plan"):
                    if outer.upload_failures > 0:
                        outer.upload_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    outer.uploads.append((p.split("/")[4], body, self.headers.get("X-Worker-Token")))
                    for plans in outer.plans.values():
                        for pl in plans:
                            if pl.get("id") == p.split("/")[4]:
                                pl["terraform_plan_ready"] = True
                    return self._send(200, {"ok": True})
                if p.startswith("/api/worker/connections/") and p.endswith(("/complete", "/fail")):
                    if self.headers.get("X-Worker-Token") != TOKEN:
                        return self._send(401, {"error": "인증 실패"})
                    if outer.conn_post_failures > 0:
                        outer.conn_post_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    outer.order.append("conn")
                    outer.conn_posts.append((p, json.loads(body), self.headers.get("X-Worker-Token")))
                    return self._send(outer.conn_post_code, {})
                if p == "/api/worker/deployments/claim":
                    outer.order.append("claim")
                    if self.headers.get("X-Worker-Token") != TOKEN:
                        return self._send(401, {"error": "인증 실패"})
                    return self._send(200, {"job": outer.claims.pop(0) if outer.claims else None})
                if p.startswith("/api/worker/deployments/") and p.endswith("/events"):
                    if outer.event_failures > 0:
                        outer.event_failures -= 1
                        return self._send(503, {"error": "일시 오류"})
                    outer.order.append("event")
                    outer.events.append((p.split("/")[4], json.loads(body)))
                    return self._send(200, {})
                return self._send(404, {"error": "없음"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wk-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "deployments").mkdir()
        script = self.tmp / "deploy.sh"
        script.write_bytes(FAKE_DEPLOY.encode("utf-8"))   # \n 줄 끝으로 쓴다
        self.log_file = self.tmp / "calls.log"
        self.old_env = dict(os.environ)
        os.environ.update({"FAKE_LOG": worker.posix(self.log_file), "FAKE_DEPLOYMENTS": worker.posix(self.tmp / "deployments")})
        for k in ("FAKE_UP_RC", "FAKE_BUILD_RC", "FAKE_APPLY_RC", "FAKE_BUILD_IMAGE", "FAKE_HANG", "FAKE_FOUNDATION_JSON", "FAKE_FOUNDATION_FAIL",
                  "FAKE_AWS_MODE", "FAKE_AWS_ARN", "FAKE_QUOTA_VALUE", "FAKE_QUOTA_USED", "FAKE_QUOTA_FAIL", "FAKE_USAGE_FAIL", "FAKE_QUOTA_NOMETRIC",
                  "FAKE_BUILD_SRC_LOG"):
            os.environ.pop(k, None)
        os.environ["WORKER_API_TOKEN"] = TOKEN
        os.environ["FAKE_HEARTBEAT"] = worker.posix(self.tmp / "heartbeat")
        self.old_retry_delay = worker.REPORT_RETRY_DELAY
        worker.REPORT_RETRY_DELAY = 0.0   # 보고 재시도 대기를 없앤다
        self.addCleanup(lambda: setattr(worker, "REPORT_RETRY_DELAY", self.old_retry_delay))
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.old_env)))
        self.cfg = worker.Config(token=TOKEN, deploy_sh=script, deployments_dir=self.tmp / "deployments")
        # 모든 시험은 가짜 aws CLI를 쓴다(연결 확인과 Fargate 할당량 조회). 진짜 aws를 부르지 않는다
        aws_script = self.tmp / "aws.sh"
        aws_script.write_bytes(FAKE_AWS.encode("utf-8"))
        self.aws_log = self.tmp / "aws.log"
        os.environ["FAKE_AWS_LOG"] = worker.posix(self.aws_log)
        self.cfg.aws_cmd = [self.cfg.bash, worker.posix(aws_script)]
        self.backend = FakeBackend()
        self.addCleanup(self.backend.close)
        self.api = worker.Api(self.backend.url, TOKEN, timeout=10)
        self.cfg.api_url = self.backend.url

    def calls(self):
        return self.log_file.read_text(encoding="utf-8").splitlines() if self.log_file.exists() else []

    def aws_calls(self):
        return self.aws_log.read_text(encoding="utf-8").splitlines() if self.aws_log.exists() else []


GOOD_RESULT = {
    "app_config": {"container_port": 8000, "health_check_path": "/health", "use_database": True,
                   "environment": {"COOKIE_SECURE": "false"}, "init_command": ["python", "-m", "backend.app.initialize_database"]},
    "dockerfile": "sample-back/Dockerfile",
    "scale": {"expected_users": "~1,000", "traffic_pattern": "steady", "monthly_budget_usd": 120},
}
SRC_BYTES = b"src-zip-bytes"
SRC_SHA = hashlib.sha256(SRC_BYTES).hexdigest()
PROJECT = {"id": "11111111-1111-1111-1111-111111111111", "name": "Launchpad", "source_sha256": SRC_SHA}


class CostTests(unittest.TestCase):
    def setUp(self):
        self.p = cost.load_prices()

    def test_estimate_matches_hand_calculation(self):
        # lean: (0.25 vCPU x 0.0696 + 0.5 GB x 0.0076) x 730 = 15.476, 공용: ALB 24.82 + RDS 24.82 + 스토리지 4.38 + 공인 IPv4 6개 21.90 = 75.92(기본 가정: 3 AZ + NAT 3대)
        e = cost.estimate("lean", self.p)
        self.assertAlmostEqual(e["app_monthly"], 15.48, places=2)
        self.assertAlmostEqual(e["shared_monthly"], 75.92, places=2)
        self.assertAlmostEqual(e["total_monthly"], 91.40, places=2)
        self.assertEqual(e["region"], "sa-east-1")
        self.assertTrue(e["excluded"])   # 제외 항목을 반드시 밝힌다
        self.assertIn("공인 IPv4 주소", [r["service"] for r in e["resources"]])   # 공인 IPv4 요금을 포함한다

    def test_public_ipv4_count_follows_the_deployed_foundation(self):
        # 기본 가정(foundation 기본값): 3 AZ + NAT 3대 = 6개
        self.assertEqual(cost.ipv4_count(self.p)[0], 6)
        # 배포된 foundation: 2 AZ, NAT 인스턴스 1대 = 3개 → lean 80.45
        f = {"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}
        self.assertEqual(cost.ipv4_count(self.p, f), (3, 2, 1))
        self.assertAlmostEqual(cost.estimate("lean", self.p, foundation=f)["total_monthly"], 80.45, places=2)
        # NAT 인스턴스가 없는 구성(assign_public_ip=true): ALB 주소만 2개
        f2 = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        self.assertEqual(cost.ipv4_count(self.p, f2), (2, 2, 0))
        # NAT 대수 출력이 없는 옛 foundation: AZ 수는 읽고 NAT는 기본 가정
        f3 = {"task_subnet_ids": ["a", "b"], "assign_public_ip": False}
        self.assertEqual(cost.ipv4_count(self.p, f3), (5, 2, 3))
        spec = cost.estimate("lean", self.p, foundation=f)["resources"][-1]["spec"]
        self.assertIn("ALB 가용 영역 2개", spec)
        self.assertIn("탄력적 IP 1개", spec)

    def test_task_public_ipv4_is_charged_per_task_when_there_is_no_nat(self):
        # NAT 인스턴스가 없는 구성은 앱 태스크마다 공인 IPv4가 붙는다(시간당 $0.005 x 730 = 월 $3.65). 태스크 수만큼 앱 비용에 더한다
        nonat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        withnat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}
        for tier, tasks in [("lean", 1), ("balanced", 1), ("roomy", 2)]:
            with self.subTest(tier=tier):
                a = cost.estimate(tier, self.p, foundation=nonat)
                b = cost.estimate(tier, self.p, foundation=withnat)
                self.assertAlmostEqual(a["app_monthly"] - b["app_monthly"], 3.65 * tasks, places=2)
        e = cost.estimate("roomy", self.p, foundation=nonat)
        self.assertIn("공인 IPv4 주소 (앱 태스크)", [r["service"] for r in e["resources"]])
        self.assertAlmostEqual(sum(r["monthlyUsd"] for r in e["resources"]), e["total_monthly"], delta=0.05)   # 항목 합이 총액과 맞다
        # foundation을 모르면 기본 가정(NAT 있음)이라 태스크 IPv4를 더하지 않는다
        self.assertNotIn("공인 IPv4 주소 (앱 태스크)", [r["service"] for r in cost.estimate("lean", self.p)["resources"]])
        # 예산 판정에도 들어간다: 이 비용이 빠져 있으면 예산을 살짝 넘는 구성이 추천됐다
        bal = cost.estimate("balanced", self.p, foundation=nonat)["total_monthly"]
        self.assertEqual(cost.recommend("~1,000", "steady", bal, self.p, foundation=nonat)["recommended"], "balanced")
        self.assertEqual(cost.recommend("~1,000", "steady", bal - 0.01, self.p, foundation=nonat)["recommended"], "lean")

    def test_balanced_and_roomy_cost_more_and_roomy_runs_two_tasks(self):
        lean, bal, roomy = (cost.estimate(t, self.p)["total_monthly"] for t in cost.ORDER)
        self.assertLess(lean, bal)
        self.assertLess(bal, roomy)
        r = cost.estimate("roomy", self.p)
        self.assertEqual((r["min_tasks"], r["max_tasks"]), (2, 4))
        self.assertIn("x 2개", r["resources"][0]["spec"])

    def test_arm_is_cheaper_than_x86(self):
        self.assertLess(cost.estimate("balanced", self.p, "ARM64")["app_monthly"], cost.estimate("balanced", self.p, "X86_64")["app_monthly"])

    def test_unknown_inputs_rejected(self):
        with self.assertRaises(ValueError):
            cost.estimate("huge", self.p)
        with self.assertRaises(ValueError):
            cost.estimate("lean", self.p, "MIPS")

    def test_users_pick_tier_and_peak_raises_one(self):
        self.assertEqual(cost.recommend("~100", "steady", None, self.p)["recommended"], "lean")
        self.assertEqual(cost.recommend("~1,000", "steady", None, self.p)["recommended"], "balanced")
        self.assertEqual(cost.recommend("~100", "peak", None, self.p)["recommended"], "balanced")
        self.assertEqual(cost.recommend("~10,000", "peak", None, self.p)["recommended"], "roomy")   # 이미 최대
        self.assertEqual(cost.recommend(None, None, None, self.p)["recommended"], "balanced")

    def test_budget_lowers_tier_and_reports_reason(self):
        r = cost.recommend("~10,000", "steady", 110, self.p)   # roomy는 199.73 > 110, balanced 106.87 이하
        self.assertEqual(r["recommended"], "balanced")
        self.assertIn("낮춤", r["reason"])

    def test_budget_below_cheapest_gives_no_recommendation_with_reason(self):
        r = cost.recommend("~100", "steady", 30, self.p)
        self.assertIsNone(r["recommended"])
        self.assertIn("91.40", r["reason"])

    def test_budget_exactly_equal_is_allowed(self):
        self.assertEqual(cost.recommend("~100", "steady", 91.40, self.p)["recommended"], "lean")

    def test_peak_cost_is_the_cost_at_max_tasks(self):
        bal = cost.estimate("balanced", self.p)   # small 1~2: 평소 1개, 부하가 최대면 2개
        self.assertAlmostEqual(bal["peak_monthly"] - bal["total_monthly"], 30.95, places=2)   # (0.5 x 0.0696 + 1 x 0.0076) x 730
        lean = cost.estimate("lean", self.p)   # 1~1이라 늘어나지 않는다
        self.assertEqual(lean["peak_monthly"], lean["total_monthly"])

    def test_peak_cost_counts_task_ipv4_when_there_is_no_nat(self):
        nonat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        a, b = cost.estimate("roomy", self.p, foundation=nonat), cost.estimate("roomy", self.p)   # roomy 2~4
        self.assertAlmostEqual(a["peak_monthly"] - a["total_monthly"], (61.90 + 3.65) * 2, places=1)   # 늘어나는 태스크마다 공인 IPv4가 붙는다
        self.assertAlmostEqual(b["peak_monthly"] - b["total_monthly"], 61.90 * 2, places=1)

    def test_large_and_xlarge_task_sizes_are_priced(self):
        # large = 2 vCPU / 4 GB: (2 x 0.0696 + 4 x 0.0076) x 730 = 123.81, xlarge는 그 두 배. modules/ecs-web-app/main.tf의 프리셋과 같은 값이다
        for size, want in [("large", 123.81), ("xlarge", 247.62)]:
            with self.subTest(size=size):
                e = cost.estimate("lean", self.p, cfg={**cost.TIERS["lean"], "task_size": size})
                self.assertAlmostEqual(e["app_monthly"], want, places=2)
        with self.assertRaises(ValueError):
            cost.estimate("lean", self.p, cfg={**cost.TIERS["lean"], "task_size": "huge"})

    def test_budget_makes_lowest_average_highest_options_within_budget(self):
        r = cost.recommend("~1,000", "steady", 300, self.p)
        opts = r["options"]
        self.assertEqual([(o["rank"], o["tier"]) for o in opts], [("lowest", "lean"), ("average", "balanced"), ("highest", "roomy")])
        for o in opts:
            self.assertLessEqual(o["peak_monthly"], 300)   # 부하가 최대일 때도 예산을 넘지 않는다
        self.assertLess(opts[0]["total_monthly"], opts[1]["total_monthly"])
        self.assertLess(opts[1]["total_monthly"], opts[2]["total_monthly"])
        # 최대 안은 예산 한도까지 채운다: 태스크를 하나 더 늘리면 예산을 넘는다
        top = opts[2]
        over = cost.estimate("roomy", self.p, cfg={**cost.TIERS["roomy"], "max_tasks": top["max_tasks"] + 1})
        self.assertGreater(over["peak_monthly"], 300)
        self.assertEqual([o["recommended"] for o in opts], [False, True, False])   # ~1,000명은 평균(balanced)

    def test_budget_is_the_only_limit_so_a_large_budget_allows_more_than_four_tasks(self):
        top = cost.recommend("~10,000", "steady", 1000, self.p)["options"][-1]
        self.assertEqual((top["tier"], top["task_size"], top["min_tasks"]), ("roomy", "medium", 2))
        self.assertGreater(top["max_tasks"], 4)   # 예전에는 max_tasks 4개가 코드로 박혀 있었다
        self.assertLessEqual(top["peak_monthly"], 1000)

    def test_small_budget_drops_options_that_cannot_be_made(self):
        # 110달러: 최대(medium 2개, 약 200달러)는 못 만든다. 평균(balanced)을 예산 한도까지 채워 최대 안으로 삼는다
        r = cost.recommend("~10,000", "steady", 110, self.p)
        self.assertEqual([(o["rank"], o["tier"], o["max_tasks"]) for o in r["options"]], [("lowest", "lean", 1), ("highest", "balanced", 1)])
        # 100달러: balanced의 평소 비용(106.87)도 못 담아 최저 안만 남는다
        r = cost.recommend("~10,000", "steady", 100, self.p)
        self.assertEqual([(o["rank"], o["tier"]) for o in r["options"]], [("lowest", "lean")])
        self.assertEqual(r["recommended"], "lean")

    def test_budget_exactly_equal_to_an_options_peak_keeps_that_option(self):
        # 반올림 오차로 딱 맞는 예산이 탈락하지 않아야 한다(최소 구성의 월 비용과 같은 예산)
        nonat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        for foundation in (None, nonat):
            for tier, tasks in [("balanced", 1), ("roomy", 2)]:
                with self.subTest(tier=tier, nat=foundation is None):
                    exact = cost.estimate(tier, self.p, foundation=foundation, cfg={**cost.TIERS[tier], "max_tasks": tasks})["peak_monthly"]
                    got = {o["tier"]: o for o in cost.recommend("~1,000", "steady", exact, self.p, foundation=foundation)["options"]}
                    self.assertIn(tier, got)
                    self.assertLessEqual(got[tier]["peak_monthly"], exact)
                    below = {o["tier"] for o in cost.recommend("~1,000", "steady", exact - 0.01, self.p, foundation=foundation)["options"]}
                    self.assertNotIn(tier, below)   # 1센트만 모자라도 그 안은 만들 수 없다

    def test_average_option_is_midway_between_the_lowest_and_the_actual_highest(self):
        # 월 $250: 최대 안은 medium 2개(약 199.73)까지만 채워진다. 중간 금액은 예산 원금이 아니라 이 실제 최대 안 기준이어야 한다
        low, avg, high = cost.recommend("~1,000", "steady", 250, self.p)["options"]
        self.assertEqual((low["peak_monthly"], high["peak_monthly"]), (91.40, 199.73))
        mid = (low["peak_monthly"] + high["peak_monthly"]) / 2   # 145.565
        self.assertLessEqual(avg["peak_monthly"], mid)
        over = cost.estimate("balanced", self.p, cfg={**cost.TIERS["balanced"], "max_tasks": avg["max_tasks"] + 1})
        self.assertGreater(over["peak_monthly"], mid)   # 하나 더 늘리면 중간을 넘는다 = 중간에 가장 가깝게 채웠다
        self.assertLess(avg["peak_monthly"], 150)   # 예산 원금 기준이면 168.78이 나왔다

    def test_reason_counts_only_the_options_that_were_made(self):
        for budget, n, want in [(300, 3, "3개 안은 모두"), (110, 2, "2개 안은 모두"), (100, 1, "하나뿐")]:
            with self.subTest(budget=budget):
                r = cost.recommend("~1,000", "steady", budget, self.p)
                self.assertEqual(len(r["options"]), n)
                self.assertIn(want, r["reason"])
                self.assertNotIn("세 안 모두", r["reason"])   # 안이 세 개보다 적은데 세 안이라고 말하면 모순이다

    def test_vcpu_quota_caps_presets_and_drops_options_that_cannot_fit(self):
        # 예산이 없어도 할당량은 지킨다. 남은 3 vCPU: roomy(medium 1 vCPU, 2~4개)는 2~3개로 줄고, small(0.5 vCPU)은 영향이 없다
        opts = {o["tier"]: o for o in cost.recommend("~10,000", "steady", None, self.p, max_vcpu=3)["options"]}
        self.assertEqual((opts["roomy"]["max_tasks"], opts["roomy"]["peak_vcpu"], opts["roomy"]["quota_limited"]), (3, 3.0, True))
        self.assertEqual((opts["balanced"]["max_tasks"], opts["balanced"]["quota_limited"]), (2, False))
        # 남은 1.5 vCPU: roomy의 최소 구성(medium 2개 = 2 vCPU)을 못 담아 빠지고, 이유에 할당량이 나온다
        r = cost.recommend("~10,000", "steady", None, self.p, max_vcpu=1.5)
        self.assertEqual([o["tier"] for o in r["options"]], ["lean", "balanced"])
        self.assertEqual(r["recommended"], "balanced")
        self.assertIn("낮춤", r["reason"])
        self.assertIn("할당량", r["reason"])
        # 할당량을 모르면(None) 제한하지 않는다
        self.assertEqual([o["max_tasks"] for o in cost.recommend("~10,000", "steady", None, self.p)["options"]], [1, 2, 4])

    def test_budget_options_never_exceed_the_vcpu_quota(self):
        # 월 $1000이면 할당량 없이는 최대 안이 medium 14개(14 vCPU). AWS 기본 할당량은 리전당 6 vCPU라 그만큼은 띄울 수 없다
        self.assertEqual(cost.recommend("~10,000", "steady", 1000, self.p)["options"][-1]["peak_vcpu"], 14.0)
        r = cost.recommend("~10,000", "steady", 1000, self.p, max_vcpu=6)
        for o in r["options"]:
            self.assertLessEqual(o["peak_vcpu"], 6)
            self.assertLessEqual(o["peak_monthly"], 1000)
        top = r["options"][-1]
        self.assertEqual((top["tier"], top["max_tasks"], top["quota_limited"]), ("roomy", 6, True))
        self.assertIn("할당량(남은 6 vCPU)", r["reason"])
        # 평균 안은 할당량으로 줄어든 실제 최대 안을 기준으로 잡는다
        low, avg, high = (o["peak_monthly"] for o in r["options"])
        self.assertLessEqual(avg, (low + high) / 2)

    def test_quota_too_small_for_the_smallest_option_gives_no_recommendation(self):
        for budget in (None, 1000):
            with self.subTest(budget=budget):
                r = cost.recommend("~100", "steady", budget, self.p, max_vcpu=0.2)   # 가장 작은 구성은 0.25 vCPU가 필요하다
                self.assertIsNone(r["recommended"])
                self.assertEqual(r["options"], [])
                self.assertIn("Fargate 할당량이 부족", r["reason"])

    def test_without_budget_the_three_presets_are_the_options(self):
        r = cost.recommend("~1,000", "steady", None, self.p)
        self.assertEqual([(o["rank"], o["tier"], o["min_tasks"], o["max_tasks"]) for o in r["options"]],
                         [("lowest", "lean", 1, 1), ("average", "balanced", 1, 2), ("highest", "roomy", 2, 4)])

    def test_options_never_exceed_any_budget_and_grow_with_it(self):
        nonat = {"task_subnet_ids": ["a", "b"], "assign_public_ip": True}
        for foundation in (None, nonat):
            prev_top = 0
            for budget in range(60, 1500, 13):
                r = cost.recommend("~1,000", "steady", budget, self.p, foundation=foundation)
                if not r["options"]:
                    self.assertIsNone(r["recommended"])
                    continue
                self.assertEqual([o["recommended"] for o in r["options"]].count(True), 1)
                for o in r["options"]:
                    self.assertLessEqual(o["peak_monthly"], budget, (budget, o))
                    self.assertGreaterEqual(o["max_tasks"], o["min_tasks"])
                peaks = [o["peak_monthly"] for o in r["options"]]
                self.assertEqual(peaks, sorted(peaks))
                self.assertGreaterEqual(peaks[-1], prev_top)   # 예산이 늘면 최대 안도 줄지 않는다
                prev_top = peaks[-1]


class AppConfigTests(unittest.TestCase):
    def conv(self, result, tier="balanced"):
        return worker.app_config_from_analysis(result, tier)

    def test_valid_config_gets_size_from_tier_not_from_analysis(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["app_config"]["task_size"] = "medium"   # LLM이 정해도 무시한다
        app, df = self.conv(res, "balanced")
        self.assertEqual((app["task_size"], app["min_tasks"], app["max_tasks"]), ("small", 1, 2))
        # 예산으로 max_tasks를 정한 구성(tier_cfg)은 프리셋 대신 그 값을 쓴다. 코드로 박아 둔 상한은 없다
        app, _ = worker.app_config_from_analysis(res, "roomy", {"task_size": "medium", "min_tasks": 2, "max_tasks": 14})
        self.assertEqual((app["task_size"], app["min_tasks"], app["max_tasks"]), ("medium", 2, 14))
        self.assertEqual(df, "sample-back/Dockerfile")
        self.assertEqual(app["init_command"], ["python", "-m", "backend.app.initialize_database"])
        self.assertTrue(app["use_database"])

    def test_defaults(self):
        app, df = self.conv({"app_config": {"container_port": 3000}}, "lean")
        self.assertEqual((app["health_check_path"], app["environment"], app["use_database"], df), ("/", {}, False, "Dockerfile"))
        self.assertNotIn("init_command", app)

    def test_rejections(self):
        base = lambda **kw: {"app_config": {"container_port": 8000, **kw}}   # noqa: E731
        bad = [
            ({}, "app_config가 없"),
            ({"app_config": {}}, "container_port"),
            (base(container_port=0), "container_port"),
            (base(container_port=70000), "container_port"),
            (base(container_port=True), "container_port"),
            (base(health_check_path="health"), "health_check_path"),
            (base(health_check_path="/a b"), "health_check_path"),
            (base(environment={"DATABASE_URL": "x"}), "DATABASE_URL"),
            (base(environment={"API_KEY": "x"}), "허용되지"),
            (base(environment={"lower": "x"}), "허용되지"),
            (base(environment={"A": 1}), "문자열"),
            (base(environment={f"K{i}": "v" for i in range(21)}), "20개"),
            (base(init_command="python"), "init_command"),
            (base(init_command=["a"] * 11), "init_command"),
            (base(init_command=[""]), "init_command"),
        ]
        for res, expect in bad:
            with self.subTest(res=str(res)[:60]):
                with self.assertRaises(worker.PlanError) as cm:
                    self.conv(res)
                self.assertIn(expect, str(cm.exception))

    def test_secret_looking_env_values_are_rejected_even_with_harmless_names(self):
        for val in ["sk-abcdefghijklmnopqrstuvwxyz", "ghp_" + "a" * 30, "-----BEGIN RSA PRIVATE KEY-----", "mysql://u:p@h:3306/db",
                    "password=hunter2", "AKIAABCDEFGHIJKLMNOP", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig"]:
            with self.subTest(val=val[:20]):
                with self.assertRaises(worker.PlanError) as cm:
                    self.conv({"app_config": {"container_port": 80, "environment": {"APP_CONFIG": val}}})
                self.assertIn("APP_CONFIG", str(cm.exception))
                self.assertNotIn(val, str(cm.exception))   # 오류 메시지에도 값을 싣지 않는다

    def test_normal_env_values_pass(self):
        app, _ = self.conv({"app_config": {"container_port": 80, "environment": {"COOKIE_SECURE": "false", "BASE_URL": "https://example.com/a", "LANG_CODE": "ko_KR"}}})
        self.assertEqual(app["environment"]["BASE_URL"], "https://example.com/a")

    def test_dockerfile_path_must_stay_inside_source(self):
        for df in ["/etc/Dockerfile", "../Dockerfile", "a/../../b", ""]:
            with self.subTest(df=df):
                with self.assertRaises(worker.PlanError):
                    self.conv({"app_config": {"container_port": 80}, "dockerfile": df})

    def test_scale(self):
        self.assertEqual(worker.scale_from_analysis(GOOD_RESULT), ("~1,000", "steady", 120.0))
        self.assertEqual(worker.scale_from_analysis({}), (None, None, None))
        with self.assertRaises(worker.PlanError):
            worker.scale_from_analysis({"scale": {"monthly_budget_usd": "abc"}})
        with self.assertRaises(worker.PlanError):
            worker.scale_from_analysis({"scale": {"monthly_budget_usd": -1}})


class RedactTests(unittest.TestCase):
    def test_secrets_are_masked(self):
        for text in ["AWS_SECRET_ACCESS_KEY=abcd1234", "password: hunter2", "key AKIAABCDEFGHIJKLMNOP end", "mysql://user:pw123@host:3306/db"]:
            with self.subTest(text=text):
                out = worker.redact(text)
                self.assertIn("[가림]", out)
                for secret in ["abcd1234", "hunter2", "AKIAABCDEFGHIJKLMNOP", "pw123"]:
                    self.assertNotIn(secret, out)

    def test_plain_text_kept(self):
        self.assertEqual(worker.redact("헬스체크 대기 (최대 300초)"), "헬스체크 대기 (최대 300초)")


class ApiTests(Base):
    def test_token_only_on_worker_paths(self):
        self.api.get("/api/projects?limit=20")
        self.api.post("/api/worker/deployments/claim")
        seen = dict(self.backend.headers_seen)
        self.assertIsNone(seen["/api/projects?limit=20"])
        self.assertEqual(seen["/api/worker/deployments/claim"], TOKEN)

    def test_http_error_becomes_apierror_with_status(self):
        with self.assertRaises(worker.ApiError) as cm:
            self.api.get("/api/projects/x/analyses/latest")
        self.assertEqual(cm.exception.status, 404)

    def test_wrong_token_is_401(self):
        bad = worker.Api(self.backend.url, "wrong")
        with self.assertRaises(worker.ApiError) as cm:
            bad.post("/api/worker/deployments/claim")
        self.assertEqual(cm.exception.status, 401)

    def test_connection_failure_is_apierror_status_0(self):
        dead = worker.Api("http://127.0.0.1:9", TOKEN, timeout=2)
        with self.assertRaises(worker.ApiError) as cm:
            dead.get("/api/projects")
        self.assertEqual(cm.exception.status, 0)

    def test_config_repr_hides_token(self):
        self.assertNotIn(TOKEN, repr(self.cfg))


class PlannerTests(Base):
    def analysis(self, result=None):
        return {"id": "22222222-2222-2222-2222-222222222222", "project_id": PROJECT["id"], "result": result or GOOD_RESULT}

    def test_happy_path_registers_plan_with_hash_and_uploads_binary(self):
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertEqual(plan["id"], "plan-1")
        body = self.backend.created_plans[0]
        self.assertEqual((body["project_id"], body["target"], body["module_id"]), (PROJECT["id"], "aws", "ecs-web-app"))
        v = body["variables"]
        self.assertEqual(v["deploy_id"], "fake0001")
        self.assertTrue(v["image"].endswith("apps:fake0001-r1"))
        self.assertEqual(v["dockerfile"], "sample-back/Dockerfile")
        self.assertEqual(v["tier"], "balanced")
        self.assertTrue(v["recommended"])
        self.assertEqual(v["app"]["task_size"], "small")
        self.assertEqual(v["source_sha256"], SRC_SHA)          # 승인된 소스 지문이 계획 변수(fingerprint 대상)에 들어간다
        self.assertEqual(v["cpu_architecture"], "X86_64")
        self.assertEqual(v["resources"][0]["service"], "ECS Fargate")
        self.assertEqual(body["cost_estimate"]["currency"], "USD")
        self.assertEqual(body["cost_estimate"]["period"], "month")
        self.assertGreater(float(body["cost_estimate"]["amount"]), 0)
        # 해시는 실제 plan 파일의 SHA-256, 업로드한 바이트도 같은 파일
        plan_bytes = (self.tmp / "deployments" / "fake0001" / "tfplan").read_bytes()
        self.assertEqual(body["terraform_plan_sha256"], hashlib.sha256(plan_bytes).hexdigest())
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertEqual(self.backend.uploads[0][:2], ("plan-1", plan_bytes))
        self.assertEqual(self.backend.uploads[0][2], TOKEN)   # 업로드는 worker 경로라 토큰이 간다
        self.assertIn("제외", body["summary"])
        self.assertIn("가격표", body["summary"])

    def test_plan_carries_budget_sized_app_config_and_the_three_options(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"].update(expected_users="~10,000", monthly_budget_usd=400)
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        body = self.backend.created_plans[0]
        v = body["variables"]
        self.assertEqual(v["tier"], "roomy")
        # medium 2개 이상, 최대 태스크 수는 예산 400달러 안에서 정해진다(기본 가정 foundation: 공용 75.92 + 태스크당 61.90 x 5 = 385.4)
        self.assertEqual((v["app"]["task_size"], v["app"]["min_tasks"], v["app"]["max_tasks"]), ("medium", 2, 5))
        self.assertLessEqual(v["cost"]["peak_monthly"], 400)
        self.assertEqual(v["cost"]["budget_usd"], 400.0)
        self.assertEqual([(o["rank"], o["tier"], o["recommended"]) for o in v["options"]],
                         [("lowest", "lean", False), ("average", "balanced", False), ("highest", "roomy", True)])
        self.assertIn("월 예산 $400.00 이내", body["summary"])
        self.assertIn("안 비교", body["summary"])
        # 승인 화면에 나가는 금액(cost_estimate.amount)은 예산을 판정한 기준과 같은 부하 최대 비용이다. 평소 비용은 variables.cost에 따로 있다
        self.assertAlmostEqual(float(body["cost_estimate"]["amount"]), v["cost"]["peak_monthly"], places=2)
        self.assertAlmostEqual(v["cost"]["total_monthly"], 199.73, places=2)

    def test_approval_amount_is_the_peak_cost_the_budget_was_judged_on(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"].update(expected_users="~10,000", monthly_budget_usd=400)   # roomy medium 2~5개: 평소 $199.73, 최대 약 $385
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        body = self.backend.created_plans[0]
        v = body["variables"]
        amount = float(body["cost_estimate"]["amount"])
        self.assertEqual(body["cost_estimate"]["amount"], f"{v['cost']['peak_monthly']:.4f}")
        self.assertGreater(amount, v["cost"]["total_monthly"])   # 평소 금액만 보여주면 승인 때 최대 청구를 모른다
        self.assertLessEqual(amount, 400)                         # 승인 금액은 사용자가 정한 예산 안이다
        self.assertEqual(v["cost"]["amount_basis"], "peak_monthly")
        # 승인 화면은 리소스 행을 나열하고 amount를 합계로 보여준다: 행의 합이 합계와 맞고, 최대 시 추가분이 행으로 보인다
        rows = v["resources"]
        self.assertAlmostEqual(sum(r["monthlyUsd"] for r in rows), amount, delta=0.05)
        extra = rows[-1]
        self.assertEqual(extra["service"], "ECS Fargate 오토스케일링 최대 시 추가분")
        self.assertAlmostEqual(extra["monthlyUsd"], amount - v["cost"]["total_monthly"], delta=0.02)
        self.assertIn("태스크 3개 더 (최대 5개)", extra["spec"])
        self.assertIn("승인 금액은 부하가 최대일 때(태스크 5개)", body["summary"])
        self.assertIn("평소 월 추정", body["summary"])

    def test_no_autoscale_row_when_the_peak_equals_the_typical_cost(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"].update(expected_users="~100", monthly_budget_usd=100)   # 월 $100에는 최저 안(1~1개)만 들어간다
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        body = self.backend.created_plans[0]
        v = body["variables"]
        self.assertEqual(float(body["cost_estimate"]["amount"]), v["cost"]["total_monthly"])
        self.assertFalse(any("오토스케일링" in r["service"] for r in v["resources"]))

    def quota_plan(self, budget=1000, users="~10,000"):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"].update(expected_users=users, monthly_budget_usd=budget)
        return worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))

    def test_plan_reads_the_fargate_quota_and_caps_the_biggest_option(self):
        # 예산 $1000이면 할당량 없이는 medium 14개(14 vCPU)지만, 할당량 6 - 사용 중 2 = 남은 4 vCPU 안으로 줄어든다
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_USED="2")
        self.cfg.region = "sa-east-1"
        self.quota_plan()
        body = self.backend.created_plans[0]
        v = body["variables"]
        self.assertEqual(v["fargate_vcpu"], {"quota_vcpu": 6.0, "used_vcpu": 2.0, "reserved_vcpu": 0.0, "available_vcpu": 4.0})
        self.assertEqual((v["tier"], v["app"]["task_size"], v["app"]["min_tasks"], v["app"]["max_tasks"]), ("roomy", "medium", 2, 4))
        self.assertEqual(v["cost"]["peak_vcpu"], 4.0)
        top = v["options"][-1]
        self.assertEqual((top["max_tasks"], top["peak_vcpu"], top["quota_limited"]), (4, 4.0, True))
        self.assertTrue(all(o["peak_vcpu"] <= 4 for o in v["options"]))
        self.assertIn("할당량(남은 4 vCPU)", v["reason"])
        self.assertIn("Fargate vCPU 할당량: 한도 6, 사용 중 2, 승인 대기·승인된 다른 계획이 예약 0, 남은 4", body["summary"])
        # 할당량 조회 → 사용량 조회 순서이고, 문서에서 확인한 이름·차원·리전을 쓴다
        quota, usage = [c for c in self.aws_calls() if c.startswith(("service-quotas", "cloudwatch"))]
        self.assertIn("service-quotas get-service-quota --service-code fargate --quota-code L-3032A538", quota)
        self.assertIn("--region sa-east-1", quota)
        for part in ("cloudwatch get-metric-statistics", "--namespace AWS/Usage", "--metric-name ResourceCount", "Name=Service,Value=Fargate",
                     "Name=Type,Value=Resource", "Name=Resource,Value=vCPU", "Name=Class,Value=Standard/OnDemand", "--statistics Maximum"):
            self.assertIn(part, usage)

    def test_usage_is_the_maximum_over_the_window_and_zero_without_datapoints(self):
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_USED="none")   # 지표가 비어 있다 = 그 시간 동안 실행 중인 작업이 없었다
        self.quota_plan()
        self.assertEqual(self.backend.created_plans[0]["variables"]["fargate_vcpu"],
                         {"quota_vcpu": 6.0, "used_vcpu": 0.0, "reserved_vcpu": 0.0, "available_vcpu": 6.0})

    def test_usage_dimensions_fall_back_to_the_documented_ones_without_usage_metric(self):
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_USED="1", FAKE_QUOTA_NOMETRIC="1")
        self.quota_plan()
        usage = [c for c in self.aws_calls() if c.startswith("cloudwatch")][0]
        self.assertIn("Name=Resource,Value=vCPU", usage)
        self.assertIn("Name=Class,Value=Standard/OnDemand", usage)
        self.assertEqual(self.backend.created_plans[0]["variables"]["fargate_vcpu"]["available_vcpu"], 5.0)

    def test_default_quota_is_used_when_the_applied_value_is_missing(self):
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_FAIL="applied")
        self.quota_plan()
        calls = [c for c in self.aws_calls() if c.startswith("service-quotas")]
        self.assertEqual([c.split()[1] for c in calls], ["get-service-quota", "get-aws-default-service-quota"])
        self.assertEqual(self.backend.created_plans[0]["variables"]["fargate_vcpu"]["quota_vcpu"], 6.0)

    def test_not_enough_vcpu_for_the_smallest_option_is_a_plan_error(self):
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_USED="5.9")   # 남은 0.1 vCPU < 최소 구성 0.25 vCPU
        with self.assertRaises(worker.PlanError) as cm:
            self.quota_plan()
        self.assertIn("Fargate 할당량이 부족", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(self.calls(), ["foundation-info"])   # plan·ID 생성·빌드·적용은 하지 않는다

    def test_unreadable_quota_stops_planning_with_a_fix_hint(self):
        for key, value in [("FAKE_QUOTA_FAIL", "all"), ("FAKE_USAGE_FAIL", "1")]:
            with self.subTest(key=key):
                os.environ.pop("FAKE_QUOTA_FAIL", None)
                os.environ.pop("FAKE_USAGE_FAIL", None)
                os.environ[key] = value
                with self.assertRaises(worker.PlanError) as cm:
                    self.quota_plan()
                msg = str(cm.exception)
                self.assertIn("Fargate 할당량을 읽지 못해", msg)
                self.assertIn("servicequotas:GetServiceQuota", msg)
                self.assertIn("--skip-quota-check", msg)
                self.assertEqual(self.backend.created_plans, [])
                self.assertFalse(any(c.startswith("up ") for c in self.calls()))   # 한도를 모른 채로 계획을 만들지 않는다

    # --- 승인 대기·승인된 계획의 vCPU 예약 ---------------------------------------------------------------
    P2_ID = "22222222-2222-2222-2222-222222222222"

    def two_projects(self, budget="1000.0000"):
        """예산이 큰 신규 프로젝트 둘(분석 결과에는 scale이 없다). 할당량 한 몫을 둘이 나눠 가져야 하는 상황을 만든다."""
        res = json.loads(json.dumps(GOOD_RESULT))
        del res["scale"]
        projects = []
        for pid, name in ((PROJECT["id"], "A"), (self.P2_ID, "B")):
            projects.append({**PROJECT, "id": pid, "name": name, "expected_users": "~10,000", "traffic_pattern": "steady", "monthly_budget_usd": budget})
            self.backend.analyses[pid] = {"id": "aa-" + name, "project_id": pid, "result": res}
        self.backend.projects = projects
        return projects

    def test_plans_created_in_the_same_pass_do_not_share_the_same_vcpu(self):
        # 할당량 6 vCPU. 첫 프로젝트가 예산 안에서 가능한 최대(6 vCPU)를 받으면 두 번째는 같은 몫을 또 받지 못한다
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_USED="0")
        p1, p2 = self.two_projects()
        state = worker.State(retry_after=0.0, recheck_after=0.0)
        worker.plan_pending(self.api, self.cfg, state)
        self.assertEqual(len(self.backend.created_plans), 1)   # 합계 12 vCPU의 계획 두 개가 승인되지 않는다
        first = self.backend.created_plans[0]["variables"]
        self.assertEqual((first["cost"]["peak_vcpu"], first["fargate_vcpu"]["reserved_vcpu"]), (6.0, 0.0))
        self.assertIn(p2["id"], state.waiting)
        self.assertNotIn(p2["id"], state.failures)   # 용량이 풀리면 만들 수 있어서 실패로 세지 않는다
        # 몇 번을 다시 점검해도 실패 횟수가 쌓여 영구 중단되지 않는다(max_tries 3회를 넘겨도 계속 기다린다)
        for _ in range(state.max_tries + 2):
            worker.plan_pending(self.api, self.cfg, state)
        self.assertNotIn(p2["id"], state.failures)
        self.assertEqual(len(self.backend.created_plans), 1)
        # 첫 계획이 배포돼 consumed가 되면 예약은 풀리고 실제 사용량(2 vCPU)만 남는다 → 두 번째 프로젝트가 나머지 4 vCPU 안에서 계획된다
        self.backend.plans[p1["id"]][0]["status"] = "consumed"
        os.environ["FAKE_QUOTA_USED"] = "2"
        worker.plan_pending(self.api, self.cfg, state)
        self.assertEqual(len(self.backend.created_plans), 2)
        second = self.backend.created_plans[1]["variables"]
        self.assertEqual(second["fargate_vcpu"], {"quota_vcpu": 6.0, "used_vcpu": 2.0, "reserved_vcpu": 0.0, "available_vcpu": 4.0})
        self.assertLessEqual(second["cost"]["peak_vcpu"], 4)
        self.assertNotIn(p2["id"], state.waiting)

    def test_a_pending_plan_reserves_its_peak_vcpu_for_the_next_plan(self):
        # 할당량 8, 첫 프로젝트 예산이 작아 2 vCPU만 쓰는 계획 → 두 번째는 남은 6 vCPU를 받는다(예약이 전체를 막지 않는다)
        os.environ.update(FAKE_QUOTA_VALUE="8", FAKE_QUOTA_USED="0")
        p1, p2 = self.two_projects()
        p1["monthly_budget_usd"] = "200.0000"   # 최대 안: medium 2개(약 $199.73) = 2 vCPU
        worker.plan_pending(self.api, self.cfg, worker.State(retry_after=0.0, recheck_after=0.0))
        self.assertEqual(len(self.backend.created_plans), 2)
        first, second = (p["variables"] for p in self.backend.created_plans)
        self.assertEqual(first["cost"]["peak_vcpu"], 2.0)
        self.assertEqual(second["fargate_vcpu"], {"quota_vcpu": 8.0, "used_vcpu": 0.0, "reserved_vcpu": 2.0, "available_vcpu": 6.0})
        self.assertLessEqual(second["cost"]["peak_vcpu"], 6)
        self.assertIn("예약 2, 남은 6", self.backend.created_plans[1]["summary"])

    def test_waiting_projects_are_not_retried_until_retry_after(self):
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_USED="0")
        _, p2 = self.two_projects()
        state = worker.State(retry_after=3600.0, recheck_after=0.0)
        worker.plan_pending(self.api, self.cfg, state)
        self.assertIn(p2["id"], state.waiting)
        before = len(self.aws_calls())
        worker.plan_pending(self.api, self.cfg, state)
        self.assertEqual(len(self.aws_calls()), before)   # 기다리는 프로젝트는 간격 안에서는 AWS를 다시 부르지 않는다

    def test_shortage_without_any_reservation_is_still_a_counted_failure(self):
        os.environ.update(FAKE_QUOTA_VALUE="0.1", FAKE_QUOTA_USED="0")   # 가장 작은 구성(0.25 vCPU)도 못 담는 계정
        p1, _ = self.two_projects()
        self.backend.projects = [p1]
        state = worker.State(retry_after=0.0, recheck_after=0.0)
        worker.plan_pending(self.api, self.cfg, state)
        self.assertEqual(state.failures[p1["id"]][0], 1)   # 예약 때문이 아니면 기다려도 풀리지 않으므로 실패로 센다
        self.assertEqual(state.waiting, {})
        self.assertEqual(self.backend.created_plans, [])

    def test_reserved_vcpu_sums_only_pending_plans_of_other_projects(self):
        os.environ.update(FAKE_QUOTA_VALUE="100")
        p1, p2 = self.two_projects()
        legacy = {"variables": {"app": {"task_size": "medium", "max_tasks": 2}}}   # cost.peak_vcpu가 생기기 전의 계획: 1 vCPU x 2
        self.backend.plans = {
            p1["id"]: [{"id": "own", "status": "awaiting_approval", "variables": {"cost": {"peak_vcpu": 50}}}],   # 계획하려는 프로젝트 자신은 뺀다
            p2["id"]: [
                {"id": "a", "status": "awaiting_approval", "variables": {"cost": {"peak_vcpu": 3}}},
                {"id": "b", "status": "approved", **legacy},
                {"id": "c", "status": "consumed", "variables": {"cost": {"peak_vcpu": 10}}},     # 배포됨: 사용량에 이미 있다
                {"id": "d", "status": "superseded", "variables": {"cost": {"peak_vcpu": 20}}},   # 대체됨
                {"id": "e", "status": "awaiting_approval", "variables": "broken"},               # 읽을 수 없으면 건너뛴다
            ],
        }
        self.assertEqual(worker.reserved_vcpu(self.api, p1["id"]), 5.0)
        self.assertEqual(worker.reserved_vcpu(self.api, "no-such-project"), 5.0 + 50.0)   # 자신이 아니면 그 계획도 센다

    def test_plan_peak_vcpu_reads_cost_then_app_and_rejects_garbage(self):
        peak = worker.plan_peak_vcpu
        self.assertEqual(peak({"variables": {"cost": {"peak_vcpu": 4.5}}}), 4.5)
        self.assertEqual(peak({"variables": {"cost": {"peak_vcpu": 0}}}), 0.0)
        self.assertEqual(peak({"variables": {"app": {"task_size": "large", "max_tasks": 3}}}), 6.0)   # 2 vCPU x 3
        for bad in (None, {}, {"variables": None}, {"variables": {"cost": {"peak_vcpu": True}}}, {"variables": {"cost": {"peak_vcpu": -1}}},
                    {"variables": {"cost": {"peak_vcpu": float("nan")}}}, {"variables": {"app": {"task_size": "huge", "max_tasks": 2}}},
                    {"variables": {"app": {"task_size": "small", "max_tasks": True}}}):
            with self.subTest(bad=bad):
                self.assertIsNone(peak(bad))

    def test_skip_quota_check_does_not_call_aws_and_says_so(self):
        os.environ.update(FAKE_QUOTA_VALUE="6", FAKE_QUOTA_FAIL="all")   # 조회했다면 실패했을 환경
        self.cfg.quota_check = False
        self.quota_plan()
        body = self.backend.created_plans[0]
        self.assertEqual(self.aws_calls(), [])
        self.assertIsNone(body["variables"]["fargate_vcpu"])
        self.assertEqual(body["variables"]["app"]["max_tasks"], 14)   # 할당량으로 줄이지 않는다
        self.assertIn("할당량 확인을 건너뜀", body["summary"])

    def plan_with_project(self, project_fields, scale=None):
        """분석 결과의 scale을 바꿔 프로젝트 API 필드(project_fields)와의 우선순위를 시험한다. scale을 주지 않으면(None) 분석 결과에서 scale을 뺀다."""
        res = json.loads(json.dumps(GOOD_RESULT))
        if scale is None:
            del res["scale"]
        else:
            res["scale"] = scale
        return worker.plan_project(self.api, self.cfg, {**PROJECT, **project_fields}, self.analysis(res))

    def test_project_api_fields_are_used_when_the_analysis_has_no_scale(self):
        # 분석기가 프로젝트 입력을 scale에 복사하지 않아도 사용자가 프로젝트에 입력한 월 예산이 적용돼야 한다.
        # 백엔드는 예산을 "120.0000" 같은 문자열로 돌려준다
        self.plan_with_project({"expected_users": "~10,000", "traffic_pattern": "steady", "monthly_budget_usd": "120.0000"})
        v = self.backend.created_plans[0]["variables"]
        self.assertEqual(v["cost"]["budget_usd"], 120.0)
        self.assertLessEqual(v["cost"]["peak_monthly"], 120)
        self.assertEqual((v["tier"], v["app"]["max_tasks"]), ("balanced", 1))   # roomy(약 $200)는 예산을 넘어 낮췄다. 예산을 무시했다면 roomy 프리셋이었다
        self.assertIn("월 예산 $120.00 이내", self.backend.created_plans[0]["summary"])

    def test_project_api_fields_win_over_the_analysis_scale_per_field(self):
        # 분석 scale은 월 $120, ~1,000명. 프로젝트는 예산만 $100을 입력했다 → 예산은 프로젝트 값, 사용자 수는 분석 값으로 대체한다
        self.plan_with_project({"monthly_budget_usd": "100.0000", "expected_users": None, "traffic_pattern": None},
                               scale={"expected_users": "~1,000", "traffic_pattern": "steady", "monthly_budget_usd": 120})
        v = self.backend.created_plans[0]["variables"]
        self.assertEqual(v["cost"]["budget_usd"], 100.0)
        self.assertEqual([o["tier"] for o in v["options"]], ["lean"])   # $100에는 최저 안만 들어간다(권장 안은 $106.87)
        self.assertIn("~1,000명", v["reason"])

    def test_analysis_scale_is_the_fallback_when_the_project_has_no_values(self):
        # 프로젝트 값이 모두 비어 있으면 분석 결과의 scale(GOOD_RESULT: 월 $120)을 쓴다
        self.plan_with_project({"monthly_budget_usd": None, "expected_users": None, "traffic_pattern": None}, scale=dict(GOOD_RESULT["scale"]))
        self.assertEqual(self.backend.created_plans[0]["variables"]["cost"]["budget_usd"], 120.0)

    def test_scale_priority_unit(self):
        sa = worker.scale_from_analysis
        self.assertEqual(sa({}, {"expected_users": "~100", "traffic_pattern": "peak", "monthly_budget_usd": "30.0000"}), ("~100", "peak", 30.0))
        self.assertEqual(sa({"scale": {"expected_users": "~1,000", "monthly_budget_usd": 120}},
                            {"expected_users": "~100", "traffic_pattern": None, "monthly_budget_usd": None}), ("~100", None, 120.0))
        self.assertEqual(sa({"scale": {"monthly_budget_usd": 120}}, {"monthly_budget_usd": "0.0000"})[2], 0.0)   # 0을 입력했으면 0이 우선이다(None이 아니다)
        self.assertEqual(sa({"scale": {"monthly_budget_usd": 120}}, None)[2], 120.0)   # project를 안 주는 호출은 이전과 같다
        self.assertEqual(sa({}, {}), (None, None, None))

    def test_invalid_project_values_are_plan_errors_that_name_the_project_field(self):
        for key, bad in [("monthly_budget_usd", "abc"), ("monthly_budget_usd", True), ("monthly_budget_usd", "-1"),
                         ("monthly_budget_usd", "nan"), ("expected_users", 123), ("traffic_pattern", [])]:
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(worker.PlanError) as cm:
                    self.plan_with_project({key: bad})
                self.assertIn(f"프로젝트 {key}", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(self.calls(), [])   # 잘못된 입력에는 deploy.sh를 부르지 않는다

    def test_plan_without_budget_uses_presets_and_says_so(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        del res["scale"]["monthly_budget_usd"]
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        body = self.backend.created_plans[0]
        self.assertIsNone(body["variables"]["cost"]["budget_usd"])
        self.assertEqual(body["variables"]["app"]["max_tasks"], 2)   # balanced 프리셋
        self.assertIn("월 예산 입력 없음", body["summary"])

    def test_plan_is_made_with_plan_only_and_without_building(self):
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        calls = self.calls()
        up = [c for c in calls if c.startswith("up ")]
        self.assertEqual(len(up), 1)
        self.assertIn("--plan-only", up[0])
        self.assertIn("--id fake0001", up[0])
        self.assertIn("--arch X86_64", up[0])
        self.assertFalse(any(c.startswith("build") or c.startswith("apply") for c in calls))   # 승인 전에는 빌드도 적용도 하지 않는다

    def test_requested_architecture_is_passed_to_planning_and_recorded(self):
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(), arch="ARM64")
        up = [c for c in self.calls() if c.startswith("up ")][0]
        self.assertIn("--arch ARM64", up)
        self.assertEqual(self.backend.created_plans[0]["variables"]["cpu_architecture"], "ARM64")

    def test_project_without_source_sha256_is_a_plan_error_before_anything_runs(self):
        for bad in [None, "", "xyz", "A" * 64]:
            with self.subTest(bad=bad):
                proj = {"id": PROJECT["id"], "name": "x", "source_sha256": bad}
                with self.assertRaises(worker.PlanError):
                    worker.plan_project(self.api, self.cfg, proj, self.analysis())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.backend.created_plans, [])

    def test_estimate_uses_the_deployed_foundation_file(self):
        (self.tmp / "deployments" / "foundation.json").write_text(
            json.dumps({"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}), encoding="utf-8")
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        body = self.backend.created_plans[0]
        ipv4 = [r for r in body["variables"]["resources"] if r["service"] == "공인 IPv4 주소"][0]
        self.assertIn("탄력적 IP 1개", ipv4["spec"])
        self.assertAlmostEqual(float(body["cost_estimate"]["amount"]), 95.92, places=2)   # balanced, 2 AZ + NAT 1대

    def test_replan_discards_an_unapplied_previous_folder(self):
        old = self.tmp / "deployments" / "fake0001"
        old.mkdir()
        (old / "tfplan").write_text("old-plan", encoding="utf-8")
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertEqual((old / "tfplan").read_text(encoding="utf-8").strip(), "plan-bytes-fake0001")   # 새 계획으로 교체됐다
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_replan_refuses_a_folder_that_was_ever_applied(self):
        old = self.tmp / "deployments" / "fake0001"
        old.mkdir()
        (old / "history.log").write_text("x", encoding="utf-8")
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertIn("이미 적용된", str(cm.exception))
        self.assertTrue(old.exists())
        self.assertEqual(self.backend.created_plans, [])

    def test_upload_failure_keeps_the_local_plan_and_marks_it_pending(self):
        self.backend.upload_failures = worker.REPORT_RETRIES   # 재시도까지 모두 실패
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertTrue(plan.get("_upload_pending"))
        self.assertTrue((self.tmp / "deployments" / "fake0001" / "tfplan").exists())   # 파일을 지우지 않는다
        self.assertEqual(self.backend.uploads, [])
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_transient_upload_errors_are_retried_inside_plan_project(self):
        self.backend.upload_failures = 2
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertFalse(plan.get("_upload_pending"))
        self.assertEqual(len(self.backend.uploads), 1)

    def test_secret_like_values_are_not_in_posted_body(self):
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertNotIn(TOKEN, json.dumps(self.backend.created_plans))

    def test_malformed_scale_values_are_plan_errors_not_crashes(self):
        # 분석 결과의 scale 값이 리스트·객체·NaN이면 처리되지 않은 TypeError가 worker 전체를 멈췄다: 해당 프로젝트의 계획 오류로만 처리한다
        cases = [("expected_users", [], False), ("expected_users", {}, False), ("expected_users", 123, False),
                 ("traffic_pattern", [], False), ("traffic_pattern", {}, False), ("traffic_pattern", 5, False),
                 ("monthly_budget_usd", True, False), ("monthly_budget_usd", float("nan"), False),
                 ("monthly_budget_usd", float("inf"), False), ("monthly_budget_usd", "abc", False), ("monthly_budget_usd", [], False)]
        for key, bad, _ in cases:
            with self.subTest(key=key, bad=bad):
                res = json.loads(json.dumps(GOOD_RESULT))
                res["scale"][key] = bad
                with self.assertRaises(worker.PlanError):
                    worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        self.assertEqual(self.backend.created_plans, [])
        # 올바른 값(문자열 사용자 수, 숫자 예산)은 그대로 통과한다
        self.assertEqual(worker.scale_from_analysis({"scale": {"expected_users": "~100", "traffic_pattern": "peak", "monthly_budget_usd": "50"}}),
                         ("~100", "peak", 50.0))
        self.assertEqual(worker.scale_from_analysis({}), (None, None, None))

    def test_budget_too_small_raises_without_touching_backend_or_terraform(self):
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"]["monthly_budget_usd"] = 20
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        self.assertIn("예산", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(self.calls(), ["foundation-info"])   # 읽기 전용인 foundation-info만 부른다(plan·ID 생성·빌드·적용은 하지 않는다)

    def test_cost_uses_the_fresh_foundation_not_the_stale_cache_file(self):
        # 캐시(옛 구성: 2 AZ + NAT 1대)로는 balanced가 $95.92라 예산 $100 안이지만, 지금 구성(3 AZ + NAT 3대)에서는 $106.87이라 넘는다
        (self.tmp / "deployments" / "foundation.json").write_text(
            json.dumps({"task_subnet_ids": ["a", "b"], "assign_public_ip": False, "nat_instance_count": 1}), encoding="utf-8")
        os.environ["FAKE_FOUNDATION_JSON"] = json.dumps({"task_subnet_ids": ["a", "b", "c"], "assign_public_ip": False, "nat_instance_count": 3})
        res = json.loads(json.dumps(GOOD_RESULT))
        res["scale"]["monthly_budget_usd"] = 100
        worker.plan_project(self.api, self.cfg, PROJECT, self.analysis(res))
        body = self.backend.created_plans[0]
        self.assertEqual(body["variables"]["tier"], "lean")   # 옛 캐시였다면 balanced($106.87)가 예산을 넘은 채 추천됐다
        self.assertLessEqual(float(body["cost_estimate"]["amount"]), 100)
        # 비용 계산(foundation-info)이 계획 생성(up)보다 먼저다
        calls = self.calls()
        self.assertLess(calls.index("foundation-info"), [i for i, c in enumerate(calls) if c.startswith("up ")][0])

    def test_unreadable_foundation_stops_planning_instead_of_using_the_cache(self):
        (self.tmp / "deployments" / "foundation.json").write_text('{"task_subnet_ids": ["a"]}', encoding="utf-8")
        os.environ["FAKE_FOUNDATION_FAIL"] = "1"
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertIn("foundation 정보를 읽지 못해", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])
        self.assertFalse(any(c.startswith("up ") for c in self.calls()))

    def test_terraform_plan_failure_raises_and_registers_nothing(self):
        os.environ["FAKE_UP_RC"] = "1"
        with self.assertRaises(worker.PlanError) as cm:
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertIn("계획을 만들지 못했습니다", str(cm.exception))
        self.assertEqual(self.backend.created_plans, [])

    def test_backend_rejection_discards_unapplied_deploy_dir_so_retry_is_possible(self):
        self.backend.plan_status = 500
        with self.assertRaises(worker.ApiError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertFalse((self.tmp / "deployments" / "fake0001").exists())

    def test_lost_registration_response_keeps_the_plan_and_resumes_the_upload(self):
        # 서버는 등록을 끝냈는데 응답만 유실돼도 로컬 plan 파일을 지우지 않고, 서버에서 등록된 계획을 찾아 업로드를 이어간다
        self.backend.lose_plan_response = 1
        plan = worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertEqual(plan["id"], "plan-1")
        self.assertTrue((self.tmp / "deployments" / "fake0001" / "tfplan").exists())
        self.assertEqual(len(self.backend.uploads), 1)   # 같은 계획의 plan 파일이 올라갔다
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_lost_registration_response_with_unreachable_lookup_keeps_the_plan_file(self):
        # 응답이 유실됐고 등록 여부 조회까지 실패하면 등록됐는지 모른다: 파일을 남기고, 다음 점검에서 서버 목록의 계획에 올린다
        self.backend.lose_plan_response = 1
        self.backend.plans_get_failures = 1
        with self.assertRaises(worker.ApiError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertTrue((self.tmp / "deployments" / "fake0001" / "tfplan").exists())   # 지우지 않는다
        self.assertEqual(self.backend.uploads, [])
        # 다음 점검: 서버 목록에 등록된 계획이 보이므로 같은 파일을 올리고 새 계획을 또 만들지 않는다
        self.backend.projects = [PROJECT]
        self.backend.analyses[PROJECT["id"]] = self.analysis()
        state = worker.State(retry_after=0.0)
        worker.plan_pending(self.api, self.cfg, state)
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_server_rejection_with_4xx_discards_the_folder(self):
        self.backend.plan_status = 422
        with self.assertRaises(worker.ApiError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis())
        self.assertFalse((self.tmp / "deployments" / "fake0001").exists())

    def test_discard_keeps_dirs_that_were_ever_applied(self):
        for marker in ["terraform.tfstate", "history.log", "applied.app.json"]:
            d = self.tmp / "deployments" / ("keep" + marker[:3])
            d.mkdir()
            (d / marker).write_text("x", encoding="utf-8")
            (d / "tfplan").write_text("x", encoding="utf-8")
            worker.discard_unapplied(self.cfg, d.name)
            self.assertTrue(d.exists(), marker)

    def test_missing_app_config_is_a_plan_error(self):
        with self.assertRaises(worker.PlanError):
            worker.plan_project(self.api, self.cfg, PROJECT, self.analysis({"stack": []}))
        self.assertEqual(self.calls(), [])


class ExecutorTests(Base):
    IMAGE = "123456789012.dkr.ecr.sa-east-1.amazonaws.com/paved-clouds/apps:fake0001-r1"

    def make_job(self, tamper_sha=False, deploy_id="fake0001", image=None, arch="X86_64"):
        d = self.tmp / "deployments" / "fake0001"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tfplan").write_bytes(b"approved-plan")
        (self.tmp / "src.zip").write_bytes(SRC_BYTES)
        sha = hashlib.sha256(b"approved-plan").hexdigest()
        if tamper_sha:
            sha = "0" * 64
        os.environ["FAKE_BUILD_IMAGE"] = self.IMAGE
        return {"deployment_id": "dep-1", "status": "provisioning", "target": "aws", "plan_id": "plan-1", "project_id": PROJECT["id"],
                "variables": {"deploy_id": deploy_id, "image": image or self.IMAGE, "dockerfile": "sample-back/Dockerfile",
                              "source_sha256": SRC_SHA, "cpu_architecture": arch},
                "terraform_plan_sha256": sha, "source_path": str(self.tmp / "src.zip"), "source_filename": "src.zip",
                "source_sha256": SRC_SHA}

    def statuses(self):
        return [e[1]["status"] for e in self.backend.events]

    def test_success_reports_deploying_then_healthy_with_url(self):
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        last = self.backend.events[-1]
        self.assertEqual(last[0], "dep-1")
        self.assertEqual(last[1]["url"], "http://alb.example:8001")
        self.assertEqual([c.split()[0] for c in self.calls()], ["build", "apply"])
        build = self.calls()[0]
        self.assertIn("--tag fake0001-r1", build)
        self.assertIn("--dockerfile sample-back/Dockerfile", build)
        self.assertIn("--arch X86_64", build)

    def test_deploying_is_reported_before_health_wait_ends_once(self):
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses().count("deploying"), 1)

    def test_changed_source_after_approval_is_rejected_before_build(self):
        job = self.make_job()
        (self.tmp / "src.zip").write_bytes(b"edited after approval")   # 승인 뒤에 소스 파일이 바뀌었다
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.backend.events[0][1]["event_type"], "source_mismatch")
        self.assertEqual(self.calls(), [])   # 빌드도 적용도 하지 않는다

    def test_missing_source_file_or_digest_is_rejected(self):
        cases = {}
        job = self.make_job()
        job["variables"].pop("source_sha256")
        cases["variables에 source_sha256 없음"] = job
        job = self.make_job()
        job["source_sha256"] = "0" * 64   # 백엔드가 가진 소스 지문이 승인된 계획의 것과 다르다
        cases["작업의 source_sha256이 다름"] = job
        job = self.make_job()
        job["source_path"] = str(self.tmp / "missing.zip")
        cases["소스 파일 없음"] = job
        for name, j in cases.items():
            with self.subTest(name=name):
                self.backend.events.clear()
                worker.execute_job(self.api, self.cfg, j)
                self.assertEqual(self.statuses(), ["failed"])
                self.assertEqual(self.backend.events[0][1]["event_type"], "source_mismatch")
        self.assertEqual(self.calls(), [])

    # --- 소스(ZIP) 확보: 같은 PC의 경로 또는 API 다운로드(백엔드가 다른 호스트·S3를 쓰는 서버 모드) ---
    DL_PATH = f"/api/worker/projects/{PROJECT['id']}/source"

    def make_download_job(self, **kw):
        """서버 모드의 작업: 호스트 경로는 없고(S3) 다운로드 경로만 있으며, 이 PC에는 소스 파일이 없다."""
        job = self.make_job(**kw)
        job["source_path"] = None
        job["source_download_path"] = self.DL_PATH
        (self.tmp / "src.zip").unlink()
        self.backend.sources[PROJECT["id"]] = SRC_BYTES
        return job

    def isolate_tempdir(self):
        """내려받은 소스의 임시 폴더가 남는지 보려고 시스템 임시 폴더를 이 시험 전용으로 바꾼다."""
        d = self.tmp / "systmp"
        d.mkdir(exist_ok=True)
        p = mock.patch.object(tempfile, "tempdir", str(d))
        p.start()
        self.addCleanup(p.stop)
        return d

    def watch_build_source(self):
        f = self.tmp / "build_src.txt"
        os.environ["FAKE_BUILD_SRC_LOG"] = worker.posix(f)
        return f

    def assert_failed_before_build(self, event_type):
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.backend.events[0][1]["event_type"], event_type)
        self.assertEqual(self.calls(), [])   # 빌드도 적용도 하지 않는다

    def test_source_is_downloaded_with_token_and_verified_when_there_is_no_host_path(self):
        tmpdir = self.isolate_tempdir()
        seen = self.watch_build_source()
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        src_path, src_sha = seen.read_text(encoding="utf-8").splitlines()
        self.assertEqual(src_sha, SRC_SHA)   # 빌드가 받은 파일은 승인된 소스와 같다
        self.assertNotIn(str(self.tmp / "src.zip"), src_path)
        gets = [h for h in self.backend.headers_seen if h[0].endswith("/source")]
        self.assertEqual(gets, [(self.DL_PATH, TOKEN)])   # worker 전용 경로에 토큰이 붙는다
        self.assertEqual(list(tmpdir.iterdir()), [])      # 내려받은 사용자 소스가 임시 폴더에 남지 않는다

    def test_matching_host_path_is_used_without_downloading(self):
        job = self.make_job()
        job["source_download_path"] = self.DL_PATH
        self.backend.sources[PROJECT["id"]] = SRC_BYTES
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        self.assertEqual(self.backend.source_gets, 0)

    def test_host_path_that_is_not_on_this_pc_falls_back_to_download(self):
        job = self.make_download_job()
        job["source_path"] = "/srv/api-host/uploads/not-on-this-pc.zip"   # 백엔드 호스트의 경로. 이 PC에는 없다
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        self.assertEqual(self.backend.source_gets, 1)

    def test_downloaded_source_with_wrong_digest_is_rejected_before_build(self):
        tmpdir = self.isolate_tempdir()
        job = self.make_download_job()
        self.backend.sources[PROJECT["id"]] = b"someone replaced the zip after approval"
        worker.execute_job(self.api, self.cfg, job)
        self.assert_failed_before_build("source_mismatch")
        self.assertEqual(list(tmpdir.iterdir()), [])

    def test_download_http_error_fails_without_retry_when_it_cannot_get_better(self):
        for code in (401, 403, 404, 409):
            with self.subTest(code=code):
                self.backend.events.clear()
                self.backend.source_gets = 0
                self.backend.source_failures = [code]
                worker.execute_job(self.api, self.cfg, self.make_download_job())
                self.assert_failed_before_build("source_download_failed")
                self.assertEqual(self.backend.source_gets, 1)   # 4xx는 다시 시도해도 같다

    def test_wrong_worker_token_is_not_retried(self):
        job = self.make_download_job()
        worker.execute_job(worker.Api(self.backend.url, "wrong-token", timeout=10), self.cfg, job)
        self.assert_failed_before_build("source_download_failed")
        self.assertEqual(self.backend.source_gets, 1)

    def test_transient_server_errors_are_retried_then_succeed(self):
        self.backend.source_failures = [503, 503]
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        self.assertEqual(self.backend.source_gets, 3)

    def test_persistent_server_errors_stop_after_the_retry_limit(self):
        self.backend.source_failures = [503] * 10
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assert_failed_before_build("source_download_failed")
        self.assertEqual(self.backend.source_gets, worker.DOWNLOAD_RETRIES)

    def test_truncated_response_is_retried_and_never_used(self):
        tmpdir = self.isolate_tempdir()
        seen = self.watch_build_source()
        self.backend.source_truncate = 1   # 첫 응답은 약속한 길이보다 짧게 끊긴다
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        self.assertEqual(self.backend.source_gets, 2)
        self.assertEqual(seen.read_text(encoding="utf-8").splitlines()[1], SRC_SHA)
        self.assertEqual(list(tmpdir.iterdir()), [])

    def test_response_that_is_always_truncated_fails_cleanly(self):
        tmpdir = self.isolate_tempdir()
        self.backend.source_truncate = 99
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assert_failed_before_build("source_download_failed")
        self.assertEqual(self.backend.source_gets, worker.DOWNLOAD_RETRIES)
        self.assertEqual(list(tmpdir.iterdir()), [])

    def test_redirect_is_not_followed_so_the_token_cannot_leak(self):
        other = FakeBackend()
        self.addCleanup(other.close)
        self.backend.source_redirect = other.url + "/steal"
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assert_failed_before_build("source_download_failed")
        self.assertEqual(other.headers_seen, [])   # 리다이렉트 대상에는 요청도 토큰도 가지 않았다

    def test_oversized_source_is_rejected(self):
        tmpdir = self.isolate_tempdir()
        self.cfg.max_source_bytes = len(SRC_BYTES) - 1
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assert_failed_before_build("source_download_failed")
        self.assertEqual(self.backend.source_gets, 1)   # 크기 초과는 다시 시도해도 같다
        self.assertEqual(list(tmpdir.iterdir()), [])

    def test_oversized_source_without_content_length_is_stopped_while_reading(self):
        # 길이를 미리 알리지 않는 응답은 헤더로 거를 수 없으니 읽는 도중 한도를 넘으면 멈춘다(Api.download 직접 호출)
        dest = self.tmp / "dl.bin"
        self.backend.source_no_length = True
        self.backend.sources["p-big"] = b"x" * (3 << 20)
        with self.assertRaises(worker.ApiError) as cm:
            self.api.download("/api/worker/projects/p-big/source", dest, 1 << 20, 30)
        self.assertEqual(cm.exception.status, 413)
        self.assertLessEqual(dest.stat().st_size, 1 << 20)   # 한도를 넘는 만큼은 디스크에 쓰지 않았다

    def test_source_without_content_length_is_accepted_when_within_the_limit(self):
        self.backend.source_no_length = True
        worker.execute_job(self.api, self.cfg, self.make_download_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])

    def test_download_path_must_be_exactly_this_jobs_project_source(self):
        other_project = "22222222-2222-2222-2222-222222222222"
        self.backend.sources[other_project] = SRC_BYTES
        for bad in (f"/api/worker/projects/{other_project}/source", "http://evil.example/source",
                    "/api/worker/projects/../../x/source", f"{self.DL_PATH}?x=1", 123, ""):
            with self.subTest(bad=bad):
                self.backend.events.clear()
                job = self.make_download_job()
                job["source_download_path"] = bad
                worker.execute_job(self.api, self.cfg, job)
                self.assert_failed_before_build("invalid_job")
        self.assertEqual(self.backend.source_gets, 0)   # 어떤 주소로도 요청하지 않았다

    def test_download_only_sends_the_token_to_worker_paths(self):
        with self.assertRaises(worker.ApiError):
            self.api.download("/api/projects/x/source", self.tmp / "dl.bin", 100, 5)
        self.assertEqual(self.backend.headers_seen[-1], ("/api/projects/x/source", None))

    def test_http_api_url_warns_unless_loopback(self):
        with mock.patch.object(worker, "log") as log:
            worker.warn_if_insecure("http://api.example.com")
            self.assertEqual(log.call_count, 1)
            self.assertIn("https", log.call_args[0][0])
        for ok in ("http://127.0.0.1:8000", "http://localhost:8000", "https://api.example.com"):
            with self.subTest(url=ok), mock.patch.object(worker, "log") as log:
                worker.warn_if_insecure(ok)
                log.assert_not_called()

    def test_planned_architecture_is_passed_to_build(self):
        worker.execute_job(self.api, self.cfg, self.make_job(arch="ARM64"))
        self.assertIn("--arch ARM64", self.calls()[0])

    def test_invalid_architecture_in_job_fails_without_building(self):
        worker.execute_job(self.api, self.cfg, self.make_job(arch="MIPS"))
        self.assertEqual(self.backend.events[0][1]["event_type"], "invalid_job")
        self.assertEqual(self.calls(), [])

    def test_plan_hash_mismatch_never_builds_or_applies(self):
        worker.execute_job(self.api, self.cfg, self.make_job(tamper_sha=True))
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.backend.events[0][1]["event_type"], "plan_mismatch")
        self.assertEqual(self.calls(), [])

    def test_missing_plan_file_fails(self):
        job = self.make_job()
        (self.tmp / "deployments" / "fake0001" / "tfplan").unlink()
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.calls(), [])

    def test_invalid_deploy_id_fails_without_running_anything(self):
        for bad in ["../foundation", "UPPER123", "a", ""]:
            with self.subTest(bad=bad):
                self.backend.events.clear()
                worker.execute_job(self.api, self.cfg, self.make_job(deploy_id=bad))
                self.assertEqual(self.statuses(), ["failed"])
        self.assertEqual(self.calls(), [])

    def test_build_failure_reports_failed_with_redacted_log_and_does_not_apply(self):
        os.environ["FAKE_BUILD_RC"] = "1"
        job = self.make_job()
        os.environ["FAKE_BUILD_RC"] = "1"
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["failed"])
        ev = self.backend.events[0][1]
        self.assertEqual(ev["event_type"], "build_failed")
        self.assertNotIn("hunter2", json.dumps(ev))
        self.assertEqual([c.split()[0] for c in self.calls()], ["build"])

    def test_built_image_different_from_plan_is_rejected_before_apply(self):
        job = self.make_job()
        os.environ["FAKE_BUILD_IMAGE"] = "other.registry/apps:evil"
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.backend.events[0][1]["event_type"], "image_mismatch")
        self.assertNotIn("apply", [c.split()[0] for c in self.calls()])

    def test_apply_failure_reports_failed_with_diagnose_and_no_auto_rollback(self):
        os.environ["FAKE_APPLY_RC"] = "1"
        job = self.make_job()
        os.environ["FAKE_APPLY_RC"] = "1"
        worker.execute_job(self.api, self.cfg, job)
        self.assertEqual(self.statuses(), ["deploying", "failed"])
        ev = self.backend.events[-1][1]
        self.assertEqual(ev["event_type"], "apply_failed")
        self.assertEqual(ev["details"]["diagnose"]["deployId"], "fake0001")
        self.assertIn("헬스체크 실패", ev["details"]["log_tail"])
        self.assertFalse(any(c.startswith("rollback") for c in self.calls()))   # 새 계획의 사용자 승인 없이 자동 롤백하지 않는다

    def test_missing_outputs_url_fails(self):
        job = self.make_job()
        orig = self.cfg.deployments_dir
        # apply는 성공하지만 outputs.json을 만들지 못하게 한다: 디렉터리로 막는다
        (orig / "fake0001" / "outputs.json").mkdir()
        worker.execute_job(self.api, self.cfg, job)
        self.assertIn("failed", self.statuses())
        self.assertNotIn("healthy", self.statuses())

    def test_transient_backend_errors_are_retried_so_results_are_not_lost(self):
        self.backend.event_failures = 2   # 첫 보고가 두 번 503이어도 재시도로 들어간다
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])

    def test_failed_deploying_report_is_resent_before_healthy(self):
        # deploying 보고가 재시도까지 모두 실패해도(4번) 읽기를 멈추지 않고, 끝난 뒤 deploying을 다시 보내고 healthy를 보낸다
        self.backend.event_failures = worker.REPORT_RETRIES
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses(), ["deploying", "healthy"])
        self.assertEqual(self.backend.events[-1][1]["url"], "http://alb.example:8001")

    def test_report_gives_up_on_client_errors_without_retry(self):
        class Always4xx:
            calls = 0

            def post(self, path, body=None):
                Always4xx.calls += 1
                raise worker.ApiError(409, "상태 전이 불가")
        with self.assertRaises(worker.ApiError):
            worker.report(Always4xx(), "dep-1", "healthy", "healthy", "m", url="http://x")
        self.assertEqual(Always4xx.calls, 1)

    def test_timeout_kills_child_processes_and_reports_failure(self):
        os.environ["FAKE_HANG"] = "1"
        self.cfg.apply_timeout = 2
        worker.execute_job(self.api, self.cfg, self.make_job())
        self.assertEqual(self.statuses()[-1], "failed")
        ev = self.backend.events[-1][1]
        self.assertEqual(ev["event_type"], "apply_failed")
        self.assertIn("제한 시간", ev["details"]["log_tail"])
        hb = self.tmp / "heartbeat"
        self.assertTrue(hb.exists())
        first = hb.read_text()
        time.sleep(1.2)
        self.assertEqual(hb.read_text(), first, "시간 초과 뒤에도 하위 프로세스가 계속 돌고 있다")

    def test_run_capture_timeout_raises_and_kills(self):
        t0 = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            worker.run_capture([self.cfg.bash, "-c", "sleep 30"], 1)
        self.assertLess(time.time() - t0, 15)

    def test_token_is_not_passed_to_child_processes(self):
        env = worker.child_env(self.cfg)
        self.assertNotIn("WORKER_API_TOKEN", env)


class LoopTests(Base):
    def setUp(self):
        super().setUp()
        self.state = worker.State(retry_after=0.0)
        self.backend.projects = [PROJECT]

    def test_projects_on_later_pages_are_listed_and_planned(self):
        old = [{"id": f"old-{i}", "name": f"o{i}"} for i in range(4)]
        self.backend.pages = [old[:2], old[2:], [PROJECT]]
        self.assertEqual(len(worker.list_projects(self.api)), 5)
        for o in old:
            self.backend.plans[o["id"]] = [{"id": "p", "status": "approved", "terraform_plan_ready": True}]   # 오래된 프로젝트는 이미 계획이 있다
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)
        self.assertEqual(self.backend.created_plans[0]["project_id"], PROJECT["id"])

    def test_project_without_analysis_waits(self):
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertEqual(self.state.failures, {})
        self.assertEqual(self.calls(), [])

    def test_project_with_existing_plan_is_skipped_and_remembered(self):
        self.backend.plans[PROJECT["id"]] = [{"id": "p", "status": "awaiting_approval", "terraform_plan_ready": True}]
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.created_plans, [])
        self.assertIn(PROJECT["id"], self.state.done)

    def test_active_or_finished_plans_are_never_replanned(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        for status in ["awaiting_approval", "approved", "consumed"]:
            with self.subTest(status=status):
                self.state.done.clear()
                self.backend.plans[PROJECT["id"]] = [{"id": "p", "status": status, "terraform_plan_ready": True}]
                worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.created_plans, [])

    def test_project_whose_plans_are_all_superseded_is_replanned(self):
        self.backend.plans[PROJECT["id"]] = [{"id": "old1", "status": "superseded"}, {"id": "old2", "status": "superseded"}]
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)   # 거절·대체된 계획만 남은 프로젝트에 새 계획을 만든다

    def test_plan_that_becomes_superseded_in_the_same_process_is_replanned(self):
        # 활성 계획이 있어 done에 들어간 프로젝트도 일정 간격마다 상태를 다시 조회해, 계획이 superseded로 바뀌면 다시 계획한다
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        self.backend.plans[PROJECT["id"]] = [{"id": "p1", "status": "awaiting_approval", "terraform_plan_ready": True}]
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertIn(PROJECT["id"], self.state.done)
        self.assertEqual(self.backend.created_plans, [])
        self.backend.plans[PROJECT["id"]] = [{"id": "p1", "status": "superseded"}]
        worker.plan_pending(self.api, self.cfg, self.state)   # 재조회 간격 안: 아직 조회하지 않는다
        self.assertEqual(self.backend.created_plans, [])
        self.state.recheck_after = 0.0                        # 간격이 지났다고 본다
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_failed_plan_upload_is_repaired_on_the_next_check(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        self.backend.upload_failures = worker.REPORT_RETRIES
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(len(self.backend.created_plans), 1)
        self.assertEqual(self.backend.uploads, [])
        self.assertIn(PROJECT["id"], self.state.repair)
        worker.plan_pending(self.api, self.cfg, self.state)   # 백엔드가 회복된 뒤
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertNotIn(PROJECT["id"], self.state.repair)
        self.assertEqual(len(self.backend.created_plans), 1)   # 새 계획을 또 만들지 않는다

    def test_repair_does_not_upload_a_local_file_that_differs_from_the_registered_plan(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        self.backend.upload_failures = worker.REPORT_RETRIES
        worker.plan_pending(self.api, self.cfg, self.state)
        (self.tmp / "deployments" / "fake0001" / "tfplan").write_bytes(b"tampered")   # 등록된 SHA-256과 달라진다
        worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.backend.uploads, [])

    def test_unexpected_error_in_one_project_does_not_stop_the_other_projects(self):
        # 한 프로젝트의 예상 못 한 예외(예: 잘못된 분석 결과의 TypeError)가 worker 점검 전체를 중단하면 안 된다
        bad = {"id": "bad-1", "name": "bad", "source_sha256": SRC_SHA}
        self.backend.projects = [bad, PROJECT]
        self.backend.analyses["bad-1"] = {"id": "a1", "result": GOOD_RESULT}
        self.backend.analyses[PROJECT["id"]] = {"id": "a2", "result": GOOD_RESULT}
        orig = worker.plan_project

        def flaky(api, cfg, project, *a, **k):
            if project["id"] == "bad-1":
                raise TypeError("unhashable type: 'list'")
            return orig(api, cfg, project, *a, **k)
        worker.plan_project = flaky
        self.addCleanup(setattr, worker, "plan_project", orig)
        worker.plan_pending(self.api, self.cfg, self.state)   # 예외가 밖으로 나오지 않는다
        self.assertEqual([p["project_id"] for p in self.backend.created_plans], [PROJECT["id"]])   # 다른 프로젝트는 계속 계획된다
        self.assertEqual(self.state.failures["bad-1"][0], 1)

    def test_plan_created_once_for_analyzed_project(self):
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        worker.plan_pending(self.api, self.cfg, self.state)
        worker.plan_pending(self.api, self.cfg, self.state)   # 두 번째 점검은 이미 끝난 프로젝트를 건너뛴다
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_failures_are_counted_and_stop_after_max_tries(self):
        bad = json.loads(json.dumps(GOOD_RESULT))
        bad["app_config"]["container_port"] = 0
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": bad}
        for _ in range(5):
            worker.plan_pending(self.api, self.cfg, self.state)
        self.assertEqual(self.state.failures[PROJECT["id"]][0], 3)
        self.assertEqual(self.backend.created_plans, [])

    def test_tick_runs_approved_job_then_plans(self):
        ex = ExecutorTests("test_success_reports_deploying_then_healthy_with_url")
        ex.tmp, ex.cfg = self.tmp, self.cfg
        job = ExecutorTests.make_job(ex)
        self.backend.claims.append(job)
        self.backend.analyses[PROJECT["id"]] = {"id": "a", "result": GOOD_RESULT}
        ran = worker.tick(self.api, self.cfg, self.state)
        self.assertTrue(ran)
        self.assertEqual([e[1]["status"] for e in self.backend.events], ["deploying", "healthy"])
        self.assertEqual(len(self.backend.created_plans), 1)

    def test_tick_without_job_returns_false(self):
        self.assertFalse(worker.tick(self.api, self.cfg, self.state))

    def test_main_requires_token(self):
        os.environ.pop("WORKER_API_TOKEN", None)
        self.assertEqual(worker.main(["--once"]), 2)


CID = "22222222-2222-2222-2222-222222222222"
ROLE_ARN = "arn:aws:iam::123456789012:role/PavedCloudsReadOnlyRole"
EXT_ID = "pc-" + "ab" * 16


def pending_conn(**over):
    """GET /api/worker/connections/pending 의 항목(back/app/schemas.py의 PendingConnectionOut)."""
    conn = {"id": CID, "provider": "aws", "name": "내 계정", "external_id": EXT_ID, "fields": {},
            "created_at": "2026-10-10T00:00:00Z", "account_id": "123456789012", "role_arn": ROLE_ARN}
    conn.update(over)
    return conn


class ConnectionTests(Base):
    """사용자 AWS 연결 확인(back/API.md의 worker 연결 경로). 실제 AWS는 부르지 않고 aws CLI는 가짜 스크립트로 대신한다."""

    def setUp(self):
        super().setUp()   # 가짜 aws CLI(self.cfg.aws_cmd, self.aws_calls())는 Base가 만든다
        self.state = worker.State(conn_retry_after=0.0)
        self.backend.connections = [pending_conn()]

    def posts(self):
        return [(p, b) for p, b, _ in self.backend.conn_posts]

    def check(self):
        worker.verify_connections(self.api, self.cfg, self.state)

    def test_connection_waits_until_the_callback_brings_the_role(self):
        self.backend.connections = [pending_conn(account_id=None, role_arn=None)]
        self.check()
        self.assertEqual(self.aws_calls(), [])
        self.assertEqual(self.posts(), [])

    def test_assumable_role_is_completed_with_account_and_role(self):
        self.check()
        calls = self.aws_calls()
        self.assertEqual(len(calls), 1)
        for part in (f"sts assume-role --role-arn {ROLE_ARN}", f"--external-id {EXT_ID}", "--role-session-name paved-clouds-verify"):
            self.assertIn(part, calls[0])
        # 임시 자격 증명은 출력하지 않는다: 역할 ARN만 읽는다
        self.assertIn("--query AssumedRoleUser.Arn --output text", calls[0])
        self.assertEqual(self.posts(), [(f"/api/worker/connections/{CID}/complete", {"account_id": "123456789012", "role_arn": ROLE_ARN})])
        self.assertEqual(self.backend.conn_posts[0][2], TOKEN)   # worker 경로에는 토큰을 붙인다
        self.assertNotIn(EXT_ID, json.dumps(self.backend.conn_posts))   # ExternalId는 백엔드로 되돌려 보내지 않는다

    def test_role_assumed_in_another_account_is_never_completed(self):
        os.environ["FAKE_AWS_ARN"] = "arn:aws:sts::999999999999:assumed-role/PavedCloudsReadOnlyRole/paved-clouds-verify"
        self.state.conn_max_tries = 2
        self.check()
        self.assertEqual(self.posts(), [])
        self.check()
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{CID}/fail"])

    def test_denied_role_is_retried_then_fails_with_a_message_that_leaks_nothing(self):
        os.environ["FAKE_AWS_MODE"] = "denied"
        self.state.assume_proven = True   # worker 권한이 증명된 뒤라면 AccessDenied는 사용자 쪽 거부다(증명 전의 동작은 아래 시험)
        self.state.conn_max_tries = 3
        self.check()
        self.check()
        self.assertEqual(self.posts(), [])   # 방금 만든 역할은 IAM 전파 전에 잠깐 거부될 수 있어 바로 실패로 만들지 않는다
        self.assertEqual(self.state.conn_tries[CID][0], 2)
        self.check()
        self.assertEqual(len(self.aws_calls()), 3)
        (path, body), = self.posts()
        self.assertEqual(path, f"/api/worker/connections/{CID}/fail")
        self.assertIn("AccessDenied", body["error"])
        for secret in (EXT_ID, "999999999999", "operator"):   # ExternalId와 운영자 계정의 IAM 주체 이름은 사용자에게 보이는 오류에 넣지 않는다
            self.assertNotIn(secret, body["error"])
        self.assertTrue(0 < len(body["error"]) <= 2000)
        self.assertNotIn(CID, self.state.conn_tries)   # 보고한 뒤에는 기록을 비운다

    def test_retries_wait_between_attempts(self):
        os.environ["FAKE_AWS_MODE"] = "denied"
        self.state.conn_retry_after = 3600.0
        self.check()
        self.check()
        self.assertEqual(len(self.aws_calls()), 1)

    def test_worker_side_errors_never_fail_the_connection(self):
        # 만료된 운영자 자격 증명은 사용자의 잘못이 아니다. 횟수를 올리지 않으니 몇 번을 시도해도 실패로 끝나지 않는다
        os.environ["FAKE_AWS_MODE"] = "expired"
        for _ in range(self.state.conn_max_tries + 2):
            self.check()
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.state.conn_tries[CID][0], 0)
        os.environ["FAKE_AWS_MODE"] = "ok"   # 자격 증명이 복구되면 이어서 완료된다
        self.check()
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{CID}/complete"])

    def test_assume_role_timeout_is_a_worker_side_error_and_kills_the_child(self):
        old = worker.ASSUME_ROLE_TIMEOUT
        worker.ASSUME_ROLE_TIMEOUT = 1
        self.addCleanup(setattr, worker, "ASSUME_ROLE_TIMEOUT", old)
        os.environ["FAKE_AWS_MODE"] = "hang"
        result, why = worker.check_role(self.cfg, os.environ.copy(), ROLE_ARN, EXT_ID, "123456789012")
        self.assertEqual(result, "error")
        self.assertIn("1초", why)

    def test_complete_rejected_with_4xx_is_not_retried(self):
        self.backend.conn_post_code = 409   # 저장된 콜백 값과 다르다 등
        self.check()
        self.check()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(len(self.aws_calls()), 1)
        self.assertIn(CID, self.state.conn_blocked)

    def test_transient_complete_error_is_reported_again_on_the_next_check(self):
        self.backend.conn_post_failures = worker.REPORT_RETRIES   # 이번 점검의 재시도를 모두 소진한다
        self.check()
        self.assertEqual(self.posts(), [])
        self.assertNotIn(CID, self.state.conn_blocked)
        self.check()
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{CID}/complete"])

    def test_malformed_role_information_is_skipped_without_calling_aws(self):
        bad = [pending_conn(role_arn="not-an-arn"),
               pending_conn(account_id="999999999999"),                       # ARN의 계정과 다르다
               pending_conn(external_id="pc-short"),
               pending_conn(role_arn=ROLE_ARN + "\n"),                         # 끝의 개행
               pending_conn(role_arn=None), pending_conn(account_id=None)]    # 한쪽만 비어 있다
        for conn in bad:
            with self.subTest(conn={k: conn[k] for k in ("account_id", "role_arn", "external_id")}):
                self.state = worker.State(conn_retry_after=0.0)   # 한 번 건너뛴 연결은 막아 두므로 경우마다 새로 시작한다
                self.backend.connections = [conn]
                self.check()
                self.assertIn(CID, self.state.conn_blocked)
        self.assertEqual(self.aws_calls(), [])
        self.assertEqual(self.posts(), [])

    def test_missing_connection_api_does_not_stop_job_claims_or_planning(self):
        self.backend.pending_status = 404   # 연결 API가 없는 백엔드
        ex = ExecutorTests("test_success_reports_deploying_then_healthy_with_url")
        ex.tmp, ex.cfg = self.tmp, self.cfg
        self.backend.claims.append(ExecutorTests.make_job(ex))
        self.assertTrue(worker.tick(self.api, self.cfg, self.state))
        self.assertEqual([e[1]["status"] for e in self.backend.events], ["deploying", "healthy"])
        self.assertTrue(self.state.conn_warned)

    def test_deployments_are_claimed_and_run_before_connections_are_checked(self):
        # 연결 확인은 호환용이라 승인된 배포를 기다리게 하면 안 된다
        ex = ExecutorTests("test_success_reports_deploying_then_healthy_with_url")
        ex.tmp, ex.cfg = self.tmp, self.cfg
        self.backend.claims.append(ExecutorTests.make_job(ex))
        self.assertTrue(worker.tick(self.api, self.cfg, self.state))
        order = self.backend.order
        self.assertEqual(order[0], "claim")
        self.assertGreater(order.index("pending"), max(i for i, o in enumerate(order) if o == "event"))
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{CID}/complete"])

    def test_idle_tick_still_checks_connections(self):
        self.assertFalse(worker.tick(self.api, self.cfg, self.state))
        self.assertEqual(self.backend.order[:2], ["claim", "pending"])
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{CID}/complete"])

    def test_access_denied_is_not_a_user_failure_until_assume_role_has_succeeded(self):
        # AccessDenied는 사용자 스택의 문제일 수도, worker 주체의 sts:AssumeRole 권한 누락일 수도 있다. 권한이 증명되기 전에는 사용자 연결을 실패로 확정하지 않는다
        os.environ["FAKE_AWS_MODE"] = "denied"
        self.state.conn_max_tries = 2
        for _ in range(6):
            self.check()
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.state.conn_tries[CID][0], 0)   # 횟수도 올리지 않는다
        self.assertIn(CID, self.state.conn_warned_ids)       # 대신 운영자가 볼 로그를 남긴다(연결마다 한 번)
        self.assertFalse(self.state.assume_proven)
        # 다른 연결의 AssumeRole이 성공하면 worker 권한이 증명된다
        other = "33333333-3333-3333-3333-333333333333"
        self.backend.connections = [pending_conn(id=other)]
        os.environ["FAKE_AWS_MODE"] = "ok"
        os.environ["FAKE_AWS_ARN"] = "arn:aws:sts::123456789012:assumed-role/PavedCloudsReadOnlyRole/paved-clouds-verify"
        self.check()
        self.assertTrue(self.state.assume_proven)
        # 이제 같은 AccessDenied는 사용자 쪽 거부라 횟수를 세고, 이어지면 실패로 보고한다
        self.backend.connections = [pending_conn()]
        os.environ["FAKE_AWS_MODE"] = "denied"
        self.check()
        self.assertEqual(self.state.conn_tries[CID][0], 1)
        self.check()
        self.assertEqual([p for p, _ in self.posts()][-1], f"/api/worker/connections/{CID}/fail")

    def test_validation_errors_are_role_side_even_before_permission_is_proven(self):
        os.environ["FAKE_AWS_MODE"] = "invalid"
        self.state.conn_max_tries = 2
        self.check()
        self.check()
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{CID}/fail"])

    def test_worker_side_error_retries_also_wait_between_attempts(self):
        os.environ["FAKE_AWS_MODE"] = "expired"
        self.state.conn_retry_after = 3600.0
        self.check()
        self.check()
        self.assertEqual(len(self.aws_calls()), 1)   # 횟수가 0이어도 매 점검마다 AWS를 부르지 않는다

    def test_connection_checks_have_a_time_budget_and_take_turns(self):
        # AWS CLI가 멈춰도(연결마다 제한 시간만큼 걸림) 한 점검이 연결 수만큼 길어지면 안 된다
        old_timeout = worker.ASSUME_ROLE_TIMEOUT
        worker.ASSUME_ROLE_TIMEOUT = 1
        self.addCleanup(setattr, worker, "ASSUME_ROLE_TIMEOUT", old_timeout)
        os.environ["FAKE_AWS_MODE"] = "hang"
        self.state.conn_budget = 1.0
        self.state.conn_retry_after = 3600.0   # 멈춘 연결은 한동안 건너뛰어 다음 연결에 차례를 준다
        ids = [f"4444444{i}-4444-4444-4444-444444444444" for i in range(4)]
        self.backend.connections = [pending_conn(id=i, external_id="pc-" + str(n) * 32) for n, i in enumerate(ids)]
        t0 = time.monotonic()
        self.check()
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 4.0)   # 4개를 모두 기다렸다면 4초 이상이다
        first = len(self.aws_calls())
        self.assertTrue(1 <= first <= 2, first)
        self.check()
        self.check()
        seen = {line.split("--external-id ")[1].split()[0] for line in self.aws_calls()}
        self.assertGreater(len(seen), first)   # 다음 점검에서는 앞서 멈춘 연결을 건너뛰고 다른 연결을 확인한다

    def test_one_failing_connection_does_not_stop_the_others(self):
        other_id = "33333333-3333-3333-3333-333333333333"
        self.backend.connections = [pending_conn(), pending_conn(id=other_id)]
        orig, seen = worker.check_role, []

        def flaky(*a):
            seen.append(a)
            if len(seen) == 1:
                raise TypeError("예상 못 한 오류")
            return orig(*a)
        worker.check_role = flaky
        self.addCleanup(setattr, worker, "check_role", orig)
        self.check()   # 예외가 밖으로 나오지 않는다
        self.assertEqual([p for p, _ in self.posts()], [f"/api/worker/connections/{other_id}/complete"])

    def test_records_of_connections_that_left_the_pending_list_are_dropped(self):
        os.environ["FAKE_AWS_MODE"] = "denied"
        self.check()
        self.state.conn_blocked.add(CID)
        self.assertIn(CID, self.state.conn_tries)
        self.backend.connections = []   # 완료·삭제돼 목록에서 사라졌다
        self.check()
        self.assertEqual((self.state.conn_tries, self.state.conn_blocked), ({}, set()))

    def test_input_rules_match_the_backend_schema(self):
        schemas = Path(__file__).resolve().parents[2] / "back" / "app" / "schemas.py"
        if not schemas.is_file():
            self.skipTest("back/app/schemas.py 없음")
        src = schemas.read_text(encoding="utf-8")
        role = re.search(r'pattern=r"(\^arn:\(aws[^"]+)"', src).group(1)
        samples = [ROLE_ARN, "arn:aws-cn:iam::123456789012:role/path/Name", "arn:aws:iam::12345:role/x", "arn:aws:iam::123456789012:user/x",
                   "arn:aws:iam::123456789012:role/", "arn:aws:iam::123456789012:role/a b", "arn:gcp:iam::123456789012:role/x"]
        for s in samples:
            with self.subTest(arn=s):
                self.assertEqual(bool(re.fullmatch(role, s)), bool(worker.ROLE_ARN_RE.match(s)))
        self.assertIn('external_id = f"pc-{os.urandom(16).hex()}"', (schemas.parent / "main.py").read_text(encoding="utf-8"))
        self.assertTrue(worker.EXTERNAL_ID_RE.match("pc-" + "0f" * 16))
        self.assertIn("min_length=35, max_length=35", src)   # pc- + 32자


TEMPLATE = Path(__file__).resolve().parents[1] / "connection" / "paved-clouds-connection.yaml"


class _CallbackServer:
    """CloudFormation 콜백을 받는 백엔드의 role-callback 경로 흉내."""

    def __init__(self, code=200):
        self.requests, self.code = [], code
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append((self.path, json.loads(self.rfile.read(n))))
                self.send_response(outer.code)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/connections/{CID}/role-callback"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TemplateTests(unittest.TestCase):
    """infra/connection/paved-clouds-connection.yaml. 실제 CloudFormation 스택은 만들지 않는다."""

    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def parsed(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML 없음(표준 라이브러리만으로는 YAML을 읽을 수 없다)")

        def tag(loader, suffix, node):   # !Ref, !Sub 같은 CloudFormation 약식 함수
            if isinstance(node, yaml.ScalarNode):
                value = loader.construct_scalar(node)
            elif isinstance(node, yaml.SequenceNode):
                value = loader.construct_sequence(node, deep=True)
            else:
                value = loader.construct_mapping(node, deep=True)
            return {suffix: value}

        class Loader(yaml.SafeLoader):
            pass
        Loader.add_multi_constructor("!", tag)
        return yaml.load(self.text, Loader=Loader)

    def test_role_trusts_only_the_operator_account_with_the_external_id(self):
        t = self.parsed()
        role = t["Resources"]["PavedCloudsRole"]["Properties"]
        self.assertEqual(role["RoleName"], "PavedCloudsReadOnlyRole")   # back/API.md의 complete 예시와 같은 이름
        (stmt,) = role["AssumeRolePolicyDocument"]["Statement"]
        self.assertEqual((stmt["Effect"], stmt["Action"]), ("Allow", "sts:AssumeRole"))
        self.assertEqual(stmt["Principal"], {"AWS": {"Sub": "arn:${AWS::Partition}:iam::${PlatformAccountId}:root"}})
        self.assertEqual(stmt["Condition"], {"StringEquals": {"sts:ExternalId": {"Ref": "ExternalId"}}})
        self.assertEqual(role["ManagedPolicyArns"], [{"Sub": "arn:${AWS::Partition}:iam::aws:policy/ReadOnlyAccess"}])
        self.assertNotIn("AdministratorAccess", self.text)

    def test_parameters_are_the_ones_the_backend_link_fills_in(self):
        t = self.parsed()
        self.assertEqual(set(t["Parameters"]), {"ExternalId", "PlatformAccountId", "RoleCallbackUrl"})
        main = Path(__file__).resolve().parents[2] / "back" / "app" / "main.py"
        if main.is_file():
            src = main.read_text(encoding="utf-8")
            for name in t["Parameters"]:
                self.assertIn(f"param_{name}", src)   # 링크에 param_<이름>으로 실린다
        self.assertRegex("pc-" + "0f" * 16, t["Parameters"]["ExternalId"]["AllowedPattern"])
        self.assertTrue(worker.EXTERNAL_ID_RE.match("pc-" + "0f" * 16))
        self.assertIsNone(re.match(t["Parameters"]["ExternalId"]["AllowedPattern"], "pc-short"))
        self.assertIsNone(re.match(t["Parameters"]["RoleCallbackUrl"]["AllowedPattern"], "http://insecure.example/cb"))

    def test_every_reference_points_to_a_declared_parameter_or_resource(self):
        t = self.parsed()
        known = set(t["Parameters"]) | set(t["Resources"])
        refs = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "Ref":
                        refs.append(v)
                    elif k == "GetAtt":
                        refs.append(v.split(".")[0] if isinstance(v, str) else v[0])
                    elif k == "Sub" and isinstance(v, str):
                        refs.extend(r.split(".")[0] for r in re.findall(r"\$\{([^}]+)\}", v))
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(t["Resources"])
        walk(t["Outputs"])
        self.assertTrue(refs)
        self.assertEqual([r for r in refs if r not in known and not r.startswith("AWS::")], [])
        # 콜백 관련 리소스는 모두 같은 조건 아래에 있다(콜백 주소가 없으면 만들지 않는다)
        self.assertEqual({n for n, r in t["Resources"].items() if r.get("Condition") == "HasCallback"},
                         {"CallbackFunctionRole", "CallbackFunction", "RoleCallback"})

    def lambda_namespace(self, server_url, calls):
        code = self.parsed()["Resources"]["CallbackFunction"]["Properties"]["Code"]["ZipFile"]
        fake = type(sys)("cfnresponse")
        fake.SUCCESS, fake.FAILED = "SUCCESS", "FAILED"
        fake.send = lambda event, context, status, data, physical_id=None, **kw: calls.append((status, data, physical_id, kw))
        sys.modules["cfnresponse"] = fake
        self.addCleanup(sys.modules.pop, "cfnresponse", None)
        ns = {}
        exec(compile(code, "index.py", "exec"), ns)
        event = {"RequestType": "Create", "ResourceProperties": {
            "CallbackUrl": server_url, "ExternalId": EXT_ID, "AccountId": "123456789012", "RoleArn": ROLE_ARN}}
        return ns["handler"], event

    def test_callback_function_posts_the_backend_contract(self):
        server = _CallbackServer()
        self.addCleanup(server.close)
        calls = []
        handler, event = self.lambda_namespace(server.url, calls)
        handler(event, None)
        self.assertEqual(server.requests, [(f"/api/connections/{CID}/role-callback",
                                            {"external_id": EXT_ID, "account_id": "123456789012", "role_arn": ROLE_ARN})])
        self.assertEqual([c[0] for c in calls], ["SUCCESS"])
        self.assertEqual(calls[0][2], "paved-clouds-role-callback")   # 갱신 때 물리 ID가 바뀌어 교체로 오인되지 않게 고정한다

    def test_callback_function_does_not_retry_client_errors_and_fails_the_stack(self):
        server = _CallbackServer(code=409)   # 다른 역할이 이미 보고된 연결
        self.addCleanup(server.close)
        calls = []
        handler, event = self.lambda_namespace(server.url, calls)
        handler(event, None)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(calls[0][0], "FAILED")
        self.assertIn("HTTP 409", calls[0][3]["reason"])
        self.assertNotIn(EXT_ID, calls[0][3]["reason"])

    def test_callback_function_retries_server_errors_then_gives_up(self):
        server = _CallbackServer(code=503)
        self.addCleanup(server.close)
        calls = []
        handler, event = self.lambda_namespace(server.url, calls)
        with mock.patch("time.sleep"):
            handler(event, None)
        self.assertEqual(len(server.requests), 4)
        self.assertEqual(calls[0][0], "FAILED")

    def test_callback_function_answers_delete_without_calling_the_backend(self):
        server = _CallbackServer()
        self.addCleanup(server.close)
        calls = []
        handler, event = self.lambda_namespace(server.url, calls)
        handler({**event, "RequestType": "Delete"}, None)   # 응답이 없으면 스택 삭제가 한 시간 멈춘다
        self.assertEqual(server.requests, [])
        self.assertEqual([c[0] for c in calls], ["SUCCESS"])


class PathTests(unittest.TestCase):
    def test_posix_converts_windows_drive_paths_only_on_windows(self):
        got = worker.posix("C:/Users/x/a.zip" if os.name == "nt" else "/tmp/a.zip")
        self.assertEqual(got, "/c/Users/x/a.zip" if os.name == "nt" else "/tmp/a.zip")


if __name__ == "__main__":
    unittest.main(verbosity=1)
