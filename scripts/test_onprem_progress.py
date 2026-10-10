"""Opt-in process observations; no Docker, database, dotenv or external calls."""
import argparse
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / 'ai', ROOT / 'infra/worker', ROOT / 'scripts'):
    sys.path.insert(0, str(directory))
import runner
import worker
from onprem_progress import Progress, from_args


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'progress.json'
        self.now = 1000.0
        self.progress = Progress(self.path, 'test-run', clock=lambda: self.now)

    def snapshot(self):
        return json.loads(self.path.read_text())

    def test_startup_does_not_claim_api_success(self):
        self.assertEqual(self.snapshot()['phase'], 'starting')
        self.assertIsNone(self.snapshot()['last_success_at'])

    def test_api_observation_and_rewrite_do_not_extend_busy_deadline(self):
        self.progress.success()
        self.progress.begin('build', 1200)
        self.now += 40
        self.progress.success()
        self.progress.write()
        self.assertEqual(self.snapshot()['deadline_at'], 2200)
        self.assertEqual(self.snapshot()['last_success_at'], 1040)
        self.assertEqual(self.snapshot()['phase'], 'build')

    def test_new_phase_gets_its_own_deadline(self):
        self.progress.begin('source', 30)
        self.now += 10
        self.progress.begin('build', 60)
        self.assertEqual(self.snapshot()['deadline_at'], 1070)

    def test_idle_does_not_refresh_api_success(self):
        self.progress.success()
        self.now += 30
        self.progress.idle()
        self.assertEqual(self.snapshot()['last_success_at'], 1000)
        self.assertIsNone(self.snapshot()['deadline_at'])

    def test_idle_cannot_erase_error(self):
        self.progress.error()
        self.progress.idle()
        self.assertEqual(self.snapshot()['phase'], 'error')

    def test_success_recovers_error_without_busy_deadline(self):
        self.progress.error()
        self.progress.success()
        self.assertEqual(self.snapshot()['phase'], 'polling')
        self.assertEqual(self.snapshot()['last_success_at'], 1000)

    def test_failed_atomic_replace_preserves_previous_observation(self):
        previous = self.path.read_bytes()
        self.now += 30
        with patch.object(Path, 'replace', side_effect=OSError('unavailable')):
            self.progress.success()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_runner_empty_success_is_an_observation(self):
        api = Mock()
        api.projects.return_value = iter([])
        counts = runner.run_once(api, self.path.parent, runner.State(), progress=self.progress)
        self.assertEqual(counts, dict(recorded=0, skipped=0, failed=0))
        self.assertEqual(self.snapshot()['phase'], 'idle')
        self.assertEqual(self.snapshot()['last_success_at'], 1000)

    def test_runner_swallowed_api_error_is_not_empty_success(self):
        api = Mock()
        api.projects.side_effect = runner.ApiError(503, 'unavailable')
        counts = runner.run_once(api, self.path.parent, runner.State(), progress=self.progress)
        self.assertEqual(counts, dict(recorded=0, skipped=0, failed=0))
        self.assertEqual(self.snapshot()['phase'], 'error')
        self.assertIsNone(self.snapshot()['last_success_at'])

    def test_worker_successful_empty_claim_updates_observation(self):
        response = Mock(status=204)
        response.read.return_value = b''
        opener = Mock()
        opener.__enter__ = Mock(return_value=response)
        opener.__exit__ = Mock(return_value=False)
        with patch.object(worker.urllib.request, 'urlopen', return_value=opener):
            result = worker.Api('http://localhost', 'fixture', progress=self.progress).post('/api/worker/jobs/claim', {})
        self.assertIsNone(result)
        self.assertEqual(self.snapshot()['last_success_at'], 1000)

    def test_worker_connection_error_is_not_a_success(self):
        with patch.object(worker.urllib.request, 'urlopen', side_effect=URLError('offline')):
            with self.assertRaises(worker.ApiError):
                worker.Api('http://localhost', 'fixture', progress=self.progress).post('/api/worker/jobs/claim', {})
        self.assertEqual(self.snapshot()['phase'], 'error')
        self.assertIsNone(self.snapshot()['last_success_at'])

    def test_worker_server_and_auth_errors_are_nonready(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                error = HTTPError('http://localhost', status, 'failed', {}, io.BytesIO(b'{}'))
                with patch.object(worker.urllib.request, 'urlopen', side_effect=error):
                    with self.assertRaises(worker.ApiError):
                        worker.Api('http://localhost', 'fixture', progress=self.progress).get('/api/worker/jobs')
                self.assertEqual(self.snapshot()['phase'], 'error')
                self.assertIsNone(self.snapshot()['last_success_at'])


    def test_local_execution_observes_source_build_apply_in_order(self):
        from test_local_docker import plan_for
        plan = plan_for()
        job = dict(deployment_id='fixture-deployment', variables=plan['variables'],
                   terraform_plan_sha256=plan['digest'])
        cfg = worker.Config(targets=('local_docker',), progress=self.progress)
        phases = []

        def observe(expected):
            snapshot = self.snapshot()
            self.assertEqual(snapshot['phase'], expected)
            self.assertGreater(snapshot['deadline_at'], self.now)
            phases.append(expected)

        def fetch(*args):
            observe('source')
            return self.path.parent / 'source.zip', None, None

        def build(*args):
            observe('build')

        def apply(*args, **kwargs):
            observe('apply')
            kwargs['on_deploying']()

        with patch.object(worker, 'fetch_source', side_effect=fetch), \
                patch.object(worker.local_docker, 'build_image', side_effect=build), \
                patch.object(worker.local_docker, 'apply', side_effect=apply):
            api = Mock()
            worker.execute_local_job(api, cfg, job)
        self.assertEqual(phases, ['source', 'build', 'apply'])
        self.assertEqual(api.post.call_args.args[1]['status'], 'healthy')


class OptionalCliTests(unittest.TestCase):
    def test_no_arguments_means_no_reporter(self):
        self.assertIsNone(from_args(argparse.ArgumentParser(), argparse.Namespace(progress_file=None, run_id=None)))

    def test_reporter_requires_paired_args_and_absolute_path(self):
        for path, run_id in (('/tmp/progress.json', None), (None, 'test'), ('relative.json', 'test')):
            with self.subTest(path=path, run_id=run_id), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    from_args(argparse.ArgumentParser(), argparse.Namespace(progress_file=path, run_id=run_id))
                self.assertEqual(error.exception.code, 2)

    def test_no_dotenv_skips_loading_but_legacy_still_loads(self):
        for args, expected in ((['--no-dotenv'], 0), ([], 1)):
            with self.subTest(args=args), patch.object(runner, 'load_dotenv') as dotenv, \
                    patch.object(runner, 'make_llm_client', return_value=(None, None)), \
                    patch.object(runner, 'run_once', return_value=dict(recorded=0, skipped=0, failed=0)) as once:
                self.assertEqual(runner.main(['--once', '--no-llm', *args]), 0)
                self.assertEqual(dotenv.call_count, expected)
                self.assertIsNone(once.call_args.kwargs['progress'])


if __name__ == '__main__':
    unittest.main()
