"""Comfy V3 nodes. The cloud result is a native FILE3DGLB object."""
import json

import folder_paths
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar
from comfy_api.latest import IO, Types
from server import PromptServer

from .assets import encode_image
from .client import CormakError, LINKS, MODEL, QUALITIES
from .generation import download_result, generate, metadata, status
from .runtime import account


def runtime():
    return PromptServer.instance.cormak_nodes_runtime


def encode_metadata(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def model_outputs():
    return [
        IO.File3DGLB.Output(display_name="GLB"),
        IO.String.Output(display_name="file_path"),
        IO.String.Output(display_name="job_id"),
        IO.String.Output(display_name="metadata_json"),
    ]


def result_output(path, info):
    return IO.NodeOutput(Types.File3D(str(path), "glb"), str(path), info["job_id"], encode_metadata(info))


def generation_inputs():
    return [
        IO.Combo.Input("model", options=[MODEL], tooltip="The model provided by Cormak 3D Studio."),
        IO.Combo.Input("quality", options=list(QUALITIES), tooltip="Review the current quote in the Cormak sidebar."),
        IO.Int.Input("credit_cap", default=0, min=0, max=1_000_000,
                     tooltip="Maximum AI credits you accept per new run. Zero cannot start a generation."),
        IO.Int.Input("run_number", default=0, min=0, max=2147483647,
                     tooltip="Keep this number when retrying. Change it deliberately to create another paid generation."),
    ]


class CormakNode(IO.ComfyNode):
    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # The Comfy process and its cache are shared by browser tabs. Never use a
        # cached account/status/model as a substitute for an authorized cloud call.
        return float("nan")


async def run_generation(arguments, credit_cap, run_number):
    app = runtime()
    binding = app.current()
    bar = ProgressBar(100)

    def check():
        throw_exception_if_processing_interrupted()
        if app.current() is not binding:
            raise CormakError("session_expired")

    job, value = await generate(app, binding, arguments, credit_cap, run_number,
                                check=check, progress=bar.update_absolute)
    check()
    path, info = await download_result(app, binding.session, job, value, folder_paths.get_temp_directory())
    return result_output(path, info)


class CormakTextTo3D(CormakNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CormakTextTo3D", display_name="Cormak Text to 3D", category="Cormak",
            search_aliases=["text to 3d", "generate glb", "ai 3d generator"],
            description="Generate a textured GLB from a description using your Cormak AI credits.",
            inputs=[
                IO.String.Input("prompt", multiline=True, default="",
                                tooltip="Describe one object in 10–600 characters."),
                *generation_inputs(),
            ], outputs=model_outputs(),
        )

    @classmethod
    async def execute(cls, prompt, model, quality, credit_cap, run_number):
        if not isinstance(prompt, str):
            raise CormakError("invalid_prompt")
        prompt = prompt.strip()
        try:
            units = len(prompt.encode("utf-16-le")) // 2
        except UnicodeError:
            raise CormakError("invalid_prompt") from None
        if not 10 <= units <= 600:
            raise CormakError("invalid_prompt")
        if model != MODEL or quality not in QUALITIES:
            raise CormakError("invalid_request")
        return await run_generation({"input_mode": "prompt", "prompt": prompt, "quality": quality},
                                    credit_cap, run_number)


class CormakImageTo3D(CormakNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CormakImageTo3D", display_name="Cormak Image to 3D", category="Cormak",
            search_aliases=["image to 3d", "photo to 3d", "3d model generator"],
            description="Generate a textured GLB from a single standard Comfy IMAGE.",
            inputs=[IO.Image.Input("image"), *generation_inputs()], outputs=model_outputs(),
        )

    @classmethod
    async def execute(cls, image, model, quality, credit_cap, run_number):
        runtime().current()
        if model != MODEL or quality not in QUALITIES:
            raise CormakError("invalid_request")
        # CUDA/MPS tensor conversion stays on the Comfy executor thread.
        payload = encode_image(image)
        return await run_generation({"input_mode": "photo", "images": [payload], "quality": quality},
                                    credit_cap, run_number)


class Cormak3DResult(CormakNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="Cormak3DResult", display_name="Cormak 3D Result", category="Cormak",
            description="Download an existing Cormak job and save its unmodified GLB to output/Cormak.",
            inputs=[IO.String.Input("job_id"), IO.String.Input("filename_prefix", default="model")],
            outputs=model_outputs(), is_output_node=True,
        )

    @classmethod
    async def execute(cls, job_id, filename_prefix):
        session = runtime().current().session
        if not isinstance(filename_prefix, str) or len(filename_prefix) > 256:
            raise CormakError("invalid_request")
        value = await status(session, job_id)
        path, info = await download_result(runtime(), session, job_id, value,
                                           folder_paths.get_output_directory(), prefix=filename_prefix)
        return result_output(path, info)


class CormakGenerationStatus(CormakNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CormakGenerationStatus", display_name="Cormak Generation Status", category="Cormak",
            description="Look up a job without spending credits. Send its job ID to Cormak 3D Result.",
            inputs=[IO.String.Input("job_id")], is_output_node=True,
            outputs=[
                IO.String.Output(display_name="job_id"), IO.String.Output(display_name="status"),
                IO.Int.Output(display_name="progress"), IO.String.Output(display_name="metadata_json"),
            ],
        )

    @classmethod
    async def execute(cls, job_id):
        session = runtime().current().session
        info = metadata(job_id, await status(session, job_id))
        encoded = encode_metadata(info)
        return IO.NodeOutput(job_id, info["status"], info["progress"], encoded, ui={"text": [encoded]})


class CormakAccount(CormakNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CormakAccount", display_name="Cormak Account", category="Cormak",
            description="Read account status and AI credit balance. Connect or disconnect in the Cormak sidebar.",
            is_output_node=True,
            outputs=[
                IO.Boolean.Output(display_name="connected"), IO.Int.Output(display_name="ai_credits"),
                IO.String.Output(display_name="plan"), IO.String.Output(display_name="create_account_url"),
                IO.String.Output(display_name="manage_credits_url"),
            ],
        )

    @classmethod
    async def execute(cls):
        binding = runtime().current(required=False)
        data = await account(binding.session) if binding else {"connected": False, "credits": 0, "plan": ""}
        return IO.NodeOutput(data["connected"], data["credits"], data["plan"], LINKS["register"], LINKS["credits"],
                             ui={"text": [encode_metadata(data)]})


NODES = [CormakTextTo3D, CormakImageTo3D, Cormak3DResult, CormakAccount, CormakGenerationStatus]
