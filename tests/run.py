"""Run contract/security tests against a separate installation of ComfyUI."""
import argparse
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import secrets
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("comfy_dir", type=Path)
settings = parser.parse_args()
sys.path.insert(0, str(settings.comfy_dir.resolve(strict=True)))
sys.argv = [str(Path(__file__)), "--cpu", "--disable-assets"]
import comfy.options
comfy.options.enable_args_parsing()

package = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cormak_under_test", package / "__init__.py",
                                            submodule_search_locations=[str(package)])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from cormak_under_test import assets, client, generation, nodes, runtime
from fixture import FakeCormak, examples, glb
from aiohttp import web, CookieJar
from aiohttp.test_utils import TestClient, TestServer
from comfy_execution.utils import CurrentNodeContext
import torch


class Contracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeCormak(client)
        self.fake.approval_by_default = False
        self.client_id = str(uuid4())
        self.fake.registrations[self.client_id] = "http://127.0.0.1:8188/cormak/oauth/callback"
        self.session = client.Session(self.client_id, self.fake)
        await self.session.exchange("cmcp_c_" + "c" * 43, "v" * 64, self.fake.registrations[self.client_id])
        self.binding = runtime.Binding("browser_hash", "epoch", self.session)
        self.journal = runtime.Journal(self.root / "state.sqlite3")
        self.app = SimpleNamespace(journal=self.journal, asset_fetch=self.fake.download)
        self.args = {"input_mode":"prompt", "prompt":"A small bronze lantern", "quality":"basic"}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def generate(self, cap=25, run=0, arguments=None):
        with patch.object(generation, "POLL_SECONDS", 0):
            return await generation.generate(self.app, self.binding, arguments or self.args, cap, run,
                                             check=lambda: None, progress=lambda x: None)

    async def test_text_download_and_reuse_no_second_charge(self):
        job, value = await self.generate()
        path, info = await generation.download_result(self.app, self.session, job, value, self.root)
        self.assertEqual(path.read_bytes(), self.fake.model)
        self.assertEqual(info["credits_charged"],25)
        self.assertNotIn("model_url", info)
        other_job, _ = await self.generate(cap=0)
        self.assertEqual(job, other_job)
        self.assertEqual(self.fake.stats["charged"], 1)

    async def test_image_payload_and_generation(self):
        image = torch.ones((1, 20, 40, 3)) * .5
        payload = assets.encode_image(image)
        with __import__("PIL.Image", fromlist=["Image"]).open(io.BytesIO(base64.b64decode(payload["data"]))) as pic:
            self.assertEqual((pic.mode, pic.size), ("RGB", (40,20)))
        _, value = await self.generate(arguments={"input_mode":"photo", "images":[payload], "quality":"basic"})
        self.assertEqual(value["status"], "succeeded")
        self.assertEqual(self.fake.stats["photos"], 1)

    async def test_cap_default_cannot_charge(self):
        with self.assertRaisesRegex(client.CormakError, "maximum"):
            await self.generate(cap=0)
        self.assertEqual(self.fake.stats["submitted"],0)

    async def test_insufficient_balance_does_not_submit(self):
        self.fake.balance = 2
        with self.assertRaises(client.CormakError) as exc:
            await self.generate()
        self.assertEqual(exc.exception.code, "insufficient_credits")
        self.assertEqual(self.fake.stats["submitted"],0)

    async def test_new_run_is_an_explicit_new_job(self):
        first, _ = await self.generate()
        second, _ = await self.generate(run=1)
        self.assertNotEqual(first, second)
        self.assertEqual(self.fake.stats["charged"],2)

    async def test_approval_preserves_request_id(self):
        self.fake.approval_by_default = True
        with self.assertRaises(client.CormakError) as exc:
            await self.generate()
        self.assertEqual(exc.exception.code, "approval_required")
        self.assertEqual(self.fake.stats["charged"],0)
        job = next(iter(self.session.approvals))
        approved_job, _ = await self.generate()
        self.assertEqual(approved_job,job)
        self.assertEqual(self.fake.stats["charged"],1)

    async def test_accepted_start_with_lost_response_resumes(self):
        actual = self.fake.request
        dropped = False
        async def request(url, **kwargs):
            nonlocal dropped
            result = await actual(url, **kwargs)
            if kwargs.get("payload",{}).get("params",{}).get("name") == "generate_3d" and not dropped:
                dropped = True
                raise client.CormakError("transport_error")
            return result
        self.fake.request = request
        with self.assertRaises(client.CormakError):
            await self.generate()
        await self.generate()
        self.assertEqual(self.fake.stats["charged"], 1)
        self.assertEqual(self.fake.stats["submitted"],1)

    async def test_unknown_intent_after_restart_never_starts(self):
        actual = self.fake.request
        async def request(url, **kwargs):
            if kwargs.get("payload",{}).get("params",{}).get("name") == "generate_3d":
                raise client.CormakError("transport_error")
            return await actual(url, **kwargs)
        self.fake.request = request
        with self.assertRaises(client.CormakError):
            await self.generate()
        self.binding.epoch = "after_restart"
        self.fake.request = actual
        with self.assertRaises(client.CormakError) as exc:
            await self.generate()
        self.assertEqual(exc.exception.code,"result_unknown")
        self.assertEqual(self.fake.stats["charged"],0)

    async def test_other_browser_has_its_own_job(self):
        first, _ = await self.generate()
        self.binding = runtime.Binding("second_browser", "epoch", self.session)
        second, _ = await self.generate()
        self.assertNotEqual(first,second)

    async def test_refresh_once_with_concurrent_reads(self):
        self.session._expiry = 0
        values = await asyncio.gather(*(runtime.account(self.session) for _ in range(4)))
        self.assertEqual(self.fake.stats["refresh"], 1)
        self.assertTrue(all(value == {"connected":True, "credits":130, "plan":"test"} for value in values))

    async def test_ambiguous_rotation_erases_tokens(self):
        actual = self.fake.request
        async def lost(url, **kwargs):
            await actual(url, **kwargs)
            raise client.CormakError("transport_error")
        self.session._expiry = 0
        self.fake.request = lost
        with self.assertRaises(client.CormakError):
            await self.session.call("get_account")
        self.assertFalse(self.session.connected)
        self.assertFalse(self.session._access)
        self.assertEqual(self.fake.stats["refresh"],1)

    async def test_disconnect_revokes_and_forgets(self):
        await self.session.disconnect()
        self.assertFalse(self.session.connected)
        self.assertEqual(self.fake.stats["revoked"], 1)

    async def test_failed_revoke_still_forgets(self):
        async def lost(*args, **kwargs):
            raise client.CormakError("transport_error")
        self.fake.request = lost
        with self.assertRaises(client.CormakError) as exc:
            await self.session.disconnect()
        self.assertEqual(exc.exception.code,"revocation_unconfirmed")
        self.assertFalse(self.session.connected)

    async def test_refund_status_is_preserved(self):
        job, _ = await self.generate()
        self.fake.jobs[(self.client_id, job)]["state"] = "failed"
        value = await generation.status(self.session, job)
        self.assertEqual(generation.metadata(job,value)["refunded"], True)
        with self.assertRaisesRegex(client.CormakError, "refund"):
            generation.fail_if_terminal(job,value)

    async def test_no_secrets_in_local_state(self):
        await self.generate()
        raw = (self.root / "state.sqlite3").read_bytes()
        for secret in (self.session._access, self.session._refresh, self.args["prompt"]):
            self.assertNotIn(secret.encode(),raw)

    async def test_interruption_does_not_submit(self):
        def stop():
            raise RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError,"interrupted"):
            await generation.generate(self.app,self.binding,self.args,25,0,check=stop,progress=lambda _:None)
        self.assertEqual(self.fake.stats["charged"],0)

    async def test_native_file_is_accepted_by_core_preview(self):
        from comfy_extras.nodes_load_3d import Preview3DAdvanced
        import folder_paths
        job, value = await self.generate()
        folder_paths.set_temp_directory(str(self.root))
        path, _ = await generation.download_result(self.app,self.session,job,value,self.root)
        result = Preview3DAdvanced.execute(nodes.Types.File3D(str(path),"glb"),{},300,300)
        self.assertIsInstance(result[0],nodes.Types.File3D)
        self.assertEqual(result[0].get_data().getvalue(),self.fake.model)

    async def test_filename_no_traversal_no_overwrite(self):
        job = str(uuid4())
        paths = [await assets.download(
            f"https://{client.STORAGE}/storage/v1/object/sign/studio-models/test/model.glb?token=sample",
            self.root, job, prefix="../../COM1:*", asset_fetch=self.fake.download) for _ in range(2)]
        self.assertNotEqual(*paths)
        self.assertTrue(all(p.parent == self.root.resolve() / "Cormak" for p in paths))
        self.assertTrue(all(".." not in p.name and ":" not in p.name for p in paths))

    async def test_output_symlink_rejected(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / "Cormak").symlink_to(other, target_is_directory=True)
        with self.assertRaises(client.CormakError):
            assets.safe_output(self.root)

    async def test_bad_images_rejected(self):
        for tensor in (torch.zeros((2,10,10,3)), torch.zeros((1,1,1,4)),
                       torch.full((1,10,10,3), float("nan")), torch.ones((1,10,10,3),dtype=torch.uint8)):
            with self.subTest(shape=tensor.shape), self.assertRaises(client.CormakError):
                assets.encode_image(tensor)

    async def test_asset_url_allowlist(self):
        valid = f"https://{client.STORAGE}/storage/v1/object/sign/studio-models/test/model.glb?token=xx"
        self.assertEqual(client.asset_link(valid),valid)
        for bad in ("http://127.0.0.1/model.glb", valid.replace("test/","../"), valid+"#hash",
                    valid.replace(client.STORAGE,client.STORAGE+"@localhost"), valid.replace("test/","%2e%2e/")):
            with self.subTest(url=bad), self.assertRaises(client.CormakError):
                client.asset_link(bad)

    async def test_glb_validation_external_and_truncated(self):
        model,_ = examples()
        path = self.root/"model.glb"
        path.write_bytes(model)
        assets.validate_glb(path)
        bad = (model[:20], model+b"x", glb({"asset":{"version":"2.0"},"buffers":[{"uri":"http://localhost/x"}]}),
               glb({"asset":{"version":"2.0"},"images":[{"uri":"../secret.png"}]}))
        for contents in bad:
            path.write_bytes(contents)
            with self.assertRaises(client.CormakError):
                assets.validate_glb(path)


class LocalEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.fake=FakeCormak(client)
        self.httpapp=web.Application()
        self.server=SimpleNamespace(app=self.httpapp,routes=web.RouteTableDef(),handlers=[])
        self.server.add_on_prompt_handler=self.server.handlers.append
        self.app=runtime.Runtime(self.server,transport=self.fake,
                                 journal=runtime.Journal(Path(self.tmp.name)/"state.sqlite3"))
        self.app.install()
        self.httpapp.add_routes(self.server.routes)
        self.httpapp.router.add_get("/",lambda r: web.Response(text="ComfyUI"))
        async def prompt(request):
            data=await request.json()
            for hook in self.server.handlers:
                data=hook(data)
            binding=None
            with CurrentNodeContext(data["prompt_id"],"1"):
                binding=self.app.current(required=False)
            return web.json_response({"prompt_id":data["prompt_id"],"browser":binding.browser if binding else None})
        self.httpapp.router.add_post("/prompt",prompt)
        self.http=TestClient(TestServer(self.httpapp),cookie_jar=CookieJar(unsafe=True))
        await self.http.start_server()
        self.headers={"X-Cormak-Request":"1"}

    async def asyncTearDown(self):
        await self.http.close()
        self.tmp.cleanup()

    async def connect(self):
        response=await self.http.post("/cormak/connect",headers=self.headers)
        self.assertEqual(response.status,200)
        from urllib.parse import urlsplit, parse_qs
        data=parse_qs(urlsplit((await response.json())["url"]).query)
        self.assertEqual(data["code_challenge_method"],["S256"])
        self.assertEqual(set(data["scope"][0].split()),set(client.SCOPES.split()))
        return data

    async def authorize(self):
        data=await self.connect()
        response=await self.http.get("/cormak/oauth/callback",params={
            "code":"cmcp_c_"+"a"*43,"state":data["state"][0]},allow_redirects=False)
        self.assertEqual(response.status,303)
        return next(iter(self.app.sessions))

    async def test_cross_origin_and_missing_header_rejected(self):
        for headers in ({},dict(self.headers, Origin="https://evil.test"),
                        dict(self.headers, **{"Sec-Fetch-Site":"cross-site"}),
                        dict(self.headers,Host="evil.test")):
            response=await self.http.post("/cormak/connect",headers=headers)
            self.assertEqual(response.status,403)
        self.assertEqual(self.fake.stats["registered"],0)

    async def test_wrong_state_cannot_consume_authorization(self):
        data=await self.connect()
        bad=await self.http.get("/cormak/oauth/callback",params={"state":"b"*43,"code":"cmcp_c_"+"c"*43})
        self.assertEqual(bad.status,400)
        self.assertFalse(self.app.sessions)
        okay=await self.http.get("/cormak/oauth/callback",params={"state":data["state"][0],"code":"cmcp_c_"+"c"*43},
                                 allow_redirects=False)
        self.assertEqual(okay.status,303)
        replay=await self.http.get("/cormak/oauth/callback",params={"state":data["state"][0],"code":"cmcp_c_"+"c"*43},
                                   allow_redirects=False)
        self.assertEqual(replay.status,400)

    async def test_queue_bound_to_browser_and_not_client_supplied_id(self):
        browser=await self.authorize()
        graph={"1":{"class_type":"CormakAccount","inputs":{}}}
        requested=str(uuid4())
        response=await self.http.post("/prompt",json={"prompt":graph,"prompt_id":requested})
        payload=await response.json()
        self.assertEqual(payload["browser"],browser)
        self.assertNotEqual(payload["prompt_id"],requested)
        self.assertNotIn("cmcp_",json.dumps(payload))

    async def test_cookie_is_httponly_and_not_in_graph(self):
        response=await self.http.post("/cormak/connect",headers=self.headers)
        cookie=response.cookies[runtime.COOKIE]
        self.assertEqual(cookie["httponly"],True)
        self.assertEqual(cookie["samesite"],"Lax")
        self.assertNotIn(cookie.value,await response.text())

    async def test_other_browser_is_disconnected(self):
        await self.authorize()
        self.http.session.cookie_jar.clear()
        state=await self.http.get("/cormak/state",headers=self.headers)
        self.assertFalse((await state.json())["connected"])
        graph={"1":{"class_type":"CormakTextTo3D","inputs":{}}}
        response=await self.http.post("/prompt",json={"prompt":graph})
        self.assertEqual(response.status,401)

    async def test_expired_state_rejected(self):
        data=await self.connect()
        for pending in self.app.pending.values():
            pending.expires=0
        response=await self.http.get("/cormak/oauth/callback",params={"state":data["state"][0],"code":"cmcp_c_"+"c"*43})
        self.assertEqual(response.status,400)

    async def test_reconnect_reuses_public_client_id(self):
        await self.authorize()
        await self.http.post("/cormak/disconnect",headers=self.headers)
        await self.authorize()
        self.assertEqual(self.fake.stats["registered"],1)

    async def test_repeated_connect_rate_limited(self):
        for _ in range(7):
            await self.connect()
        response=await self.http.post("/cormak/connect",headers=self.headers)
        self.assertEqual(response.status,400)

    async def test_multi_user_or_public_listen_is_disabled(self):
        with patch.object(runtime.args,"multi_user",True):
            self.assertFalse(runtime.Runtime(self.server).enabled)
        with patch.object(runtime.args,"listen","0.0.0.0"):
            self.assertFalse(runtime.Runtime(self.server).enabled)


if __name__ == "__main__":
    unittest.main(argv=[str(Path(__file__))], verbosity=2)
