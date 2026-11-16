# Owner-controlled publication

The 0.1.0 release is published from the standalone repository
`https://github.com/ecabpro-sudo/ComfyUI-Cormak` under Registry publisher
`@cormak`. There is no automatic publication on push; future releases remain
manual and require the publisher's hidden CLI prompt.

Nothing in this package automatically pushes code, publishes a Registry version
or deploys the Cormak website.

## Before publication

1. Authorize and complete production QA using an explicit test credit budget:
   existing account sign-in, new registration, balance, one text and one photo
   build, real approval, actual charge/refund receipts, reconnect and revocation.
   Repeat installation and file saving on Windows and Linux.
2. Confirm the public repository destination and Cormak Registry publisher
   account. Public endpoint checks for `cormak` and `cormak-nodes` returned 404
   during preparation; this is not proof of ownership, availability or reservation.
3. The owner publisher is `@cormak` (created and verified in the Registry).
4. With owner approval, create **ComfyUI-Cormak** on GitHub. Export only this
   directory, never the parent Cormak repository. Review the archive file list.
   Initialize and commit the standalone repository locally before any approved
   push. There are no automatic publishing workflows.

## Registry metadata

The [official metadata specification](https://docs.comfy.org/registry/specifications)
defines these fields:

| Field | Value / action |
| --- | --- |
| `project.name` | `cormak-nodes` — proposed immutable package identifier |
| `project.version` | `0.1.0` |
| `project.description` | Existing concise Text to 3D / Image to 3D description |
| `project.license` | MIT, referenced by `LICENSE` |
| `project.dependencies` | Empty: use ComfyUI's aiohttp, Pillow and PyTorch |
| `project.urls.Repository` | `https://github.com/ecabpro-sudo/ComfyUI-Cormak` |
| `tool.comfy.PublisherId` | `cormak` |
| `tool.comfy.DisplayName` | `Cormak Nodes` |
| `tool.comfy.Icon` | Existing official 128×128 Cormak logo URL |
| `tool.comfy.requires-comfyui` | `>=0.45.0,<0.46.0` for the tested candidate |

The publisher is intentionally blank and Repository is absent in the local
candidate. Do not invent or copy another publisher's identity. The empty
publisher makes this draft unsuitable for an accidental publish.

Do not add fictitious Marketplace tags or screenshots fields to pyproject.toml.
Use the README for detailed features, search terms and screenshots. The icon
follows the stricter square ≤400px requirement in the specification (the
publishing overview gives a different upper size).

## Manual CLI release

Use a dedicated environment for the official
[comfy-cli](https://docs.comfy.org/comfy-cli/getting-started). Confirm its installed
version and `comfy node publish --help` immediately before release. From the
standalone repository, verify metadata with:

```sh
python -c "import tomllib; from pathlib import Path; p=tomllib.loads(Path('pyproject.toml').read_text()); assert p['tool']['comfy']['PublisherId']; assert p['project']['urls']['Repository'].startswith('https://github.com/')"
git ls-files
```

Review `.comfyignore`. The CLI packages tracked files and applies this exclusion
file. Never run the command from the parent Cormak repository.

After **explicit owner authorization**, run:

```sh
comfy node publish
```

Enter the publisher's Registry API key in the CLI's hidden interactive prompt.
It is a publishing credential, unrelated to generation or Cormak OAuth.
Do not put it in commands, logs, workflow JSON or a tracked file.

Inspect the returned Registry URL, install that exact version in a clean
ComfyUI instance via Manager, and import every example. The release is not
verified merely because the upload command succeeded.

Then record real GitHub and Registry URLs in the website's release configuration
and documentation. The prepared website copy is an unpublished preview.
Deploy it only after owner approval and Cormak's current release-continuity
checks. No automatic publication on push is configured.

Sources:
[Registry publishing](https://docs.comfy.org/registry/publishing),
[metadata spec](https://docs.comfy.org/registry/specifications),
[Registry standards](https://docs.comfy.org/registry/standards),
[Manager](https://docs.comfy.org/manager/pack-management).
