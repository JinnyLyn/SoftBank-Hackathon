"""Supervisor 단위 계약: 실제 Docker·사용자 서비스는 호출하지 않는다."""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import onprem_service as service


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'worker.json'
        self.process = Mock()
        self.process.poll.return_value = None
        self.data = dict(version=1, run_id='run', phase='idle', observed_at=100,
                         last_success_at=100, deadline_at=None)

    def status(self, now=101, **updates):
        self.data.update(updates)
        service.atomic_json(self.path, self.data)
        return service.progress_status(self.path, 'run', self.process, now)['status']

    def test_actual_empty_api_success_ready(self):
        self.assertEqual(self.status(), 'ready')

    def test_no_observation_is_not_success(self):
        self.assertEqual(self.status(last_success_at=None), 'starting')
        self.assertEqual(self.status(now=115), 'stale')

    def test_failed_polls_cannot_refresh_success(self):
        self.assertEqual(self.status(now=114, observed_at=114), 'ready')
        self.assertEqual(self.status(now=115, observed_at=115), 'stale')

    def test_long_busy_and_exact_deadline(self):
        self.assertEqual(self.status(now=500, phase='build', deadline_at=600), 'ready')
        self.assertEqual(self.status(now=600), 'stale')

    def test_busy_requires_prior_api_success(self):
        self.assertEqual(self.status(phase='build', deadline_at=600, last_success_at=None), 'starting')

    def test_busy_is_bounded(self):
        self.assertEqual(self.status(phase='apply', deadline_at=3701), 'stale')

    def test_run_mismatch_future_unknown(self):
        for updates in ({'run_id': 'previous'}, {'observed_at': 102}, {'last_success_at': 102},
                        {'phase': 'whatever'}, {'phase': []}, {'phase': {}}, {'version': 2}, {'observed_at': True}):
            with self.subTest(updates=updates):
                old = dict(self.data)
                self.assertEqual(self.status(**updates), 'stale')
                self.data = old

    def test_missing_broken_and_dead(self):
        self.assertEqual(service.progress_status(self.path, 'run', self.process, 101)['status'], 'starting')
        for content in ('{', '[]'):
            self.path.write_text(content)
            self.assertEqual(service.progress_status(self.path, 'run', self.process, 101)['status'], 'stale')
        self.process.poll.return_value = 0
        self.assertEqual(service.progress_status(self.path, 'run', self.process, 101)['status'], 'down')

    def test_error_phase(self):
        self.assertEqual(self.status(phase='error'), 'down')


