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
