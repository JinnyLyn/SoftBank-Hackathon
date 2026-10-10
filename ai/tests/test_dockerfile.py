"""Dockerfile 초안 검사. 초안은 규칙 템플릿이라 내용을 그대로 비교할 수 있다.
실제 빌드·실행 확인은 Docker가 필요해 일반 테스트에서 뺐다(PR 본문 검증 절에 기록)."""

import json
import unittest

from paved_ai.analysis import analyze_files
from paved_ai.masking import backend_unsafe_paths
from paved_ai.source import SourceFile


def files(**kv):
    return [SourceFile(path, text) for path, text in kv.items()]


def draft_of(spec):
    result = analyze_files(files(**spec)).to_result()
    return result, result["dockerfile_draft"]


class DraftTests(unittest.TestCase):
    def test_flask_uses_gunicorn_on_the_found_port(self):
        result, d = draft_of({
            "requirements.txt": "flask==3.0.0\n",
            "app.py": "from flask import Flask\napp = Flask(__name__)\n@app.route('/health')\ndef h():\n    return 'ok'\napp.run(port=5050)\n",
        })
        self.assertEqual(d["path"], "Dockerfile")
        self.assertEqual(d["port"], 5050)
        self.assertIn("FROM python:3.12-slim", d["content"])
        self.assertIn("RUN pip install --no-cache-dir gunicorn", d["content"], "의존성에 없으면 실행 서버를 설치")
        self.assertIn('CMD ["gunicorn", "-b", "0.0.0.0:5050", "app:app"]', d["content"])
        self.assertIn("app.py:2 app = Flask(__name__)", d["based_on"])
        self.assertIn("초안", result["unresolved"]["dockerfile"], "배포는 여전히 저장소에 Dockerfile이 있어야 함")
        self.assertEqual(backend_unsafe_paths(result), [])

    def test_fastapi_module_path_and_runtime_version(self):
        _, d = draft_of({
            "requirements.txt": "fastapi\nuvicorn\n",
            ".python-version": "3.11\n",
            "app/main.py": "from fastapi import FastAPI\napi = FastAPI()\n@api.get('/health')\ndef h():\n    return {}\n",
        })
        self.assertIn("FROM python:3.11-slim", d["content"])
        self.assertNotIn("pip install --no-cache-dir uvicorn", d["content"], "이미 의존성에 있으면 더 설치하지 않음")
        self.assertIn('CMD ["uvicorn", "app.main:api", "--host", "0.0.0.0", "--port", "8000"]', d["content"])

    def test_python_requirement_dependencies_exist_before_install(self):
        # pip는 requirements의 재귀 include·constraint·로컬 패키지를 설치 시점에 읽는다.
        for requirement in ("-r requirements/prod.txt", "-c constraints.txt", "-e ./local-package"):
            with self.subTest(requirement=requirement):
                _, draft = draft_of({
                    "requirements.txt": f"flask\n{requirement}\n",
                    "requirements/prod.txt": "-r base.txt\n",
                    "requirements/base.txt": "gunicorn\n",
                    "constraints.txt": "flask==3.0.0\n",
                    "local-package/setup.py": "from setuptools import setup\nsetup(name='local-package', version='1')\n",
                    "app.py": "from flask import Flask\napp = Flask(__name__)\n",
                })
                available = set()
                for line in draft["content"].splitlines():
                    if line == "COPY . .":
                        available.update(("requirements/prod.txt", "requirements/base.txt", "constraints.txt", "local-package/setup.py"))
                    if line.startswith("RUN pip install"):
                        self.assertTrue({"requirements/prod.txt", "requirements/base.txt", "constraints.txt", "local-package/setup.py"} <= available,
                                        "pip 실행 전에 중첩 의존성 파일과 로컬 패키지가 있어야 함")
                        break

    def test_node_start_script_lockfile_and_build(self):
        _, d = draft_of({
            "package.json": json.dumps({"dependencies": {"express": "^4"}, "scripts": {"start": "node server.js", "build": "tsc"}}),
            "package-lock.json": "{}",
            "server.js": "app.listen(process.env.PORT || 3000)\n",
        })
        # NODE_ENV=production 이면 npm이 개발 의존성을 빼므로 명시 (PR #29 리뷰)
        self.assertIn("RUN npm ci --include=dev\n", d["content"], "빌드가 있으면 개발 의존성도 설치")
        self.assertIn("RUN npm run build", d["content"])
        self.assertIn("ENV PORT=3000", d["content"])
        self.assertIn('CMD ["npm", "start"]', d["content"])

    def test_package_manager_from_lockfile(self):
        # pnpm 워크스페이스(모노레포): 하위 package.json 이 모두 있어야 설치되므로 전체를 먼저 복사, corepack 으로 버전 고정
        base = {"package.json": json.dumps({"name": "root", "private": True, "scripts": {"build": "x", "start": "y"}}),
                "apps/site/package.json": json.dumps({"name": "site", "private": True, "dependencies": {"next": "^15"}}),
                "apps/site/app/page.tsx": "export default function Page() { return null }\n"}
        _, d = draft_of({**base, "pnpm-lock.yaml": "lockfileVersion: '9.0'\n"})
        self.assertIn("RUN corepack enable && pnpm install --frozen-lockfile\n", d["content"])
        self.assertIn("RUN pnpm run build", d["content"])
        self.assertIn('CMD ["pnpm", "start"]', d["content"])
        self.assertLess(d["content"].index("COPY . ."), d["content"].index("pnpm install"))
        _, d = draft_of({**base, "yarn.lock": "# yarn lockfile v1\n"})
        self.assertIn("corepack enable && yarn install", d["content"])
        self.assertIn('CMD ["yarn", "start"]', d["content"])

    def test_malformed_node_scripts_reports_reason_without_crashing(self):
        for scripts in ("node server.js", ["node server.js"], 1, True, None):
            with self.subTest(scripts=scripts):
                result, draft = draft_of({
                    "package.json": json.dumps({"dependencies": {"express": "^4"}, "scripts": scripts}),
                    "server.js": "app.listen(3000)\n",
                })
                self.assertIsNone(draft)
                self.assertIn("scripts가 객체가 아니어서", " ".join(f["detail"] for f in result["findings"]))

    def test_node_version_default_and_from_project(self):
        pkg = json.dumps({"dependencies": {"express": "^4"}, "scripts": {"start": "node a.js"}})
        _, d = draft_of({"package.json": pkg})
        self.assertIn("FROM node:22-slim", d["content"], "버전이 없으면 지원 중인 LTS")
        _, d = draft_of({"package.json": pkg, ".nvmrc": "20.11.1\n"})
        self.assertIn("FROM node:20-slim", d["content"], "프로젝트에 적힌 버전을 따름")

    def test_node_env_is_set_after_build(self):
        # NODE_ENV=production 이 먼저 있으면 매니저가 개발 의존성을 빼 빌드가 실패함
        _, d = draft_of({"package.json": json.dumps({"dependencies": {"express": "^4"}, "scripts": {"build": "tsc", "start": "node dist/a.js"}}),
                         "pnpm-lock.yaml": "x\n"})
        c = d["content"]
        self.assertLess(c.index("pnpm run build"), c.index("ENV NODE_ENV=production"))

    def test_start_only_typescript_keeps_development_tools(self):
        for lock in (None, "package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
            with self.subTest(lock=lock):
                spec = {"package.json": json.dumps({"dependencies": {"express": "^4"},
                        "devDependencies": {"tsx": "^4"}, "scripts": {"start": "tsx server.ts"}}),
                        "server.ts": "app.listen(3000)\n"}
                if lock:
                    spec[lock] = "{}"
                _, draft = draft_of(spec)
                content = draft["content"]
                self.assertNotIn("--omit=dev", content)
                self.assertNotIn("--prod", content)
                if lock in (None, "package-lock.json"):
                    self.assertIn("--include=dev", content)
                self.assertLess(content.index(" install") if lock != "package-lock.json" else content.index("npm ci"),
                                content.index("ENV NODE_ENV=production"))

    def test_next_config_keeps_health_url_unresolved(self):
        pkg = json.dumps({"dependencies": {"next": "^15"}, "scripts": {"start": "next start"}})
        for config in ("next.config.js", "next.config.mjs", "next.config.cjs", "next.config.ts"):
            for route in ("app/page.tsx", "app/api/health/route.ts", "pages/index.js"):
                with self.subTest(config=config, route=route):
                    analysis = analyze_files(files(**{"package.json": pkg, config: "export default {basePath: '/shop'}\n", route: "export default function Page() {}\n"}))
                    self.assertIsNone(analysis.health_check_path)
                    self.assertIn("basePath", analysis.unresolved["health_check_path"])

    def test_next_config_without_routing_keeps_file_route(self):
        # 헤더·트랜스파일 같은 설정만 있으면 주소가 바뀌지 않음 → 파일 경로로 정함
        pkg = json.dumps({"dependencies": {"next": "^15"}, "scripts": {"start": "next start"}})
        config = ('import type { NextConfig } from "next";\nconst nextConfig: NextConfig = {\n  transpilePackages: ["@x/shared"],\n'
                  '  async headers() { return [{ source: "/(.*)", headers: [] }]; },\n};\nexport default nextConfig;\n')
        a = analyze_files(files(**{"apps/web/package.json": pkg, "apps/web/next.config.ts": config, "apps/web/src/app/page.tsx": "x\n"}))
        self.assertEqual(a.health_check_path, "/")
        self.assertNotIn("health_check_path", a.unresolved)
        # 모듈 불러오기·값 변환 호출만 있으면 주소가 바뀌지 않음
        plain = ("const path = require('path')\nconst port = Number(process.env.PORT)\nconst dir = path.join(__dirname, 'x')\n"
                 "module.exports = { output: 'standalone' }\n")
        a = analyze_files(files(**{"package.json": pkg, "next.config.js": plain, "app/page.tsx": "x\n"}))
        self.assertEqual(a.health_check_path, "/")

    def test_next_routing_that_may_move_paths_stays_unresolved(self):
        pkg = json.dumps({"dependencies": {"next": "^15"}, "scripts": {"start": "next start"}})
        cases = {
            "rewrites": {"next.config.js": "module.exports = { async rewrites() { return [] } }\n"},
            "local import": {"next.config.mjs": "import base from './config/base.mjs'\nexport default base\n"},
            "spread": {"next.config.js": "const shared = require('@x/config')\nmodule.exports = { ...shared }\n"},
            "plugin wrapper": {"next.config.mjs": "import withIntl from 'next-intl/plugin'\nexport default withIntl()({})\n"},
            "middleware": {"next.config.js": "module.exports = {}\n", "src/middleware.ts": "export function middleware() {}\n"},
            # 래퍼 결과를 변수에 담았다가 내보냄 (PR #40 리뷰)
            "wrapper via variable": {"next.config.js": "const withRouting = require('next-routing')\nconst config = withRouting({})\nmodule.exports = config\n"},
            "typed wrapper via variable": {"next.config.ts": "import withX from 'next-x'\nconst config: NextConfig = withX({})\nexport default config\n"},
            "awaited wrapper": {"next.config.mjs": "const config = await makeConfig()\nexport default config\n"},
            "curried require": {"next.config.js": "const config = require('@next/bundle-analyzer')({ enabled: true })({})\nmodule.exports = config\n"},
        }
        for name, extra in cases.items():
            with self.subTest(name=name):
                a = analyze_files(files(**{"package.json": pkg, "app/page.tsx": "x\n", **extra}))
                self.assertIsNone(a.health_check_path)
                self.assertIn("basePath", a.unresolved["health_check_path"])

    def test_next_health_path_from_file_routes(self):
        pkg = json.dumps({"private": True, "dependencies": {"next": "^15"}, "scripts": {"build": "next build", "start": "next start"}})
        app_router = analyze_files(files(**{"package.json": pkg, "src/app/page.tsx": "export default function P() {}\n"}))
        self.assertEqual(app_router.health_check_path, "/")
        pages_router = analyze_files(files(**{"package.json": pkg, "pages/index.js": "export default () => null\n"}))
        self.assertEqual(pages_router.health_check_path, "/")
        with_api = analyze_files(files(**{"package.json": pkg, "app/page.tsx": "x\n", "app/api/health/route.ts": "export function GET() {}\n"}))
        self.assertEqual(with_api.health_check_path, "/api/health", "헬스체크 전용 경로가 있으면 그것")

    def test_no_draft_when_entry_is_ambiguous_or_dockerfile_exists(self):
        # 앱 팩토리라 앱 객체를 확정할 수 없음 → 추측하지 않음
        result, d = draft_of({"requirements.txt": "flask\n", "app.py": "from flask import Flask\ndef create_app():\n    return Flask(__name__)\n"})
        self.assertIsNone(d)
        self.assertIn("초안도 만들지 못했습니다", " ".join(f["detail"] for f in result["findings"]))
        # 앱 객체가 두 곳이면 확정하지 않음
        _, d = draft_of({"requirements.txt": "fastapi\n", "a.py": "x = FastAPI()\n", "b.py": "y = FastAPI()\n"})
        self.assertIsNone(d)
        # Dockerfile이 이미 있으면 초안을 만들지 않음
        _, d = draft_of({"requirements.txt": "flask\n", "app.py": "app = Flask(__name__)\n", "Dockerfile": "FROM python:3.12\n"})
        self.assertIsNone(d)


if __name__ == "__main__":
    unittest.main()