class LauncherTests(unittest.TestCase):
    def make_service(self, directory):
        return service.Service(argparse.Namespace(state_dir=Path(directory), api_port=18000,
                                                  web_port=15173, startup_timeout=600))

    def test_clean_environment(self):
        env = service.clean_env(dict(PATH='/bin', HOME='/home/test', DOCKER_CONTEXT='remote', DOCKER_HOST='ssh://x',
                                     AWS_SECRET_ACCESS_KEY='secret', ANTHROPIC_API_KEY='secret', API_TARGET='other',
                                     VITE_USE_MOCK='true', WORKER_API_TOKEN='foreign'))
        self.assertEqual(env, dict(PATH='/bin', HOME='/home/test'))

    @patch.object(service, 'run')
    def test_docker_context_precedence_and_remote_rejection(self, run):
        run.return_value = 'unix:///run/docker.sock'
        self.assertEqual(service.docker_endpoint({}, {'DOCKER_CONTEXT': 'local', 'DOCKER_HOST': 'ssh://ignored'}),
                         'unix:///run/docker.sock')
        self.assertIn('local', run.call_args.args[0])
        run.return_value = 'ssh://remote'
        with self.assertRaisesRegex(RuntimeError, 'SSH/TCP'):
            service.docker_endpoint({}, {'DOCKER_CONTEXT': 'remote'})

    def test_explicit_endpoint_only_local(self):
        self.assertEqual(service.docker_endpoint({}, {'DOCKER_HOST': 'npipe:////./pipe/docker_engine'}),
                         'npipe:////./pipe/docker_engine')
        with self.assertRaises(RuntimeError):
            service.docker_endpoint({}, {'DOCKER_HOST': 'tcp://localhost:2375'})

    @patch.object(service.socket, 'socket')
    def test_port_collision_is_failure(self, socket):
        socket.return_value.__enter__.return_value.bind.side_effect = OSError('occupied')
        with self.assertRaisesRegex(RuntimeError, '사용 중'):
            service.check_ports([18000])
        with self.assertRaisesRegex(RuntimeError, '서로 달라야'):
            service.check_ports([18000, 18000])

    @patch.object(service, 'run')
    def test_prepare_persistence_lock_and_endpoint(self, run):
        def fake(command, *args, **kwargs):
            if command[0] == 'node':
                return 'v22.12.0'
            if command[:3] == ['docker', 'context', 'show']:
                return 'default'
            if command[:3] == ['docker', 'context', 'inspect']:
                return 'unix:///local.sock'
            return 'ok'
        run.side_effect = fake
        with tempfile.TemporaryDirectory() as directory, patch.object(service.shutil, 'which', return_value='/bin/tool'), \
                patch.object(service, 'check_ports'), patch.object(Path, 'exists', autospec=True) as exists:
            original_exists = __import__('os').path.exists
            exists.side_effect = lambda p: True if str(p).endswith('node_modules/.bin/vite') else original_exists(p)
            first = self.make_service(directory)
            first.prepare()
            config = (Path(directory) / 'identity.json').read_text()
            second = self.make_service(directory)
            with self.assertRaisesRegex(RuntimeError, '이미 실행 중'):
                second.prepare()
            second.close()
            first.close()
            third = self.make_service(directory)
            third.prepare()
            self.assertEqual((Path(directory) / 'identity.json').read_text(), config)
            self.assertNotEqual(first.run_id, third.run_id)
            self.assertNotIn('DOCKER_CONTEXT', third.env)
            self.assertEqual(third.env['DOCKER_HOST'], 'unix:///local.sock')
            self.assertEqual((Path(directory) / 'uploads').stat().st_mode & 0o777, 0o777)
            self.assertEqual((Path(directory) / 'status').stat().st_mode & 0o777, 0o755)
            third.close()

    @patch.object(service, 'run', side_effect=subprocess.TimeoutExpired('docker', 3))
    def test_docker_probe_timeout_not_ready(self, _run):
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            (instance.state / 'status').mkdir()
            self.assertFalse(instance.snapshot())
            data = json.loads((instance.state / 'status/status.json').read_text())
            self.assertEqual(data['components']['docker']['status'], 'down')
            self.assertEqual(data['expires_at'] - data['checked_at'], 15)

    @patch.object(service, 'stop_process')
    @patch.object(service, 'run')
    def test_cleanup_only_owned_handles_and_compose_no_purge(self, run, stop):
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            owned = Mock()
            instance.children = {'worker': owned}
            instance.compose = ['docker', 'compose', '--project-name', 'owned-only']
            instance.compose_started = True
            self.assertTrue(instance.close())
            stop.assert_called_once_with(owned)
            self.assertEqual(run.call_args.args[0], instance.compose + ['down', '--timeout', '10'])
            self.assertNotIn('--volumes', run.call_args.args[0])

    @patch.object(service.subprocess, 'Popen')
    @patch.object(service, 'stop_process')
    def test_command_timeout_cleans_owned_group(self, stop, popen):
        popen.return_value.communicate.side_effect = subprocess.TimeoutExpired('docker', 3)
        with self.assertRaises(subprocess.TimeoutExpired):
            service.run(['docker', 'info'], {}, timeout=3)
        stop.assert_called_once_with(popen.return_value)
        self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])

    @patch.object(service.os, 'killpg')
    def test_process_group_stopped_not_global(self, kill):
        process = Mock(pid=98765)
        service.stop_process(process)
        self.assertEqual([c.args for c in kill.call_args_list],
                         [(98765, signal.SIGTERM), (98765, signal.SIGKILL)])

    @patch.object(service.urllib.request, 'urlopen')
    @patch.object(service, 'run')
    def test_analyzer_downloads_through_api_without_reading_private_uploads(self, run, urlopen):
        urlopen.return_value.__enter__.return_value.status = 200
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            instance.env['WORKER_API_TOKEN'] = 'dedicated-token'
            private_uploads = instance.state / 'uploads'
            private_uploads.mkdir()
            source = private_uploads / 'private.zip'
            source.write_bytes(b'private source')
            source.chmod(0o600)
            instance.snapshot = Mock(return_value=True)
            calls = {}
            def spawn(name, command, cwd, env):
                calls[name] = (command, dict(env))
                instance.children[name] = Mock()
                instance.children[name].poll.return_value = None
            instance.spawn = spawn
            instance.start()
            self.assertEqual(calls['web'][1]['VITE_ONPREM_ONLY'], 'true')
            command, env = calls['analyzer']
            path = Path(command[command.index('--upload-dir') + 1])
            self.assertNotEqual(path, private_uploads)
            self.assertEqual(path, instance.state / 'source-via-api' / instance.run_id)
            self.assertFalse(path.exists())
            self.assertEqual(env['WORKER_API_TOKEN'], 'dedicated-token')
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source.read_bytes(), b'private source')

    @patch.object(service.subprocess, 'Popen')
    def test_migration_failure_retains_stage_class_code_not_secret(self, popen):
        startup, migration = Mock(), Mock()
        startup.communicate.return_value = (None, None)
        startup.returncode = 0
        migration.communicate.return_value = ('stdout PRIVATE_SENTINEL',
            "Traceback (most recent call last):\n"
            "pymysql.err.OperationalError: (1045, 'mysql://PRIVATE_SENTINEL@db')\n")
        migration.returncode = 1
        popen.side_effect = [startup, migration]
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            instance.compose = ['docker', 'compose']
            with self.assertRaises(RuntimeError) as raised:
                instance.start()
            diagnostic = str(raised.exception)
            self.assertIn('플랫폼 DB 마이그레이션', diagnostic)
            self.assertIn('OperationalError code=1045', diagnostic)
            self.assertIn('exit 1', diagnostic)
            self.assertNotIn('PRIVATE_SENTINEL', diagnostic)
            self.assertNotIn('mysql://', diagnostic)

    def test_diagnosis_unknown_output_and_messages_remain_private(self):
        self.assertEqual(service.safe_diagnosis('PRIVATE_SENTINEL'), '진단 유형 확인 불가 (원문 비공개)')
        self.assertEqual(service.safe_diagnosis('ValueError: PRIVATE_SENTINEL'), 'ValueError')

    @patch.object(service, 'run', side_effect=RuntimeError('startup timeout'))
    def test_partial_compose_start_records_cleanup_ownership(self, run):
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            instance.compose = ['docker', 'compose', '--project-name', 'owned']
            with self.assertRaisesRegex(RuntimeError, 'startup timeout'):
                instance.start()
            self.assertTrue(instance.compose_started)
            run.side_effect = None
            self.assertTrue(instance.close())
            self.assertEqual(run.call_args.args[0][-3:], ['down', '--timeout', '10'])

    @patch.object(service.subprocess, 'Popen')
    def test_child_spawn_failure_leaves_no_fake_handle(self, popen):
        popen.side_effect = OSError('exec failed')
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            (instance.state / 'logs').mkdir()
            with self.assertRaises(OSError):
                instance.spawn('worker', ['missing'], service.ROOT, {})
            self.assertEqual(instance.children, {})
            instance.close()
            self.assertTrue(instance.logs[0].closed)

    @patch.object(service.shutil, 'which', return_value=None)
    def test_missing_tool_fails_before_resources(self, _which):
        with tempfile.TemporaryDirectory() as directory:
            instance = self.make_service(directory)
            with self.assertRaisesRegex(RuntimeError, '실행기'):
                instance.prepare()
            self.assertFalse(instance.compose_started)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
