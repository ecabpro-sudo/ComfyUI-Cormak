# Security and privacy

The browser owns an opaque HttpOnly cookie. The server hashes it to find a
browser-specific OAuth session. A prompt middleware captures that identity and
a prompt hook replaces the queue ID with a server-minted UUID. Node execution
looks up that queue ID through Comfy's executing context. Cookie values, tokens
and account selectors are never graph inputs or prompt extra_data.

Tokens are memory-only. The only persisted state is public OAuth registration
IDs and request hashes, UUIDs, accepted maximum credit prices and session epochs.
That journal is not an alternate wallet. A payload hash is scoped to the browser.
It contains no original text/image, private asset URL or bearer token.

Requests from unrelated browser origins, missing custom control headers and
unexpected Host values are rejected. Authorization callbacks have no custom
header because the browser follows an OAuth redirect; they require exact origin,
cookie, state and expiry, and consume state before exchange. HTTP identity is
not inferred from a frontend client_id or Comfy profile ID.

All network destinations are fixed Cormak endpoints. Only the returned private
Cormak Storage model URL is accepted for downloads; its bearer is the signed
URL itself. No OAuth Authorization header is sent to storage. Files are bounded
and checked for GLB 2 structure, matching lengths and embedded resources.
Temporary downloads use exclusive creation; saved files use unique names and
no-clobber publication. The server's temp/output roots are administrator-trusted.
A local attacker who can modify the process or replace directories concurrently
is outside this boundary.

No token file, OS keychain dependency, service-role key, provider key, client
secret or password is shipped. Refresh responses are validated. Refresh is
serialized across the server and executor loops, and lost responses cause
reconnection instead of a replay. Upstream response bodies and exception strings
are not exposed in node errors.

ComfyUI's shared history/files are not isolated per user. Use a dedicated private
instance; the extension refuses --multi-user, non-loopback listen addresses and
offline mode. An authenticated HTTPS proxy must preserve the configured Host.
Do not log OAuth callback query strings at that proxy. Comfy itself disables its
HTTP access log, but the proxy administrator must configure theirs.

There is no hidden analytics client. Click attribution uses clearly documented
UTM parameters. Cormak receives the generation input and account-authorized API
requests. Website registration/payment attribution follows its own consent and
privacy controls.

Report security issues privately to support@cormak.app. Do not include tokens,
cookies, signed URLs, passwords or private workflow content in issue trackers.
