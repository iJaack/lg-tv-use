"""Compile witnessed native app routes into small, local Python addons."""
from __future__ import annotations
import hashlib
import types
import json
import os
from pathlib import Path
import re
import tempfile
import time
import fcntl

from tv_use import TVError, TV

OPERATIONS = {"launch", "search", "open_url", "youtube_video"}

def supported_operation(operation):
    return operation in OPERATIONS or bool(re.fullmatch(r'ui:[a-z][a-z0-9_]{0,39}', operation))


def fingerprint(metadata):
    keys = ("id", "version", "type", "inAppSearchParams", "deeplinkingParams")
    return hashlib.sha256(json.dumps({k: metadata.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AppLearning:
    def __init__(self, root: Path):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / "app-profiles.json"
        self.code_path = root / "generated_addons.py"
        self.scope = None
        self.module_hash = None
        self.module = None
        self.data_stamp = None
        self.data_cache = None

    def read(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return {"schema": 1, "devices": {}}
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp != self.data_stamp:
            data = json.loads(self.path.read_text())
            if data.get("schema") != 1 or not isinstance(data.get("devices"), dict):
                raise TVError("Unsupported app profile format")
            self.data_cache, self.data_stamp = data, stamp
        return self.data_cache

    def bind(self, host: str, model: str, firmware: str):
        self.scope = "|".join([host, model, firmware])

    def known(self, metadata, operation):
        app = self.read().get("devices", {}).get(self.scope, {}).get(metadata["id"], {})
        route = app.get("operations", {}).get(operation, {})
        return app.get("fingerprint") == fingerprint(metadata) and route.get("verified") is True

    def learn(self, metadata, operation, witness_sha256: str, notes: str = "", recipe=None):
        if not self.scope or not supported_operation(operation) or not re.fullmatch(r"[a-f0-9]{64}", witness_sha256):
            raise TVError("Learning requires a bound device, supported native operation and screenshot witness")
        if not isinstance(notes, str) or len(notes) > 500:
            raise TVError("Learning notes must be a string of at most 500 characters")
        if operation.startswith('ui:'):
            if not isinstance(recipe,dict) or not all(isinstance(recipe.get(key),str) and
                    re.fullmatch(r'[a-z][a-z0-9_]{0,59}',recipe[key]) for key in ['from_state','to_state']):
                raise TVError('UI route requires a witnessed recipe with named states')
            TV.validate_ui_steps(recipe.get('steps'),'validation')
        lock = self.path.with_suffix(".lock")
        with lock.open("a+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            data = self.read()
            device = data.setdefault("devices", {}).setdefault(self.scope, {})
            app = device.get(metadata["id"], {})
            if app.get("fingerprint") != fingerprint(metadata):
                app = {"title": metadata.get("title"), "version": metadata.get("version"),
                       "fingerprint": fingerprint(metadata), "operations": {}}
            app["operations"][operation] = {"verified": True, "witness_sha256": witness_sha256,
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "notes": notes}
            if operation.startswith('ui:'):
                app['operations'][operation]['recipe'] = recipe
            device[metadata["id"]] = app
            atomic_write(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            self._compile(data)
        return {"learned": True, "app_id": metadata["id"], "operation": operation,
                "version": metadata.get("version"), "addon_file": str(self.code_path)}

    def _compile(self, data):
        code = ['"""Generated from witnessed app operations. Rebuilt automatically by AppLearning."""', ""]
        mappings = []
        for scope, apps in sorted(data.get("devices", {}).items()):
            for app_id, app in sorted(apps.items()):
                for operation, route in sorted(app.get("operations", {}).items()):
                    if not supported_operation(operation) or route.get("verified") is not True:
                        continue
                    suffix = hashlib.sha256((scope + app_id + operation).encode()).hexdigest()[:10]
                    name = "app_" + re.sub(r"[^a-z0-9_]", "_", app_id.lower()) + "_" + operation.replace(':','_') + "_" + suffix
                    call = (f"tv.run_ui_steps({app_id!r}, {route['recipe']['steps']!r}, value)" if operation.startswith('ui:')
                            else f"tv.native_app_action({app_id!r}, {operation!r}, value)")
                    code += [f"def {name}(tv, value=''):", f"    return {call}", ""]
                    mappings.append((scope, app_id, operation, app["fingerprint"], name))
        code.append("ADDONS = {")
        for scope, app_id, operation, digest, name in mappings:
            code.append(f"    {(scope, app_id, operation, digest)!r}: {name},")
        code += ["}", ""]
        atomic_write(self.code_path, "\n".join(code))

    def addon(self, metadata, operation):
        if not self.known(metadata, operation) or not self.code_path.exists():
            return None
        source = self.code_path.read_bytes()
        code_hash = hashlib.sha256(source).hexdigest()
        if code_hash != self.module_hash:
            module = types.ModuleType("lg_tv_generated_addons")
            # Read the exact hash-checked source; timestamp-based pyc caches can be stale.
            exec(compile(source, str(self.code_path), "exec"), module.__dict__)
            self.module, self.module_hash = module, code_hash
        return self.module.ADDONS.get((self.scope, metadata["id"], operation, fingerprint(metadata)))

    def capabilities(self, inventory):
        result = []
        for app in inventory:
            stored = self.read().get('devices',{}).get(self.scope,{}).get(app['id'],{}).get('operations',{})
            operations = [op for op in sorted(set(OPERATIONS)|set(stored)) if self.known(app, op)]
            advertised = ["launch"] if app.get("type") != "stub" else []
            if app.get("inAppSearchParams"):
                advertised.append("search")
            if app["id"] == "youtube.leanback.v4":
                advertised.append("youtube_video")
            if app["id"] == "com.webos.app.browser":
                advertised.append("open_url")
            result.append({"app_id": app["id"], "title": app.get("title"), "version": app.get("version"),
                "learned": operations, "needs_discovery": [op for op in advertised if op not in operations],
                "ui_routes":[op[3:] for op in operations if op.startswith('ui:')],
                "ui_discovery_required":not any(op.startswith('ui:') for op in operations),
                "launch_evidence":stored.get('launch',{}).get('notes') if self.known(app,'launch') else None,
                "installation_required": app.get("type") == "stub"})
        return result

    def recipe(self, metadata, operation):
        if not self.known(metadata, operation):
            raise TVError('UI recipe is unknown for this TV and app version')
        return self.read()['devices'][self.scope][metadata['id']]['operations'][operation].get('recipe')
