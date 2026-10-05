import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from app_learning import AppLearning
from tv_runtime import TVRuntime
from tv_use import TV, TVError, ConnectionLost

APP = {'id': 'youtube.leanback.v4', 'title': 'YouTube', 'type': 'native', 'version': '1', 'inAppSearchParams': '{"contentTarget":"q=$SEARCH_KEYWORD"}'}

class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.learning = AppLearning(self.root); self.learning.bind('192.168.1.100', 'model', 'firmware')

    def test_version_and_device_scope_invalidate_route(self):
        self.learning.learn(APP, 'search', 'a'*64)
        self.assertTrue(self.learning.known(APP, 'search'))
        self.assertFalse(self.learning.known(dict(APP, version='2'), 'search'))
        self.learning.bind('192.168.1.100', 'model', 'new-firmware')
        self.assertFalse(self.learning.known(APP, 'search'))

    def test_generated_addon_reloads_exact_source_and_is_parameterized(self):
        tv = Mock()
        self.learning.learn(APP, 'search', 'a'*64)
        self.learning.addon(APP, 'search')(tv, 'first query')
        tv.native_app_action.assert_called_once_with(APP['id'], 'search', 'first query')
        self.learning.learn(APP, 'launch', 'b'*64)
        self.assertIsNotNone(self.learning.addon(APP, 'launch'))
        self.assertEqual(self.learning.code_path.stat().st_mode & 0o777, 0o600)

    def test_remote_strings_are_data_not_generated_code(self):
        app = dict(APP, id="4'; raise Exception('injected') #")
        self.learning.learn(app, 'launch', 'a'*64)
        tv = Mock(); self.learning.addon(app, 'launch')(tv)
        tv.native_app_action.assert_called_once_with(app['id'], 'launch', '')

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.tv = Mock(); self.tv.host = '192.168.1.100'; self.tv.ws.connected = True
        self.tv.metrics = {'connections': 0, 'requests': 0, 'screenshots': 0}
        self.tv.foreground.return_value = APP['id']
        self.tv.app_inventory.return_value = [copy.deepcopy(APP)]
        self.tv.request.return_value = {'modelName': 'model'}
        self.tv.native_app_action.return_value = {'returnValue': True}
        self.tv.observe.return_value = ({'state': {'foreground_app': {'appId': APP['id']}, 'audio': {'volume': 7}}, 'observed_at': 'now', 'limitations': []}, None)
        self.tv.screenshot.return_value = (b'image', 'image/jpeg')
        self.runtime = TVRuntime(root=Path(self.directory.name), tv=self.tv)

    @patch('tv_runtime.time.sleep')
    def test_unknown_route_learns_then_runs_without_images_or_reconnect(self, _):
        result, image = self.runtime.app_action(APP['id'], 'search', 'hello')
        self.assertEqual(image[0], b'image'); self.assertFalse(result['content_verified'])
        self.runtime.learn(result['witness_id'], 'Results visually inspected')
        self.tv.screenshot.reset_mock()
        result, image = self.runtime.app_action(APP['id'], 'search', 'different query')
        self.assertIsNone(image); self.assertTrue(result['generated_addon_used'])
        self.tv.screenshot.assert_not_called(); self.tv.connect.assert_not_called()

    @patch('tv_runtime.time.sleep')
    def test_cannot_learn_stale_version_or_wrong_witness(self, _):
        result, _ = self.runtime.app_action(APP['id'])
        with self.assertRaises(TVError): self.runtime.learn('0'*64)
        self.tv.app_inventory.return_value = [dict(APP, version='2')]
        with self.assertRaises(TVError): self.runtime.learn(result['witness_id'])

    @patch('tv_runtime.time.sleep')
    def test_refresh_visual_witness_and_auto_observe_known_app(self, _):
        self.runtime.app_action(APP['id'])
        self.tv.screenshot.return_value = (b'new-image', 'image/jpeg')
        result, _ = self.runtime.observe('always')
        self.assertEqual(result['witness_id'], hashlib.sha256(b'new-image').hexdigest())
        self.runtime.learn(result['witness_id'])
        self.tv.screenshot.reset_mock()
        result, image = self.runtime.observe()
        self.assertTrue(result['known_app']); self.assertIsNone(image)
        self.tv.screenshot.assert_not_called()

    def test_lost_mutation_is_never_replayed(self):
        self.tv.native_app_action.side_effect = ConnectionLost('unknown delivery')
        result, image = self.runtime.app_action(APP['id'], capture='never')
        self.assertFalse(result['ok']); self.assertEqual(result['outcome'], 'unverified')
        self.tv.native_app_action.assert_called_once()

    @patch('tv_runtime.time.sleep')
    def test_version_change_returns_to_discovery(self, _):
        result, _ = self.runtime.app_action(APP['id'])
        self.runtime.learn(result['witness_id'])
        self.tv.app_inventory.return_value = [dict(APP, version='2')]
        self.tv.screenshot.reset_mock()
        result, image = self.runtime.app_action(APP['id'])
        self.assertFalse(result['generated_addon_used'])
        self.assertTrue(result['fallback_required']); self.assertIsNotNone(image)
        self.tv.screenshot.assert_called_once()

    def test_primitive_loss_is_not_replayed_and_arbitrary_methods_are_blocked(self):
        self.tv.press.side_effect = ConnectionLost('unknown delivery')
        result, _ = self.runtime.primitive('press', ['ENTER'], 'never')
        self.assertFalse(result['ok']); self.tv.press.assert_called_once()
        with self.assertRaises(TVError): self.runtime.primitive('request', ['ssap://anything'])

    @patch('tv_runtime.time.sleep')
    def test_missing_capture_retains_state_but_cannot_learn(self, _):
        self.tv.screenshot.side_effect = TVError('DRM')
        result, image = self.runtime.app_action(APP['id'])
        self.assertTrue(result['app_verified']); self.assertIsNone(image)
        self.assertIsNone(result['witness_id'])
        with self.assertRaises(TVError): self.runtime.learn('a'*64)

    def test_read_health_reconnect_refreshes_device_scope(self):
        self.runtime.ready()
        self.tv.foreground.side_effect = [ConnectionLost('closed'), APP['id']]
        self.tv.request.return_value = {'modelName': 'new-model', 'major_ver': '2', 'minor_ver': '0'}
        self.runtime.ready()
        self.tv.connect.assert_called_once()
        self.assertEqual(self.runtime.device['model'], 'new-model')

