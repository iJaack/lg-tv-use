# Security policy

This is an experimental local automation tool. Only the current public release is
maintained; there is no security response SLA. Use it on trusted devices and networks.

## Reporting

Report vulnerabilities privately through GitHub's **Report a vulnerability** option
in this repository's Security tab. Do not open a public issue containing a working
exploit, pairing key or private capture. Include affected version, impact, minimal
reproduction and a sanitized proof. General feature/compatibility bugs can use issues.

## Boundaries

- TV targets must be RFC1918 IPv4. Control uses TLS and pins the TV certificate on
  explicit first pairing (trust on first use, not a public CA verification).
- First pairing assumes you have verified the TV on a trusted LAN. Existing pins
  are checked before credentials are sent. A changed certificate fails closed;
  pairing again does not silently discard the pin.
- An intentional pin reset requires verifying the physical TV and removing the
  relevant local credential file, including the selected registered-device copy
  when applicable. Do not delete all state or reset pins just to suppress an error.
- Pointer commands use the same pinned encrypted listener. Capture URLs are restricted
  to the selected TV, with no redirects and an 8 MiB cap. A TV-provided HTTP image
  URL is plaintext LAN traffic; HTTPS downloads must match the stored pin.
- The MCP transport is stdio, with no inbound host server. Images, metadata and tool
  results are returned to the MCP client; that client may use a remote model.
- Credentials and registry/addon files are trusted local state. Credential files are
  0600 and their directory is 0700. Generated code is loaded locally: a process able
  to edit that code can influence code execution. Do not load other people's generated
  addons or unreviewed state files as trusted configuration.
- MCP exposes bounded commands, not arbitrary shell or SSAP/Luna endpoints. Agent
  guidance limits consent, account and purchase operations, but there is no semantic
  policy engine that recognizes all effects of a key sequence or remote page.
- Known route replay predicts UI state; it is not authentication or a focus guarantee.
  Protected content, external remote input and asynchronous UI can invalidate assumptions.
- Uncertain commands are never automatically replayed. Native power state is checked;
  loss of connectivity does not establish physical power-off.

Never commit credential files, local profiles, MAC inventories, screenshots or raw
receipts. They are excluded from this repository's public source and should remain
excluded from forks and issue reports.
