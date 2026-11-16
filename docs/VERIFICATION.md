# Verification of 0.1.0 candidate

Local QA on macOS arm64, ComfyUI **0.45.0**, frontend **1.58.5**,
Python **3.13**, CPU execution and Chromium/Chrome. Only Cormak's transport and
download bytes are substituted; the actual ComfyUI server, queue, execution
engine, V3 loader, IMAGE conversion, frontend, 3D viewers and save nodes run.

## Passed

- 31 automated Python contract/security checks.
- Five nodes load through the real V3 extension entrypoint and expose category Cormak.
- Browser panel connection through PKCE parameters and a synthetic OAuth callback;
  balance and live server quote display; disconnect attempts server revocation.
- Text generation pauses for approval, then reuses its original UUID after approval.
- Text and IMAGE workflows execute, receive a real GLB-format sample, render it
  in Preview 3D (Advanced) and Save 3D Model, and save the file to output.
- Requeuing the identical generation does not submit another paid job.
- Generation Status → 3D Result → Get 3D Components → Save 3D Model executes.
  The core mesh parser reads 284 vertices and 142 triangles from the sample.
- No browser page errors in the workflow run.
- No bearer/refresh token or browser cookie in returned workflow/history data.
- Durable request identity, lost start response recovery, ambiguous refresh
  invalidation, single refresh under concurrency, failed revoke, refund receipt,
  low balance, zero credit cap and interrupted execution.
- Cross-origin/missing-header control requests, invalid Host, invalid/expired/replayed
  OAuth state and unrelated browser sessions are rejected.
- Public binds and Comfy multi-user mode are disabled.
- URL allowlist, GLB structural checks/external-resource rejection, IMAGE shape
  validation, filename sanitation, symlink rejection and unique no-overwrite saving.
- Cormak native and Landing TypeScript checks pass.
- EN/RU integration page metadata, catalogue links and prepared workflow screenshots.

The screenshots show an intentionally simple **test lantern** and an illustrated
reference image produced by the fixture. They are real screenshots of ComfyUI,
not examples of quality from a cloud model. No Meshy task or real credit debit
is involved.

## Coverage limits and release checks

No real Cormak password, token, paid provider call or production database write
was used. Only public OAuth metadata was checked against production; it advertises
PKCE S256 and authorization-code/refresh grants. Device Authorization Flow is not
advertised, so this package uses browser OAuth and a private callback.

These remain unverified: production registration/login and consent; actual
generation and billing/refunds through the live account; revocation semantics of
the deployed service; Windows/Linux runtime; remote SSH/proxy end-to-end setup;
newer ComfyUI/frontend releases; real cloud texture/material fidelity.

The built-in 3D/V3 APIs in this release are official but still marked experimental.
Pinning ComfyUI 0.45.x and exercising native File3D is deliberate; this is not a
promise of a fully stable API across future releases. The registry range is
restricted accordingly.

The QA installation omits the optional large global workflow-template media
packages; its missing-global-template warning does not affect the shipped Cormak
examples, core node loading or execution. Users should follow ComfyUI's complete
installation guide for their own installation.

The released package is published at
`https://github.com/ecabpro-sudo/ComfyUI-Cormak` and under Registry publisher
`@cormak`. The local fixture QA still does not substitute for production
OAuth, billing, provider quality, Windows/Linux, or remote proxy checks.

## Reproduce

Use a separate ComfyUI checkout and its Python environment:

```sh
python tests/run.py /absolute/path/to/ComfyUI
python tests/serve_fixture.py /absolute/path/to/ComfyUI /absolute/path/to/qa-scratch
```

The fixture starts on loopback port 8189. In another terminal, use a development
Node.js environment with `@playwright/test` and Chrome installed:

```sh
node tests/browser.cjs http://127.0.0.1:8189 /absolute/path/to/qa-scratch
```

The browser runner intercepts synthetic authorization/approval URLs. It is only
for `serve_fixture.py`; do not use it as a production acceptance test. Python
tests use temporary SQLite files and can run without any account or API secrets.
