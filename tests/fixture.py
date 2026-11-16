"""Deterministic mesh and fake Cormak service. Never contacts the internet."""
import base64
import io
import json
import struct
from uuid import uuid4

from PIL import Image, ImageDraw


def examples():
    """Build a small colored lantern and matching reference illustration."""
    image = Image.new("RGB", (400, 400), "#171b28")
    draw = ImageDraw.Draw(image)
    draw.ellipse((79, 328, 319, 351), fill="#11141e")
    draw.rounded_rectangle((173, 40, 227, 131), radius=27, outline="#dba954", width=12)
    draw.polygon([(107, 155), (199, 108), (291, 155), (273, 171), (125, 171)], fill="#d9aa57")
    draw.rectangle((133, 171, 266, 305), fill="#6c4527")
    draw.rectangle((150, 182, 249, 294), fill="#f9cb76")
    draw.rectangle((189, 182, 208, 294), fill="#8f602d")
    draw.rounded_rectangle((112, 302, 288, 329), radius=8, fill="#bd843d")
    png = io.BytesIO()
    image.save(png, "PNG")

    vertices, colors, indices = [], [], []
    bronze = (0.72, 0.35, 0.08)
    gold = (0.98, 0.73, 0.24)
    glow = (1.0, 0.94, 0.61)

    def face(v, color):
        start = len(vertices)
        vertices.extend(v)
        colors.extend([color] * len(v))
        for i in range(1, len(v) - 1):
            indices.extend([start, start + i, start + i + 1])

    def box(x, y, z, w, h, d, color):
        v = [(x+a*w, y+b*h, z+c*d) for a,b,c in
             ((0,0,0),(1,0,0),(1,0,1),(0,0,1),(0,1,0),(1,1,0),(1,1,1),(0,1,1))]
        for f in ((0,1,2,3), (7,6,5,4), (4,5,1,0), (5,6,2,1), (6,7,3,2), (7,4,0,3)):
            face([v[i] for i in f], color)

    box(-.68, 0, -.60, 1.36, .17, 1.2, bronze)
    box(-.58, .17, -.50, 1.16, .08, 1., gold)
    box(-.43, .25, -.37, .86, .91, .74, glow)
    for x in (-.54, .43):
        for z in (-.49, .38):
            box(x, .24, z, .11, .93, .11, bronze)
    box(-.68, 1.17, -.59, 1.36, .08, 1.18, bronze)
    bottom = [(-.7,1.25,-.62),(.7,1.25,-.62),(.7,1.25,.62),(-.7,1.25,.62)]
    top = [(-.24,1.53,-.19),(.24,1.53,-.19),(.24,1.53,.19),(-.24,1.53,.19)]
    for i in range(4):
        j = (i + 1) % 4
        face([bottom[i], top[i], top[j], bottom[j]], gold)
    face(top[::-1], gold)
    box(-.20, 1.49, -.05, .065, .42, .10, bronze)
    box(.135, 1.49, -.05, .065, .42, .10, bronze)
    box(-.20, 1.87, -.05, .40, .065, .10, gold)
    pos = b"".join(struct.pack("<3f", *v) for v in vertices)
    rgb = b"".join(struct.pack("<3f", *c) for c in colors)
    idx = struct.pack("<" + "H" * len(indices), *indices)
    binary = pos + rgb + idx
    binary += b"\x00" * (-len(binary) % 4)
    doc = {
        "asset": {"version": "2.0", "generator": "Cormak QA fixture"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1},
                                    "indices": 2, "material": 0}]}],
        "materials": [{"doubleSided": True, "pbrMetallicRoughness": {"metallicFactor": 0, "roughnessFactor": .63}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer":0,"byteOffset":0,"byteLength":len(pos),"target":34962},
            {"buffer":0,"byteOffset":len(pos),"byteLength":len(rgb),"target":34962},
            {"buffer":0,"byteOffset":len(pos)+len(rgb),"byteLength":len(idx),"target":34963},
        ],
        "accessors": [
            {"bufferView":0,"componentType":5126,"count":len(vertices),"type":"VEC3",
             "min":[min(v[k] for v in vertices) for k in range(3)],
             "max":[max(v[k] for v in vertices) for k in range(3)]},
            {"bufferView":1,"componentType":5126,"count":len(colors),"type":"VEC3"},
            {"bufferView":2,"componentType":5123,"count":len(indices),"type":"SCALAR"},
        ],
    }
    return glb(doc, binary), png.getvalue()


