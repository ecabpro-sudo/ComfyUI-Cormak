"""Run a separately installed ComfyUI on loopback with a fixture transport."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys

from fixture import FakeCormak

parser = argparse.ArgumentParser()
parser.add_argument("comfy_dir", type=Path)
parser.add_argument("scratch", type=Path)
parser.add_argument("--port", type=int, default=8189)
args = parser.parse_args()
comfy = args.comfy_dir.resolve(strict=True)
base = args.scratch.resolve()
package = Path(__file__).resolve().parents[1]
base.mkdir(parents=True, exist_ok=True)
for folder in ("input", "output", "user"):
    (base / folder).mkdir(exist_ok=True)

# This script is an isolated QA harness. It leaves the developer's own
# ComfyUI paths untouched and never loads their other custom nodes.
config = base / "paths.yaml"
config.write_text(json.dumps({"fixture": {"base_path": str(package.parent), "custom_nodes": "."}}))
sys.path.insert(0, str(comfy))
sys.argv = [str(comfy / "main.py"), "--listen", "127.0.0.1", "--port", str(args.port),
            "--cpu", "--disable-partner-nodes", "--disable-assets", "--disable-auto-launch",
            "--disable-metadata", "--input-directory", str(base / "input"),
            "--output-directory", str(base / "output"), "--user-directory", str(base / "user"),
            "--temp-directory", str(base), "--extra-model-paths-config", str(config),
            "--disable-all-custom-nodes", "--whitelist-custom-nodes", package.name]
os.environ.pop("CORMAK_PUBLIC_ORIGIN", None)
import comfy.options
comfy.options.enable_args_parsing()
import main

loop, server, start = main.start_comfyui()
runtime = server.cormak_nodes_runtime
namespace = type(runtime).__module__.rsplit(".", 1)[0]
client = importlib.import_module(namespace + ".client")
generation = importlib.import_module(namespace + ".generation")
generation.POLL_SECONDS = .03
fake = FakeCormak(client, base / "fixture_report.json")
runtime.transport, runtime.asset_fetch = fake, fake.download
(base / "input" / "cormak-reference.png").write_bytes(fake.image)
try:
    loop.run_until_complete(start())
except KeyboardInterrupt:
    pass
