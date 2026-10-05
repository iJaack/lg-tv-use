import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tv_power import Devices, network_identity, wake_packet, send_wake
from tv_runtime import TVRuntime
from tv_use import TVError, ConnectionLost

INFO = {'modelName':'LG'}
NETWORK = {'wifiInfo':{'macAddress':'02:00:00:00:00:01'},
           'wiredInfo':{'macAddress':'02:00:00:00:00:02'},
           'p2pInfo':{'macAddress':'02:00:00:00:00:03'}}


class RegistryTests(unittest.TestCase):
    def test_identity_works_without_serial_and_ignores_p2p(self):
        identity, macs = network_identity(INFO, NETWORK)
        self.assertEqual(len(macs), 2)
        self.assertEqual(network_identity(dict(INFO, serialNumber='x'), NETWORK)[0], identity)
        self.assertNotEqual(network_identity({'modelName':'other'}, NETWORK)[0], identity)
        with self.assertRaises(TVError): network_identity(INFO, {})

    def test_wake_packet_is_exact_and_rejects_bad_targets(self):
        packet = wake_packet('02:00:00:00:00:01')
        self.assertEqual(packet, b'\xff'*6 + bytes.fromhex('020000000001')*16)
        for bad in ['00:00:00:00:00:00','FF:FF:FF:FF:FF:FF','bad']:
            with self.assertRaises(TVError): wake_packet(bad)
        with self.assertRaises(TVError): send_wake(['02:00:00:00:00:01'], '8.8.8.8')

    def test_separate_credential_backups_and_ambiguous_hosts(self):
        with tempfile.TemporaryDirectory() as folder:
            tv=Mock(); tv.state_dir=Path(folder); tv.host='192.168.1.100'
            tv.key_path=Path(folder)/'ip.json'; tv.key_path.write_text('{"client_key":"secret"}')
            tv.request.return_value=NETWORK
            registry=Devices(folder); first=registry.register(tv, INFO, 'TV Two','192.168.1.255')
            original=tv.key_path
            second=registry.register(tv, {'modelName':'different'}, 'TV One','192.168.1.255')
            self.assertNotEqual(first['credential'], second['credential'])
            self.assertEqual(original.stat().st_mode & 0o777, 0o600)
            self.assertEqual(registry.resolve('TV Two')['id'],first['id'])
            with self.assertRaises(TVError): registry.resolve('192.168.1.100')


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.tv=Mock(); self.tv.host='192.168.1.101'; self.tv.ws.connected=True
        self.runtime=TVRuntime(root=Path(self.folder.name),tv=self.tv)
        self.runtime.power_status=Mock()
        self.on={'state':'Active','on':True,'verified':True}
        self.off={'state':'Active Standby','on':False,'verified':True}

    def test_already_off_never_sends_off_again(self):
        self.runtime.power_status.return_value=self.off
        result=self.runtime.power('off',1)
        self.assertFalse(result['changed']); self.tv.request.assert_not_called()

    @patch('tv_runtime.time.sleep')
    def test_lost_off_delivery_is_not_replayed(self,_):
        self.runtime.power_status.side_effect=[self.on,self.off]
        self.tv.request.side_effect=ConnectionLost('lost')
        result=self.runtime.power('off',1)
        self.assertTrue(result['verified'])
        self.tv.request.assert_called_once_with('ssap://system/turnOff')

    @patch('tv_runtime.send_wake')
    def test_wake_uses_registered_device_and_reads_actual_state(self,wake):
        self.runtime.selected_device={'macs':['02:00:00:00:00:01'],'broadcast':'192.168.1.255'}
        self.runtime.power_status.side_effect=[TVError('offline'),self.on]
        self.assertTrue(self.runtime.power('on',1)['verified'])
        wake.assert_called_once_with(self.runtime.selected_device['macs'],'192.168.1.255')
        self.tv.request.assert_not_called()

    @patch('tv_runtime.time.monotonic',side_effect=[0,0,2])
    @patch('tv_runtime.time.sleep')
    def test_endpoint_disappearance_is_not_off_proof(self,_,clock):
        self.runtime.power_status.side_effect=[self.on,TVError('offline')]
        result=self.runtime.power('off',1)
        self.assertFalse(result['verified']); self.assertFalse(result['ok'])
        self.tv.request.assert_called_once()

    def test_wrong_native_identity_blocks_mutation(self):
        self.runtime.selected_device={'id':'wrong'}
        self.tv.request.return_value=NETWORK
        with self.assertRaises(TVError): self.runtime._verify_identity(INFO)
        self.tv.close.assert_called_once()

    def test_unregistered_offline_device_cannot_wake(self):
        self.runtime.power_status.side_effect=TVError('offline')
        with self.assertRaises(TVError): self.runtime.power('on',1)
        self.tv.request.assert_not_called()

    @patch('tv_runtime.send_wake')
    def test_screen_off_requires_display_wake_even_if_hardware_on(self,wake):
        self.runtime.selected_device={'macs':['02:00:00:00:00:01'],'broadcast':'192.168.1.255'}
        self.runtime.power_status.side_effect=[dict(self.on,state='Screen Off',display_on=False),dict(self.on,display_on=True)]
        result=self.runtime.power('on',1)
        self.assertTrue(result['changed']);wake.assert_called_once()


if __name__ == '__main__': unittest.main()