class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tv = TV('192.168.1.100'); self.tv.app_inventory = Mock(return_value=[APP]); self.tv.request = Mock(return_value={'returnValue': True})

    def test_search_encodes_text_and_rejects_unadvertised_routes(self):
        self.tv.native_app_action(APP['id'], 'search', 'hello & q=x')
        self.assertEqual(self.tv.request.call_args.args[1]['params']['contentTarget'], 'q=hello%20%26%20q%3Dx')
        self.tv.app_inventory.return_value = [dict(APP, inAppSearchParams=None)]
        with self.assertRaises(TVError): self.tv.native_app_action(APP['id'], 'search', 'test')

    def test_stub_and_bad_video_ids_never_launch(self):
        self.tv.app_inventory.return_value = [dict(APP, type='stub')]
        with self.assertRaises(TVError): self.tv.native_app_action(APP['id'], 'launch')
        self.tv.request.assert_not_called()
        self.tv.app_inventory.return_value = [APP]
        with self.assertRaises(TVError): self.tv.native_app_action(APP['id'], 'youtube_video', 'x&v=evil')

    def test_lost_request_closes_and_reports_uncertain_delivery(self):
        tv = TV('192.168.1.100'); ws = Mock(); tv.ws = ws; ws.recv.return_value = ''
        with self.assertRaises(ConnectionLost): tv.request('ssap://system.launcher/launch')
        ws.send.assert_called_once(); ws.close.assert_called_once(); self.assertIsNone(tv.ws)

if __name__ == '__main__': unittest.main()
