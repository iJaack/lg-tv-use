# Compatibility and validation

The release separates implementation, mock regression tests and physical TV evidence.
Private captures, network addresses, credentials and generated home profiles are not
included. Contributors should publish sanitized summaries rather than raw captures.

## Physical checks before the public release

| Device | Evidence | Limits |
| --- | --- | --- |
| LG 65QNED826QB | Pairing, capture, remote/native app routes and YouTube search/video tested in the preceding prototype. | The later pairing retry was rejected; the new multi-device power cycle was not verified on this model. |
| LG 55NANO866NA | Pairing and model/MAC registration; native power transitioned Active → Active Standby → Active. YouTube native search and matching results visually checked. | Wake used WOL plus an available native handshake, so WOL alone was not isolated. |

On the second device, 23 non-stub app launches were attempted and foreground-checked;
21 received inspected launch captures. Many screens were profiles, consents or
loading logos. These do not establish complete in-app coverage. Six internal recipes
were visually recorded in Netflix/Stremio, but broad reliable replay across every
screen was not established.

A replay counterexample showed that the same focused sidebar item on two different
underlying pages is not the same origin. State labels were corrected and a regression
guard was added. That is evidence for the guard, not a claim that every UI recipe is
reliable. External physical remote use was also present during some exploration.

One local warm YouTube search run used generated native wrappers, one retained
connection and zero image blocks, with a median around 0.56 seconds for command and
readback. This excludes UI loading, network variation and model reasoning; it is
not a universal latency guarantee or a benchmark for internal UI batches.

The public 0.3.1 preparation changes configuration, branding, examples, packaging
and documentation. It does not rerun household TV navigation or power tests.

## Automated checks

The unittest suite covers private-target validation, certificate pinning before
credential transmission, pairing failure/model mismatch, atomic credential writes,
bounded input/downloads, app-version invalidation, generated-code escaping and
reload, screenshot witnesses, UI origin/expiry checks, no uncertain mutation replay,
pointer preflight, device identity, WOL packets and native power verification.

Configuration regressions check that no TV is targeted implicitly, explicit host
overrides work, discovery needs no configured TV, custom state storage is consistent
and missing CLI configuration produces an actionable error. A real stdio MCP
subprocess verifies the 17-tool schema and an empty local device registry without
connecting to any TV.

CI runs these checks on Python 3.11/3.14, macOS/Linux; it cannot certify a physical
TV, every webOS firmware, app release, account state or network standby setting.

## Contributor acceptance checklist

For a new native route or UI recipe, record model, firmware availability, app version,
exact origin and result, capture availability and what native readback proves. Test
repeated replay only from the correct observed/tracked state; include a stale/version
or alternate-page counterexample. Keep power-off and power-on readbacks distinct.
Never publish device MACs, IP inventory, account names, private images or pairing keys.