def glb(document, binary=b""):
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    body = struct.pack("<I4s", len(encoded), b"JSON") + encoded
    if binary:
        body += struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class FakeCormak:
    def __init__(self, client_module, report=None):
        self.client = client_module
        self.registrations = {}
        self.access = {}
        self.refreshes = {}
        self.jobs = {}
        self.report = report
        self.balance = 130
        self.approval_by_default = True
        self.stats = {"registered": 0, "oauth": 0, "refresh": 0, "revoked": 0,
                      "submitted": 0, "charged": 0, "downloaded": 0, "photos": 0}
        self.model, self.image = examples()

    def count(self, key):
        self.stats[key] += 1
        if self.report:
            temporary = self.report.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.stats))
            temporary.replace(self.report)

    def token(self, owner):
        access = "cmcp_a_" + uuid4().hex + "A" * 11
        refresh = "cmcp_r_" + uuid4().hex + "R" * 11
        self.access[access] = owner
        self.refreshes[refresh] = owner
        return {"access_token": access, "refresh_token": refresh, "expires_in": 3600, "scope": self.client.SCOPES}

    @staticmethod
    def result(data, error=False):
        return 200, {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": data, "isError": error}}

    async def request(self, url, *, payload=None, form=None, headers=None, empty=False):
        assert url.startswith(self.client.RESOURCE)
        if url.endswith("/register"):
            assert payload["token_endpoint_auth_method"] == "none"
            client_id = str(uuid4())
            self.registrations[client_id] = payload["redirect_uris"][0]
            self.count("registered")
            return 201, {"client_id": client_id}
        if url.endswith("/token"):
            client = form["client_id"]
            if form["grant_type"] == "authorization_code":
                assert form["redirect_uri"] == self.registrations[client]
                assert self.client.CODE.fullmatch(form["code"]) and len(form["code_verifier"]) == 64
                self.count("oauth")
            else:
                if self.refreshes.pop(form["refresh_token"], None) != client:
                    return 400, {"error": "invalid_grant"}
                self.count("refresh")
            return 200, self.token(client)
        if url.endswith("/revoke"):
            owner = self.refreshes.pop(form["token"], None)
            assert owner == form["client_id"]
            self.access = {key: value for key, value in self.access.items() if value != owner}
            self.count("revoked")
            return 200, {}
        owner = self.access.get(headers["Authorization"].removeprefix("Bearer "))
        if owner is None:
            return 401, {}
        name, params = payload["params"]["name"], payload["params"]["arguments"]
        if name == "get_account":
            return self.result({"available_ai_credits": self.balance, "plan": "test",
                                "sensitive_field_to_discard": "private_fixture"})
        if name == "quote_3d":
            quality = params["quality"]
            return self.result({"quote": {"maximum": {"basic": 25, "medium": 35, "high": 50}[quality],
                                          "quality": quality, "currency": "ai_credits"},
                                "spending": {"starts_without_approval": not self.approval_by_default}})
        key = (owner, params["request_id"])
        if name == "generate_3d":
            self.count("submitted")
            assert params["confirm_spend"] is True and params["expected_max_credits"] in (25, 35, 50)
            if params["input_mode"] == "photo":
                image = params["images"][0]
                with Image.open(io.BytesIO(base64.b64decode(image["data"]))) as pic:
                    assert pic.format == "JPEG" and pic.mode == "RGB"
                self.count("photos")
            old = self.jobs.get(key)
            if old is not None:
                assert old["params"] == params
                if old["state"] == "approved":
                    old["state"] = "running"
                    self.count("charged")
            else:
                wants_approval = self.approval_by_default and params["input_mode"] == "prompt"
                self.jobs[key] = {"state": "awaiting_confirmation" if wants_approval else "running",
                                  "params": params, "reads": 0, "approval": str(uuid4())}
                if not wants_approval:
                    self.count("charged")
            job = self.jobs[key]
            return self.result({"request_id": key[1], "status": job["state"],
                                "approval_url": f"{self.client.APP}/mcp?approval_id={job['approval']}"})
        if name == "get_3d_status":
            job = self.jobs.get(key)
            if job is None:
                return self.result({"request_id": key[1], "error": "studio_3d_not_found"}, error=True)
            status = job["state"]
            job["reads"] += 1
            if status == "awaiting_confirmation":
                if job["reads"] == 1:
                    # The browser fixture opens the approval link before requeue.
                    job["state"] = "approved"
                status = job["state"]
            if status == "running" and job["reads"] > 2:
                job["state"] = status = "succeeded"
            data = {"request_id": key[1], "status": status, "quality": job["params"]["quality"], "progress": 62}
            if status == "succeeded":
                data.update(progress=100, credits_charged=job["params"]["expected_max_credits"],
                            model_url=f"https://{self.client.STORAGE}/storage/v1/object/sign/studio-models/fixture/model.glb?token=sample")
            if status == "failed":
                data.update(error="studio_3d_failed", refunded=True)
            return self.result(data, error=status == "failed")
        raise AssertionError(f"Unexpected fixture tool: {name}")

    async def download(self, url, stream):
        assert "token=sample" in url
        stream.write(self.model)
        self.count("downloaded")
