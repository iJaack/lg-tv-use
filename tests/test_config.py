import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tv_use import TV, TVError
from tv_runtime import TVRuntime
from tv_cli import main


class ConfigTests(unittest.TestCase):
    def test_no_implicit_tv_target(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(TVError, 'TV_HOST'):
                TV()

    def test_state_override_keeps_generated_addons_together(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'TV_HOST': '192.168.1.100', 'TV_STATE_DIR': folder}, clear=True):
                runtime = TVRuntime()
                self.addCleanup(runtime.close)
                self.assertEqual(runtime.tv.state_dir, Path(folder))
                self.assertEqual(runtime.learning.path.parent, Path(folder) / 'addons')
                self.assertEqual(runtime.devices.path.parent, Path(folder) / 'addons')

    def test_explicit_host_overrides_environment(self):
        with patch.dict(os.environ, {'TV_HOST': '192.168.1.101'}):
            self.assertEqual(TV('192.168.1.100').host, '192.168.1.100')

    def test_discovery_needs_no_configured_tv(self):
        out = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['lg-tv-use', 'discover']), \
             patch('tv_cli.discover', return_value=[]) as discover, contextlib.redirect_stdout(out):
            main()
        discover.assert_called_once()
        self.assertIn('"devices": []', out.getvalue())

    def test_cli_missing_host_is_actionable_and_has_no_traceback(self):
        err = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['lg-tv-use', 'observe']), \
             contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as result:
            main()
        self.assertEqual(result.exception.code, 2)
        self.assertIn('TV_HOST', err.getvalue())
        self.assertNotIn('Traceback', err.getvalue())
