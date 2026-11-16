# Comfy Registry listing draft and search research

Prepared against official ComfyUI 0.45.0 source and Registry documentation on
16 November 2026. This is a proposed listing, not a published URL.

**Display name:** Cormak Nodes  
**Package identifier:** cormak-nodes (proposed; recheck before publication)  
**Publisher:** owner to confirm in Registry  
**Category in ComfyUI:** Cormak  
**Description:** Text to 3D and Image to 3D in ComfyUI. Create textured GLB assets
with your Cormak account, preview them and use them in 3D workflows.

Long copy belongs in the README:

> Create textured 3D assets from a description or reference image in ComfyUI.
> Connect your existing Cormak account, check the AI credit cost, generate a GLB,
> and send it to native preview, save and mesh-processing nodes. The extension
> is free to install; cloud generation uses Cormak AI credits.

## What the Registry supports

The documented package metadata has name, description, version, license,
repository URLs, dependencies, Python requirement, classifiers, publisher,
display name, icon, banner and ComfyUI version requirement. It does not document
a free-form SEO keyword/tag field or a screenshot-gallery field in pyproject.
Use meaningful README headings, actual node names and concise descriptions.
The Manager searches node packs and individual nodes.

Relevant natural search phrases include Text to 3D, Image to 3D, AI 3D Generator,
3D Model Generator, GLB Generator, AI Mesh, 3D Assets, ComfyUI 3D and Generative 3D.
No search-volume dataset was available; these are functional vocabulary,
not measured rankings or popularity claims.

## Official competitors and the practical difference

| Integration | Observed API/node structure | Implication for Cormak |
| --- | --- | --- |
| Meshy built-in partner nodes | Text, image and multiview generation, refinement, GLB/FBX outputs and provider task IDs | State explicitly that Cormak currently exposes Meshy 7.1 and GLB, rather than suggesting a new underlying model |
| Rodin built-in partner nodes | Single/multiview model generation and several quality modes; newer Gen-2.5 text/image nodes use native 3D file outputs | Show real quality presets and a native preview workflow, rather than claiming arbitrary 3D-node compatibility |
| Tripo built-in partner nodes | Text/image/multiview generation, model task IDs, GLB/FBX and further asset operations | Keep the first Cormak release focused on its existing 3D contract, receipt and safe retries |
| Local Hunyuan3D examples | Locally loaded model weights and mesh workflows | Explain that Cormak is cloud generation through its existing account/credits and does not require local weights |

Cormak's useful distinction is its existing account and shared credit balance,
an advance quote and approval controls, and a durable job identity across
workflow retries. Do not claim better generation quality than those providers,
free cloud generation, exclusive 3D technology or local/offline inference.

## Attribution without hidden telemetry

- Sidebar account links disclose `utm_source=comfyui`,
  `utm_medium=integration` and `utm_campaign=cormak_nodes`.
- Registration attribution can use Cormak's existing website acquisition
  mechanism when the user enters through these links.
- OAuth registrations identify the client as **Cormak Nodes for ComfyUI**.
  Connections and generation starts can be counted server-side through existing
  client/grant/call/audit records without adding local event transmission.
- Paid conversion attribution requires a server-side cohort report using existing
  account acquisition and billing records. This package does not claim that
  report or a complete conversion dashboard is already implemented.
- The current OAuth server does not forward UTM parameters from authorization to
  its consent page. Direct Connect sign-ins therefore support client/grant
  attribution, but not guaranteed website registration campaign attribution.
  Improving that requires a separately reviewed server change.
- There are no node-start tracking pixels, analytics SDKs or background pings.
  Do not add these without clear disclosure and any required consent.

Sources:
[Meshy source](https://github.com/Comfy-Org/ComfyUI/blob/v0.45.0/comfy_api_nodes/nodes_meshy.py),
[Rodin source](https://github.com/Comfy-Org/ComfyUI/blob/v0.45.0/comfy_api_nodes/nodes_rodin.py),
[Rodin guide](https://docs.comfy.org/tutorials/api-nodes/rodin/model-generation),
[Tripo source](https://github.com/Comfy-Org/ComfyUI/blob/v0.45.0/comfy_api_nodes/nodes_tripo.py),
[local 3D guide](https://docs.comfy.org/zh/tutorials/3d/hunyuan3D-2),
[Registry fields](https://docs.comfy.org/registry/specifications),
[Manager search](https://docs.comfy.org/manager/pack-management).
