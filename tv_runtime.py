"""Persistent session, lightweight verification and screenshot-on-discovery policy."""
from __future__ import annotations
import atexit
import hashlib
import os
import re
from pathlib import Path
import time

from app_learning import AppLearning
from tv_use import ConnectionLost, TV, TVError
from tv_power import Devices, send_wake, network_identity

POLICIES = {"auto", "never", "always"}


class TVRuntime:
    def __init__(self, host=None, root=None, tv=None, expected_model=None):
        self.tv = tv or TV(host)
        self.learning = AppLearning(Path(root or os.environ.get("TV_PROFILE_DIR") or
                                        self.tv.state_dir / "addons"))
        self.last_witness = None
        self.ui_context = self.visual = None
        self.device = None
        self.devices = Devices(self.learning.path.parent)
        self.expected_model = expected_model or (os.environ.get("TV_MODEL") if host is None else None)
        self.selected_device = self.devices.for_host(self.tv.host, self.expected_model)
        if self.selected_device:
            self._use_registration(self.selected_device)
        atexit.register(self.close)

    def _use_registration(self, record):
        filename = record['credential']
        if not re.fullmatch(r'[a-f0-9]{64}',record['id']) or filename != 'device-' + record['id'] + '.json':
            raise TVError('Invalid device credential reference')
        self.tv.key_path = self.tv.state_dir / filename
        self.expected_model = record['model']
        self.selected_device = record

    def select(self, target):
        record = self.devices.resolve(target)
        self.close()
        self.tv = TV(record['host'])
        self.device = self.last_witness = None
        self._use_registration(record)
        self.ready()
        return {'selected':record['name'], 'device':self.device}

    def register_device(self, name, broadcast):
        self.ready()
        info = self.tv.request('ssap://system/getSystemInfo')
        record = self.devices.register(self.tv, info, name, broadcast)
        self._use_registration(record)
        return record

    def close(self):
        self.tv.close()
        self.ui_context = self.visual = None

    def ready(self, check_foreground=True):
        if self.tv.ws is None or not self.tv.ws.connected:
            self.ui_context = self.visual = None
            self.tv.connect()
            self.device = None
        # Read-only health probe can be retried. No mutation is ever replayed.
        info = None
        try:
            app = self.tv.foreground() if check_foreground else None
            if not check_foreground:
                info = self.tv.request('ssap://system/getSystemInfo')
        except ConnectionLost:
            self.ui_context = self.visual = None
            self.tv.connect()
            self.device = None
            app = self.tv.foreground() if check_foreground else None
            if not check_foreground:
                info = self.tv.request('ssap://system/getSystemInfo')
        if info and self.expected_model and info.get('modelName') != self.expected_model:
            self.close()
            raise TVError('Device identity mismatch during power verification')
        if info and self.selected_device:
            self._verify_identity(info)
        if self.device is None:
            info = info or self.tv.request("ssap://system/getSystemInfo")
            model = info.get("modelName", "unknown")
            if self.expected_model and model != self.expected_model:
                self.close()
                raise TVError('Device identity mismatch: expected ' + self.expected_model + ', reached ' + model)
            if self.selected_device and check_foreground:
                self._verify_identity(info)
            try:
                firmware = self.tv.request("ssap://com.webos.service.update/getCurrentSWInformation")
                revision = ".".join(str(firmware.get(k, "")) for k in ("major_ver", "minor_ver", "product_name"))
            except TVError:
                revision = "unavailable"
            self.device = {"host": self.tv.host, "model": model, "firmware": revision}
            self.learning.bind(self.tv.host, model, revision)
        return app

    def _verify_identity(self, info):
        network = self.tv.request('ssap://com.webos.service.connectionmanager/getinfo')
        identity, _ = network_identity(info, network)
        if identity != self.selected_device['id']:
            self.close()
            raise TVError('Native TV network identity differs from registered device')

    def _status(self):
        raw, _ = self.tv.observe(capture=False)
        state = raw["state"]
        audio = state.get("audio", {})
        audio = audio.get("volumeStatus", audio)
        return {"app_id": state.get("foreground_app", {}).get("appId"),
                "volume": audio.get("volume"), "muted": audio.get("muteStatus"),
                "observed_at": raw["observed_at"], "limitations": raw["limitations"]}

    def observe(self, capture="auto"):
        self._policy(capture)
        self.ready()
        status = self._status()
        inventory = self.tv.app_inventory()
        app = next((a for a in inventory if a["id"] == status["app_id"]), None)
        known = bool(app and self.learning.known(app, "launch"))
        image, limitation = self._capture(capture == "always" or capture == "auto" and not known)
        if limitation:
            status["limitations"].append(limitation)
        witness_id = None
        if image and self.last_witness:
            witness = self.last_witness
            if app == witness["metadata"] and status["app_id"] == app["id"] and self.learning.scope == witness["scope"]:
                witness_id = hashlib.sha256(image[0]).hexdigest()
                witness["id"] = witness_id
                witness["foreground_verified"] = True
            else:
                self.last_witness = None
        self._remember_visual(image, status)
        return {"state": status, "known_app": known, "screenshot": "captured" if image else
                "unavailable" if limitation else "not_requested", "witness_id": witness_id,
                "visual_witness_id":self.visual['id'] if image else None}, image

    def _capture(self, wanted):
        if not wanted:
            return None, None
        try:
            return self.tv.screenshot(), None
        except (TVError, OSError) as error:
            return None, "screenshot unavailable: " + str(error)

    @staticmethod
    def _policy(capture):
        if capture not in POLICIES:
            raise TVError("Capture policy must be auto, never or always")

    def capabilities(self):
        self.ready()
        return {"device": self.device, "apps": self.learning.capabilities(self.tv.app_inventory())}

    def power_status(self):
        self.ready(check_foreground=False)
        raw = self.tv.request('ssap://com.webos.service.tvpower/power/getPowerState')
        state = raw.get('state', 'Unknown')
        canonical=state.replace(' ','') if isinstance(state,str) else 'Unknown'
        known = canonical in {'Active','ScreenSaver','ScreenOff','PowerOff','Suspend','ActiveStandby'}
        return {'state':state, 'on':canonical in {'Active','ScreenSaver','ScreenOff'} if known else None,
                'display_on':canonical in {'Active','ScreenSaver'} if known else None,
                'verified':known, 'model':self.device['model']}

    def power(self, operation, wait_seconds=20):
        if operation not in {'on','off','status'} or type(wait_seconds) is not int or not 1 <= wait_seconds <= 45:
            raise TVError('Power accepts on/off/status and a 1-45 second verification window')
        if operation == 'status':
            try: return self.power_status()
            except (TVError, OSError) as error:
                return {'state':'unreachable','on':None,'verified':False,'error':str(error)}
        self.last_witness = None
        self.ui_context = self.visual = None
        before = None
        try: before = self.power_status()
        except (TVError, OSError):
            if operation == 'off':
                return {'ok':False,'verified':False,'error':'TV state unavailable; no power-off sent'}
        wanted = operation == 'on'
        def matches(state):
            return state['verified'] and state['on']==wanted and (not wanted or state.get('display_on') is not False)
        if before and matches(before):
            return {'ok':True,'verified':True,'changed':False,'state':before}
        if operation == 'on':
            if not self.selected_device:
                raise TVError('Register the paired TV network MACs while it is on before Wake-on-LAN')
            sent = send_wake(self.selected_device['macs'],self.selected_device['broadcast'])
            # Connected standby may also accept the native wake handshake.
            if before is not None:
                try: self.tv.request('ssap://com.webos.service.tvpower/power/turnOn')
                except (TVError,OSError): pass
        else:
            try:
                acknowledgement = self.tv.request('ssap://system/turnOff')
                sent = {'sent':True,'acknowledged':acknowledgement.get('returnValue') is True}
            except ConnectionLost:
                sent = {'sent':True,'acknowledged':False,'delivery':'uncertain; no replay'}
            except (TVError,OSError) as error:
                return {'ok':False,'verified':False,'error':str(error),'state':before}
        deadline=time.monotonic()+wait_seconds
        state={'state':'unreachable','on':None,'verified':False}
        while time.monotonic()<deadline:
            try:
                state=self.power_status()
                if matches(state):
                    return {'ok':True,'verified':True,'changed':True,'command':sent,'state':state}
            except (TVError,OSError) as error:
                state={'state':'unreachable','on':None,'verified':False,'error':str(error)}
            time.sleep(.4)
        return {'ok':False,'verified':False,'command':sent,'state':state,
                'limitation':'Control endpoint absence does not prove physical power state; no automatic action replay'}

    def app_action(self, app_id, operation="launch", value="", capture="auto"):
        self._policy(capture)
        started = time.perf_counter()
        self.ready()
        self.ui_context = self.visual = None
        metadata = next((a for a in self.tv.app_inventory() if a["id"] == app_id), None)
        if not metadata:
            raise TVError("App is not installed or visible")
        known = self.learning.known(metadata, operation)
        addon = self.learning.addon(metadata, operation) if known else None
        self.last_witness = None
        image = None
        before = dict(self.tv.metrics)
        try:
            result = addon(self.tv, value) if addon else self.tv.native_app_action(app_id, operation, value)
        except (TVError, OSError) as error:
            # Report uncertain outcomes. Recovery is read-only and never replays the action.
            try:
                self.ready()
                state = self._status()
                if capture != "never":
                    image = self.tv.screenshot()
            except (TVError, OSError):
                state = {"app_id": None}
            return {"ok": False, "error": str(error), "outcome": "unverified", "state": state,
                    "fallback_required": True}, image
        try:
            deadline = time.monotonic() + 4
            observed = self.tv.foreground()
            while observed != app_id and time.monotonic() < deadline:
                time.sleep(.1)
                observed = self.tv.foreground()
            state = self._status()
        except (TVError, OSError) as error:
            return {"ok": False, "error": str(error), "outcome": "unverified",
                    "acknowledged": result.get("returnValue") is True, "fallback_required": True}, None
        foreground_verified = state["app_id"] == app_id
        take_image = capture == "always" or capture == "auto" and (not known or not foreground_verified)
        witness = None
        if take_image:
            time.sleep(.8)  # First visual discovery needs one settled frame, not a capture after every key.
            image, limitation = self._capture(True)
            if limitation:
                state["limitations"].append(limitation)
            if image:
                witness = hashlib.sha256(image[0]).hexdigest()
                self.last_witness = {"id": witness, "metadata": metadata, "operation": operation,
                                     "foreground_verified": foreground_verified, "scope": self.learning.scope}
        self._remember_visual(image, state)
        return {"ok": foreground_verified, "acknowledged": result.get("returnValue") is True,
                "app_id": app_id, "operation": operation, "state": state,
                "app_verified": foreground_verified, "content_verified": False,
                "route_previously_verified": known, "generated_addon_used": addon is not None,
                "fallback_required": not known or not foreground_verified,
                "witness_id": witness, "elapsed_ms": round((time.perf_counter()-started)*1000, 1),
                "session_metrics": dict(self.tv.metrics),
                "metrics": {k: self.tv.metrics[k]-before[k] for k in before}}, image

    def learn(self, witness_id, notes=""):
        witness = self.last_witness
        if not witness or witness["id"] != witness_id or not witness["foreground_verified"]:
            raise TVError("Learning requires the latest successful app action screenshot; inspect it first")
        self.ready()
        if self.learning.scope != witness["scope"]:
            raise TVError("TV or firmware changed after the screenshot")
        current = next((app for app in self.tv.app_inventory() if app["id"] == witness["metadata"]["id"]), None)
        if current != witness["metadata"] or self.tv.foreground() != current["id"]:
            raise TVError("App identity, version or foreground changed after the screenshot")
        result = self.learning.learn(current, witness["operation"], witness_id, notes, witness.get('recipe'))
        if witness.get('recipe'):
            self.ui_context = {'state':witness['recipe']['to_state'],'app':current,
                               'scope':self.learning.scope,'at':time.monotonic(),'visual_witness_id':witness_id}
        self.last_witness = None
        return result

    def primitive(self, method, args, capture="never"):
        self._policy(capture)
        self.ready()
        self.last_witness = None
        self.ui_context = self.visual = None
        if method not in {"press", "move", "click", "scroll", "text"}:
            raise TVError("Unsupported primitive")
        try:
            result = getattr(self.tv, method)(*args)
        except (TVError, OSError) as error:
            try:
                self.ready()
                state = self._status()
            except (TVError, OSError):
                state = {"app_id": None}
            image, _ = self._capture(capture != "never")
            return {"ok": False, "error": str(error), "outcome": "unverified", "state": state,
                    "fallback_required": True}, image
        state = self._status()
        # Direct primitive requests are explicit. Screen-based decision-making requires observe(always).
        inventory = self.tv.app_inventory() if capture == "auto" else []
        app = next((a for a in inventory if a["id"] == state["app_id"]), None)
        wanted = capture == "always" or capture == "auto" and not (app and self.learning.known(app, "launch"))
        image, limitation = self._capture(wanted)
        if limitation:
            state["limitations"].append(limitation)
        self._remember_visual(image, state)
        return {"action": result, "state": state, "content_verified": False,
                "navigation_target_known": False,"visual_witness_id":self.visual['id'] if image else None}, image

    def _remember_visual(self, image, state):
        if image:
            self.ui_context = None
            self.visual = {'id':hashlib.sha256(image[0]).hexdigest(),'app_id':state['app_id'],
                           'scope':self.learning.scope,'at':time.monotonic()}

    def ui_anchor(self, witness_id, state):
        self.ready()
        if not isinstance(state,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,59}',state):
            raise TVError('UI state needs a short lowercase identifier')
        visual=self.visual
        if not visual or visual['id']!=witness_id or time.monotonic()-visual['at']>30 or visual['scope']!=self.learning.scope:
            raise TVError('Anchor requires an inspected screenshot from the last 30 seconds')
        app=next((a for a in self.tv.app_inventory() if a['id']==visual['app_id']),None)
        if not app or self.tv.foreground()!=app['id']:
            raise TVError('Foreground changed after visual observation')
        self.ui_context={'state':state,'app':app,'scope':self.learning.scope,'at':time.monotonic(),'visual_witness_id':witness_id}
        return {'anchored':True,'state':state,'app_id':app['id'],'witness_id':witness_id}

    def _ui_origin(self, app, state):
        context=self.ui_context
        return bool(context and context['app']==app and context['state']==state and
                    context['scope']==self.learning.scope and time.monotonic()-context['at']<=60 and
                    self.tv.foreground()==app['id'])

    def ui_record(self, name, from_state, to_state, steps, value=''):
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',name):
            raise TVError('UI route needs a short lowercase name')
        if not all(isinstance(s,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,59}',s) for s in [from_state,to_state]):
            raise TVError('UI route requires named origin and destination states')
        self.ready()
        context=self.ui_context
        if not context or not context.get('visual_witness_id') or not self._ui_origin(context['app'],from_state):
            raise TVError('Observe and anchor the origin before recording a UI route')
        recipe={'from_state':from_state,'to_state':to_state,'steps':steps,
                'origin_witness_sha256':context['visual_witness_id']}
        return self._ui_execute(context['app'],'ui:'+name,recipe,value,record=True)

    def ui_run(self, app_id, name, value=''):
        self.ready()
        app=next((a for a in self.tv.app_inventory() if a['id']==app_id),None)
        if not app: raise TVError('App is not installed')
        operation='ui:'+name
        recipe=self.learning.recipe(app,operation) if self.learning.known(app,operation) else None
        if not recipe or not self._ui_origin(app,recipe['from_state']):
            self.ui_context=self.last_witness=None
            state=self._status(); image,limitation=self._capture(True);self._remember_visual(image,state)
            return {'ok':False,'executed':False,'fallback_required':True,'required_state':recipe['from_state'] if recipe else 'route_discovery',
                    'state':state,'visual_witness_id':self.visual['id'] if image else None,'limitation':limitation},image
        return self._ui_execute(app,operation,recipe,value,record=False)

    def _ui_execute(self, app, operation, recipe, value, record):
        TV.validate_ui_steps(recipe['steps'],value)
        self.ui_context=self.visual=self.last_witness=None
        started=time.perf_counter()
        try:
            addon=self.learning.addon(app,operation) if not record else None
            result=addon(self.tv,value) if addon else self.tv.run_ui_steps(app['id'],recipe['steps'],value)
            state=self._status(); valid=state['app_id']==app['id']
            image,limitation=self._capture(record or not valid)
        except (TVError,OSError) as error:
            try:
                self.ready();state=self._status();image,_=self._capture(True)
                self._remember_visual(image,state)
            except (TVError,OSError):
                state={'app_id':None};image=None
            return {'ok':False,'executed':True,'error':str(error),'outcome':'uncertain; no replay',
                    'fallback_required':True,'state':state,'visual_witness_id':self.visual['id'] if image else None},image
        self._remember_visual(image,state)
        witness=self.visual['id'] if image else None
        if record and valid and image:
            self.last_witness={'id':witness,'metadata':app,'operation':operation,'foreground_verified':True,
                               'scope':self.learning.scope,'recipe':recipe}
        elif not record and valid:
            self.ui_context={'state':recipe['to_state'],'app':app,'scope':self.learning.scope,'at':time.monotonic()}
        return {'ok':valid,'executed':True,'action':result,'state':state,'content_verified':False,
                'tracked_state':recipe['to_state'] if not record and valid else None,'generated_addon_used':bool(addon),
                'witness_id':witness,'fallback_required':record or not valid,'limitation':limitation,
                'elapsed_ms':round((time.perf_counter()-started)*1000,1)},image
