"""Readiness contract tests: synthetic launcher snapshots and live DB callback."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.onprem_readiness import readiness


class OnpremReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'status.json'
        self.env = {'ONPREM_STATUS_FILE': str(self.path), 'ONPREM_RUN_ID': 'test-run'}
        self.db = Mock()
        self.data = dict(version=1, run_id='test-run', checked_at=1000, expires_at=1015,
                         target='local_docker', analysis_mode='rules',
                         components={name: {'status': 'ready'} for name in ('docker', 'analyzer', 'worker')})

    def read(self):
        self.path.write_text(json.dumps(self.data))
        return readiness(self.db, env=self.env, now=1001)

    def test_legacy_environment_is_unconfigured_without_file_access(self):
        result = readiness(self.db, env={}, now=1001)
        self.assertEqual(result['status'], 'unconfigured')
        self.assertIn('scripts/run_onprem.sh', result['reasons'][0]['action'])

    def test_missing_snapshot_is_starting(self):
        self.assertEqual(readiness(self.db, env=self.env, now=1001)['status'], 'starting')

    def test_fresh_components_and_live_db_are_ready(self):
        result = self.read()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['checked_at'], '1970-01-01T00:16:40+00:00')
        self.assertEqual(result['expires_at'], '1970-01-01T00:16:55+00:00')
        self.assertEqual(set(result['components']), {'api', 'db', 'docker', 'analyzer', 'worker'})
        self.db.assert_called_once_with()

    def test_busy_workers_remain_ready_with_phase_explanation(self):
        self.data['components']['analyzer']['phase'] = 'analyzing'
        self.data['components']['worker']['phase'] = 'build'
        result = self.read()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['components']['worker']['phase'], 'build')

    def test_db_failure_overrides_ready_without_exception_disclosure(self):
        self.db.side_effect = RuntimeError('mysql://secret-user:secret-password@private-db')
        result = self.read()
        self.assertEqual(result['status'], 'down')
        self.assertEqual(result['components']['db']['status'], 'down')
        self.assertNotIn('secret-password', json.dumps(result))
        self.assertEqual(result['reasons'][0]['code'], 'db_down')

    def test_component_nonready_propagates(self):
        for status in ('down', 'stale', 'starting', 'unconfigured'):
            with self.subTest(status=status):
                self.data['components']['worker']['status'] = status
                result = self.read()
                self.assertEqual(result['status'], status)
                self.assertEqual(result['reasons'][0]['code'], 'worker_' + status)

    def test_expired_future_and_overlong_snapshots_fail_closed(self):
        for checked, expires in ((980, 995), (1002, 1017), (1000, 1016), (1000, 1001), (-1, 1005)):
            with self.subTest(checked=checked, expires=expires):
                self.data.update(checked_at=checked, expires_at=expires)
                self.assertEqual(self.read()['status'], 'stale')

    def test_wrong_run_version_target_and_mode_fail_closed(self):
        for key, value in (('run_id', 'previous-run'), ('version', 2), ('target', 'aws'), ('analysis_mode', 'llm')):
            with self.subTest(key=key):
                old = self.data[key]
                self.data[key] = value
                self.assertEqual(self.read()['status'], 'stale')
                self.data[key] = old

    def test_malformed_numeric_timestamps_fail_closed(self):
        for value in (True, None, '1000', float('nan'), float('inf'), [], {}):
            with self.subTest(value=value):
                self.data['checked_at'] = value
                self.assertEqual(self.read()['status'], 'stale')

    def test_invalid_component_schema_fails_closed(self):
        for components in (None, [], {}, {'worker': {'status': 'ready'}},
                           {'docker': {'status': 'ready'}, 'analyzer': {'status': 'ready'}, 'worker': {'status': 'healthy'}},
                           {'docker': {'status': 'ready'}, 'analyzer': {'status': 'ready'}, 'worker': {'status': 'ready', 'phase': 'unknown'}}):
            with self.subTest(components=components):
                self.data['components'] = components
                self.assertEqual(self.read()['status'], 'stale')

    def test_invalid_json_and_nonobject_snapshots_fail_closed(self):
        for raw in ('{broken', '[]', 'null', '"secret-content"', 'x' * 17000):
            with self.subTest(raw=raw[:20]):
                self.path.write_text(raw)
                result = readiness(self.db, env=self.env, now=1001)
                self.assertEqual(result['status'], 'stale')
                self.assertIsNone(result['checked_at'])
                self.assertNotIn('secret-content', json.dumps(result))

    def test_only_public_component_fields_are_returned(self):
        self.data['components']['worker']['token'] = 'private-token'
        self.data['private_path'] = '/private/state'
        result = self.read()
        self.assertEqual(result['status'], 'ready')
        self.assertNotIn('private-token', json.dumps(result))
        self.assertNotIn('/private/state', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
