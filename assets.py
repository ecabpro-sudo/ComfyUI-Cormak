"""Encode one Comfy IMAGE and safely download/write self-contained GLB files."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
from uuid import uuid4

import aiohttp
from PIL import Image

from .client import CormakError, UUID, asset_link

MAX_GLB = 100_000_000
MAX_IMAGE = 2_250_000


def encode_image(image):
    import torch

    if (not isinstance(image, torch.Tensor) or image.ndim != 4 or image.shape[0] != 1
            or image.shape[3] != 3 or not image.is_floating_point()
            or not all(1 <= x <= 8192 for x in image.shape[1:3])):
        raise CormakError("invalid_image")
    with torch.no_grad():
        # Resize before copying to CPU. Keep aspect ratio; avoid an unbounded upload.
        pixels = image[0].detach()
        h, w, _ = pixels.shape
        scale = min(1, 1600 / max(w, h))
        if scale < 1:
            pixels = torch.nn.functional.interpolate(pixels.permute(2, 0, 1).unsqueeze(0).float(),
                size=(max(1, round(h * scale)), max(1, round(w * scale))), mode="bilinear", align_corners=False
            )[0].permute(1, 2, 0)
        if not bool(torch.isfinite(pixels).all()):
            raise CormakError("invalid_image")
        data = pixels.clamp(0, 1).mul(255).round().to(torch.uint8).cpu().numpy()
    picture = Image.fromarray(data)
    for quality in (92, 82, 72):
        buffer = io.BytesIO()
        picture.save(buffer, "JPEG", quality=quality, optimize=True)
        if 0 < buffer.tell() <= MAX_IMAGE:
            return {"mime_type": "image/jpeg", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}
    raise CormakError("invalid_image")


def validate_glb(path):
    try:
        size = path.stat().st_size
        if not 28 <= size <= MAX_GLB:
            raise ValueError()
        with path.open("rb") as stream:
            magic, version, length = struct.unpack("<4sII", stream.read(12))
            if (magic, version, length) != (b"glTF", 2, size):
                raise ValueError()
            length, kind = struct.unpack("<I4s", stream.read(8))
            if kind != b"JSON" or not 4 <= length <= min(16_000_000, size - 20) or length % 4:
                raise ValueError()
            manifest = json.loads(stream.read(length).decode("utf-8"))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("asset"), dict) or manifest["asset"].get("version") != "2.0":
                raise ValueError()
            for field in ("buffers", "images"):
                entries = manifest.get(field, [])
                if not isinstance(entries, list) or any(not isinstance(entry, dict) or "uri" in entry for entry in entries):
                    raise ValueError()
            remaining = size - stream.tell()
            if remaining:
                length, kind = struct.unpack("<I4s", stream.read(8))
                if kind != b"BIN\x00" or remaining != length + 8 or length % 4:
                    raise ValueError()
            else:
                length = 0
            buffers = manifest.get("buffers", [])
            if buffers:
                used = buffers[0].get("byteLength")
                if len(buffers) != 1 or type(used) is not int or used <= 0 or not used <= length <= used + 3:
                    raise ValueError()
    except (ValueError, OSError, struct.error, UnicodeError, RecursionError):
        raise CormakError("invalid_glb") from None


def safe_output(root):
    root = Path(root).resolve()
    path = root / "Cormak"
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.resolve() != path or not path.is_dir():
        raise CormakError("invalid_asset_path")
    return path


def filename(prefix, job):
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", prefix)[:40].strip("-_") or "model"
    return f"cormak-{prefix}-{job[:8]}-{uuid4().hex}.glb"


async def download(url, root, job, *, prefix="model", asset_fetch=None):
    if not UUID.fullmatch(job):
        raise CormakError("invalid_request")
    url = asset_link(url)
    # Files go into a dedicated child of Comfy's temp/output root. No user-provided paths.
    try:
        root = safe_output(root)
        path = root / filename(prefix, job)
        temp = root / (path.name + ".part")
        stream = temp.open("xb")
        try:
            with stream:
                await (asset_fetch or fetch_asset)(url, stream)
            await asyncio.to_thread(validate_glb, temp)
            try:
                os.link(temp, path)  # atomic no-clobber publication where supported
            except FileExistsError:
                raise
            except OSError:
                # Portable installs may use a filesystem that lacks hardlinks.
                with path.open("xb") as dest:
                    try:
                        with temp.open("rb") as src:
                            shutil.copyfileobj(src, dest)
                    except BaseException:
                        dest.close()
                        path.unlink(missing_ok=True)
                        raise
            return path
        finally:
            temp.unlink(missing_ok=True)
    except OSError:
        raise CormakError("invalid_asset_path") from None


async def fetch_asset(url, stream):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, connect=10, sock_read=20),
                                         auto_decompress=False) as http:
            async with http.get(url, allow_redirects=False) as response:
                if response.status in (401, 403, 404):
                    raise CormakError("asset_denied")
                if (response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity"
                        or response.content_type not in ("model/gltf-binary", "application/octet-stream")
                        or (response.content_length is not None and response.content_length > MAX_GLB)):
                    raise CormakError("invalid_glb")
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_GLB:
                        raise CormakError("invalid_glb")
                    stream.write(chunk)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        raise CormakError("transport_error") from None
