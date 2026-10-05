"""Paired device registry and bounded Wake-on-LAN, without new dependencies."""
import fcntl
import hashlib
import json
from pathlib import Path
import re
import socket
from app_learning import atomic_write
from tv_use import TVError, validate_host


def normalized_mac(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value):
        raise TVError('Expected a six-byte MAC address')
    raw = bytes.fromhex(value.replace(':', ''))
    if raw == b'\0'*6 or raw[0] & 1:
        raise TVError('Wake requires a unicast device MAC')
    return value.lower()


def wake_packet(mac):
    return b'\xff'*6 + bytes.fromhex(normalized_mac(mac).replace(':',''))*16


def network_identity(info, network):
    model = info.get('modelName')
    macs = sorted(set(normalized_mac(network[k]['macAddress']) for k in ['wifiInfo','wiredInfo']
                      if network.get(k, {}).get('macAddress')))
    if not model or not macs:
        raise TVError('Registration requires native TV model and network MAC identity')
    return hashlib.sha256((model+'|'+'|'.join(macs)).encode()).hexdigest(), macs


def send_wake(macs, broadcast):
    destination = validate_host(broadcast)
    if not macs or len(macs) > 2:
        raise TVError('Wake requires the registered TV network MACs')
    packets = [wake_packet(mac) for mac in macs]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for packet in packets:
            sock.sendto(packet, (destination, 9))
    return {'sent': True, 'packets':len(packets), 'acknowledged':False}


class Devices:
    def __init__(self, root):
        self.path = Path(root)/'tv-devices.json'

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'schema':1,'devices':{}}

    def register(self, tv, info, name, broadcast):
        if not isinstance(name,str) or not name.strip() or len(name)>80:
            raise TVError('Device name must contain 1-80 characters')
        validate_host(broadcast)
        network = tv.request('ssap://com.webos.service.connectionmanager/getinfo')
        device_id, macs = network_identity(info, network)
        credential = tv.state_dir/('device-'+device_id+'.json')
        atomic_write(credential, tv.key_path.read_text())
        record = {'id':device_id,'name':name.strip(),'host':tv.host,'model':info['modelName'],
                  'macs':macs,'broadcast':broadcast,'credential':credential.name}
        with self.path.with_suffix('.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            data=self.read();data['devices'][device_id]=record
            atomic_write(self.path,json.dumps(data,indent=2)+'\n')
        tv.key_path=credential
        return record

    def resolve(self, target):
        if not isinstance(target,str): raise TVError('Device target must be a name or ID')
        matches=[d for d in self.read()['devices'].values()
                 if target in {d['id'],d['host']} or target.lower()==d['name'].lower()]
        if len(matches)!=1:
            raise TVError('Select exactly one registered device by name or ID; shared IPs are ambiguous')
        return matches[0]

    def for_host(self, host, model=None):
        matches=[d for d in self.read()['devices'].values() if d['host']==host and
                 (model is None or d['model']==model)]
        return matches[0] if len(matches)==1 else None
