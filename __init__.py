from comfy_api.latest import ComfyExtension
from server import PromptServer

from .nodes import NODES
from .runtime import Runtime

WEB_DIRECTORY = "./js"
__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]


class CormakExtension(ComfyExtension):
    async def get_node_list(self):
        return NODES


async def comfy_entrypoint():
    server = PromptServer.instance
    if not hasattr(server, "cormak_nodes_runtime"):
        runtime = Runtime(server)
        runtime.install()
        server.cormak_nodes_runtime = runtime
    return CormakExtension()
