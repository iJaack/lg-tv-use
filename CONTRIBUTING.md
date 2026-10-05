# Contributing

Issues and pull requests are welcome. Keep changes small and include the behavior
they fix, meaningful regression tests and any physical TV evidence needed to support
the claim. The project is experimental; additional webOS compatibility reports help.

## Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q tv_use.py tv_runtime.py tv_power.py app_learning.py tv_cli.py server.py tests
```

Automated tests use mocks and temporary local state. Do not add tests that discover,
pair, navigate or power-cycle a real TV as part of default CI. Physical testing should
be explicit and performed only on devices you are authorized to control.

## Change guidelines

- Reuse native advertised routes before adding UI sequences. Do not guess deep links
  and label them verified without observing the actual result.
- Keep remote input and generated templates bounded. Do not add arbitrary SSAP/Luna,
  shell execution, service menus or code supplied by TV content to MCP.
- Preserve device identity checks, pinning, capture restrictions and no uncertain
  mutation replay. App foreground is not content/focus verification.
- Test failures, stale versions, changed origins and reconnect behavior. Validate all
  steps before executing a batch; avoid tests that merely repeat the implementation.
- Update tool documentation and changelog when behavior or arguments change.
- Use the existing standard library and pinned dependencies where possible. Include
  a reason and compatibility check for dependency changes.

Inspect `git diff --cached` before committing. The repository deliberately excludes
local profiles, pairing keys, screenshots, output receipts and downloaded SDK sources.
Sanitize bug reports too; ignore rules cannot prevent copying a secret into a comment.

For reports include Python/OS version, public package version, TV model, app version,
reproduction steps, expected/actual result and sanitized error text. Do not attach
captures containing personal content. Security reports follow [SECURITY.md](SECURITY.md).

Please be respectful, specific and patient in discussions. Contributions are made
under the project's [MIT license](LICENSE). No CLA is required.
