import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tv_use import TV, TVError, validate_host


class TVTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tv = TV('192.168.1.100', state_dir=Path(self.directory.name), timeout=.1)

    def socket(self, messages):
        self.tv.ws = Mock()
        self.tv.ws.recv.side_effect = [json.dumps(m) for m in messages]
        return self.tv.ws

    def test_only_lan_hosts(self):
        for address in ["8.8.8.8", "127.0.0.1", "169.254.1.1", "224.1.1.1", "::1", "evil.test"]:
            with self.subTest(address=address), self.assertRaises((TVError, ValueError)):
                validate_host(address)

    def test_normal_actions_never_pair_implicitly(self):
        with patch.object(self.tv, "_open_socket") as opened:
            with self.assertRaisesRegex(TVError, "Not paired"):
                self.tv.connect()
            opened.assert_not_called()

    def test_tls_pin_mismatch_closes_before_credentials(self):
        self.tv.pin = "different"
        ws = Mock()
        ws.sock.getpeercert.return_value = b"certificate"
        with patch("websocket.create_connection", return_value=ws):
            with self.assertRaisesRegex(TVError, "certificate changed"):
                self.tv._open_socket("wss://192.168.1.100:3001")
        ws.send.assert_not_called()
        ws.close.assert_called_once()

    def test_pair_persists_only_after_registered(self):
        ws = Mock()
        ws.recv.side_effect = [json.dumps({"type": "response", "payload": {"pairingType": "PROMPT"}}),
                               json.dumps({"type": "registered", "payload": {"client-key": "test-key"}})]
        def opened(_):
            self.tv.pin = "pin"
            return ws
        with patch.object(self.tv, "_open_socket", side_effect=opened):
            self.tv.connect(pair=True)
        saved = json.loads(self.tv.key_path.read_text())
        self.assertEqual(saved["client_key"], "test-key")
        self.assertEqual(saved["certificate_sha256"], "pin")
        self.assertEqual(self.tv.key_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.tv.state_dir.stat().st_mode & 0o777, 0o700)

    def test_rejected_pair_does_not_persist(self):
        ws = Mock()
        ws.recv.return_value = json.dumps({"type": "error", "error": "denied"})
        with patch.object(self.tv, "_open_socket", return_value=ws):
            with self.assertRaisesRegex(TVError, "denied"):
                self.tv.connect(pair=True)
        self.assertFalse(self.tv.key_path.exists())
        ws.close.assert_called_once()

    def test_pair_wrong_model_does_not_save_credential(self):
        ws=Mock();ws.recv.return_value=json.dumps({'type':'registered','payload':{'client-key':'wrong-tv-key'}})
        with patch.object(self.tv,'_open_socket',return_value=ws),patch.object(self.tv,'request',return_value={'modelName':'55NANO866NA'}):
            with self.assertRaisesRegex(TVError,'different model'):
                self.tv.connect(pair=True,expected_model='65QNED826QB')
        self.assertFalse(self.tv.key_path.exists());ws.close.assert_called_once()

    def test_empty_pair_frame_reports_closed_connection(self):
        ws = Mock(); ws.recv.return_value = ""
        with patch.object(self.tv, "_open_socket", return_value=ws):
            with self.assertRaisesRegex(TVError, "closed"):
                self.tv.connect(pair=True)
        self.assertFalse(self.tv.key_path.exists())

    def test_request_matches_id_ignoring_other_events(self):
        self.socket([{"id": "event", "payload": {"other": 1}},
                     {"id": "1", "payload": {"volume": 10}}])
        self.assertEqual(self.tv.request("ssap://audio/getVolume"), {"volume": 10})

    def test_request_error_is_not_success(self):
        self.socket([{"id": "1", "type": "error", "error": "404 no such service"}])
        with self.assertRaisesRegex(TVError, "404"):
            self.tv.request("ssap://tv/executeOneShot")

    def test_request_false_return_value_is_error(self):
        self.socket([{"id": "1", "payload": {"returnValue": False, "errorText": "not allowed"}}])
        with self.assertRaisesRegex(TVError, "not allowed"):
            self.tv.request("ssap://tv/executeOneShot")

    def test_capture_and_pointer_urls_cannot_escape_tv(self):
        for url in ["https://evil.test/image", "file:///tmp/a", "http://user:pass@192.168.1.100/x"]:
            with self.subTest(url=url), self.assertRaises(TVError):
                self.tv.download(url)
        with patch.object(self.tv, "request", return_value={"socketPath": "ws://192.168.1.3:3000/x"}):
            with self.assertRaisesRegex(TVError, "unexpected pointer"):
                self.tv.press("HOME")

    def test_bounded_input_rejects_service_keys_and_injection(self):
        for button in ["POWER", "IN_START", "HOME\nname:POWER"]:
            with self.assertRaises(TVError):
                self.tv.press(button)
        for dx, dy in [(2001, 0), (0, float("nan")), (True, 1)]:
            with self.assertRaises(TVError):
                self.tv.move(dx, dy)
        with self.assertRaises(TVError):
            self.tv.scroll(101)
        with self.assertRaises(TVError):
            self.tv.text("a" * 1001)

    def test_missing_screenshot_is_reported_with_state(self):
        with patch.object(self.tv, "request", return_value={"appId": "home"}), \
             patch.object(self.tv, "screenshot", side_effect=TVError("404 unsupported")):
            observation, capture = self.tv.observe()
        self.assertIsNone(capture)
        self.assertEqual(observation["screenshot"], "unavailable")
        self.assertIn("foreground_app", observation["state"])
        self.assertIn("404 unsupported", observation["limitations"][0])

    def test_screenshot_rejects_nonimage(self):
        with patch.object(self.tv, "request", return_value={"imageUri": "http://192.168.1.100/x"}), \
             patch.object(self.tv, "download", return_value=(b"<html>error</html>", "text/html")):
            with self.assertRaisesRegex(TVError, "not a supported image"):
                self.tv.screenshot()

    def test_launch_only_installed_app(self):
        with patch.object(self.tv, "app_inventory", return_value=[{"id": "youtube.leanback.v4"}]), \
             patch.object(self.tv, "request") as request:
            with self.assertRaises(TVError):
                self.tv.launch("unknown")
            request.assert_not_called()

    def test_apps_excludes_hidden_services_and_verbose_metadata(self):
        payload = {"apps": [{"id": "netflix", "title": "Netflix", "visible": True, "extra": "large"},
                            {"id": "hidden.service", "visible": False}]}
        with patch.object(self.tv, "request", return_value=payload):
            self.assertEqual(self.tv.apps(), {"apps": [{"id": "netflix", "title": "Netflix"}]})

    def test_pointer_sent_does_not_claim_acknowledgement(self):
        self.tv.pointer = Mock()
        import time
        self.tv.pointer_checked_at=time.monotonic()
        result = self.tv.press("HOME")
        self.assertTrue(result["sent"])
        self.assertFalse(result["acknowledged"])
        self.tv.pointer.send.assert_called_once_with("type:button\nname:HOME\n\n")


if __name__ == "__main__":
    unittest.main()
