from unittest.mock import patch
import test_learning as fixtures
from test_learning import APP
import unittest
from tv_use import TV, TVError, ConnectionLost
import time


class UIRouteTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    def anchor(self):
        result,_=self.runtime.observe('always')
        self.runtime.ui_anchor(result['visual_witness_id'],'home')

    def test_record_learn_and_generated_batch_uses_no_screenshot(self):
        self.anchor()
        result,_=self.runtime.ui_record('open_search','home','search',[{'press':'UP'}])
        self.runtime.learn(result['witness_id'],'Search input visibly focused')
        self.assertIn('ui:open_search',self.runtime.capabilities()['apps'][0]['learned'])
        self.anchor(); self.tv.screenshot.reset_mock()
        result,image=self.runtime.ui_run(APP['id'],'open_search')
        self.assertTrue(result['generated_addon_used']);self.assertIsNone(image)
        self.assertEqual(result['tracked_state'],'search')
        self.tv.screenshot.assert_not_called()

    def test_stale_context_and_primitive_require_fallback_without_execution(self):
        self.anchor(); result,_=self.runtime.ui_record('search','home','search',[{'press':'UP'}])
        self.runtime.learn(result['witness_id'])
        self.anchor(); self.runtime.ui_context['at']=time.monotonic()-61
        self.tv.run_ui_steps.reset_mock()
        result,image=self.runtime.ui_run(APP['id'],'search')
        self.assertFalse(result['executed']);self.assertIsNotNone(image)
        self.tv.run_ui_steps.assert_not_called()
        self.anchor(); self.runtime.primitive('press',['DOWN'])
        self.assertIsNone(self.runtime.ui_context)

    def test_unknown_version_rejects_recipe_and_missing_visual_cannot_anchor(self):
        with self.assertRaises(TVError): self.runtime.ui_anchor('a'*64,'home')
        self.anchor(); result,_=self.runtime.ui_record('search','home','search',[{'press':'UP'}])
        self.runtime.learn(result['witness_id'])
        self.tv.app_inventory.return_value=[dict(APP,version='2')]
        result,image=self.runtime.ui_run(APP['id'],'search')
        self.assertFalse(result['executed']);self.assertEqual(result['required_state'],'route_discovery')
        self.assertIsNotNone(image)

    def test_all_steps_validated_before_any_mutation_and_loss_not_replayed(self):
        self.anchor()
        with self.assertRaises(TVError):self.runtime.ui_record('bad','home','x',[{'press':'UP'},{'request':'arbitrary'}])
        self.tv.run_ui_steps.assert_not_called()
        self.tv.run_ui_steps.side_effect=ConnectionLost('lost')
        result,_=self.runtime.ui_record('search','home','x',[{'press':'UP'}])
        self.assertFalse(result['ok']);self.tv.run_ui_steps.assert_called_once()
        self.assertIsNone(self.runtime.ui_context)

    def test_real_executor_stops_on_app_switch_and_bounds_wait(self):
        from unittest.mock import Mock
        tv=TV('192.168.1.100');tv.press=Mock();tv.foreground=Mock(side_effect=[APP['id'],'other'])
        with self.assertRaises(TVError):tv.run_ui_steps(APP['id'],[{'press':'UP'},{'press':'ENTER'}])
        tv.press.assert_called_once_with('UP')
        with self.assertRaises(TVError):TV.validate_ui_steps([{'wait':5}]*3)
        with self.assertRaises(TVError):TV.validate_ui_steps([{'text':'$VALUE'}],'')

    def test_idle_pointer_recovers_before_input_without_replay(self):
        from unittest.mock import Mock
        import websocket
        tv=TV('192.168.1.100'); old=Mock(); fresh=Mock();tv.pointer=old
        old.recv_data_frame.side_effect=websocket.WebSocketTimeoutException('idle')
        tv.request=Mock(return_value={'socketPath':'ws://192.168.1.100:3000/resources/input'})
        tv._open_socket=Mock(return_value=fresh)
        tv.press('UP')
        old.send.assert_not_called(); old.close.assert_called_once()
        fresh.send.assert_called_once_with('type:button\nname:UP\n\n')

    def test_same_sidebar_on_different_page_is_not_same_origin(self):
        self.anchor()
        result,_=self.runtime.ui_record('open_search','home','search',[{'press':'UP'}])
        self.runtime.learn(result['witness_id'])
        self.anchor();self.runtime.ui_context['state']='other_page_home'
        self.tv.run_ui_steps.reset_mock()
        result,_=self.runtime.ui_run(APP['id'],'open_search')
        self.assertFalse(result['executed']);self.tv.run_ui_steps.assert_not_called()

    def test_replace_and_submit_are_bounded_native_ime_calls(self):
        from unittest.mock import Mock
        tv=TV('192.168.1.100');tv.request=Mock(return_value={'returnValue':True});tv.foreground=Mock(return_value=APP['id'])
        tv.run_ui_steps(APP['id'],[{'replace_text':'$VALUE'},{'submit':True}],'Blue Planet')
        self.assertEqual(tv.request.call_args_list[0].args,('ssap://com.webos.service.ime/insertText',{'text':'Blue Planet','replace':True}))
        self.assertEqual(tv.request.call_args_list[1].args,('ssap://com.webos.service.ime/sendEnterKey',))
        with self.assertRaises(TVError): TV.validate_ui_steps([{'submit':False}])
