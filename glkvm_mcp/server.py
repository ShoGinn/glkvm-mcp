# FastMCP server exposing the GLKVM (PiKVM kvmd fork) interactive session.
#
# Tools cover: screenshot + OCR element tree (blocks/lines/words with pixel
# bboxes), absolute mouse (with GL's 90%-width calibration), full keyboard
# (KeyboardEvent.code names, e.g. MetaLeft opens the Start menu), unicode
# typing, human-interference detection, and a raw kvmd API passthrough.

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent

from .kvmd import GlkvmClient, GlkvmError
from .vision import Engine, build_tree, flatten, find_text

mcp = FastMCP(
    "glkvm",
    instructions=(
        "Drive and observe the GLKVM KVM session (target machine screen, 2560x1440). "
        "Coordinates are target-screen pixels. Prefer observe_screen() for bounded text-only "
        "checks; use screenshot() when visual context or OCR verification is needed. "
        "Use screen_elements() when clickable OCR boxes are needed. "
        "Inspect the target before using click_at/click_text/key/key_combo/type_text to act. "
        "Keyboard keys are KeyboardEvent.code names (MetaLeft/KeyA/Enter/F1/Escape/...). "
        "File transfer needs no target agent: msd_push_files wraps files in a FAT image and "
        "attaches it as a USB drive on the target; msd_pull_files streams that image back "
        "(target writes files onto the mounted drive). "
        "human_activity() warns when a human is interfering with the target machine."
    ),
)

client = GlkvmClient()
ocr = Engine()
_tree_cache: dict[tuple[str, str, bool, float], dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _tree_for(img, sha: str, *, letters: bool, min_score: float,
              force_refresh: bool = False,
              pixel_sha256: str | None = None) -> tuple[dict, bool]:
    """Return OCR for this frame and the exact options used to build its tree."""
    pixel_identity = pixel_sha256 or _pixel_sha256(img)
    cache_key = (sha, pixel_identity, letters, float(min_score))
    if not force_refresh and cache_key in _tree_cache:
        return _tree_cache[cache_key], True
    res, ocr_ms = ocr.run(img, 1.0)
    tree = build_tree(res, img.width, img.height, sha, time.time(), ocr_ms,
                      letters=letters, min_score=min_score)
    _tree_cache[cache_key] = tree
    while len(_tree_cache) > 16:
        _tree_cache.pop(next(iter(_tree_cache)))
    return tree, False


def _pixel_sha256(img) -> str:
    """Hash decoded pixels so the observation identity is not a short JPEG digest."""
    digest = hashlib.sha256()
    digest.update(img.width.to_bytes(8, "big"))
    digest.update(img.height.to_bytes(8, "big"))
    digest.update(img.convert("RGB").tobytes())
    return digest.hexdigest()


def _artifact_directory() -> Path:
    configured = os.environ.get("GLKVM_ARTIFACT_DIR")
    path = Path(configured).expanduser() if configured else Path.home() / ".local/share/glkvm-mcp/artifacts"
    # Make returned paths portable across agent working directories without
    # resolving away a final symlink before the ownership/type check below.
    path = path.absolute()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("artifact directory is not a private directory owned by this server user")
    permissions = stat.S_IMODE(info.st_mode)
    if permissions & 0o077 or not permissions & 0o200:
        raise OSError("artifact directory must be owner-only and writable")
    return path


def _save_frame(img, observation_id: str, captured_at: str, frame_sha256: str,
                artifact_dir: Path) -> dict:
    """Save a fresh frame with exclusive creation and private, read-only permissions."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    path = artifact_dir / f"{observation_id}.png"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(path, 0o400)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": digest,
        "format": "png",
        "provenance": {
            "observation_id": observation_id,
            "capture_received_at_utc": captured_at,
            "frame_sha256": frame_sha256,
            "source": "fresh screen capture received by this local MCP server",
        },
    }


def _text_result(payload: dict) -> ToolResult:
    """Return one client-visible JSON text block without a duplicate structured echo."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ToolResult(content=[TextContent(text=text)])


def _observation_payload(base: dict, lines: list[dict], *, max_lines: int,
                         max_chars: int, total_text_chars: int) -> dict:
    """Apply line and serialized-JSON character limits with explicit truncation."""
    included = [dict(line) for line in lines[:max_lines]]
    payload = {**base, "lines": included}

    def update_truncation() -> None:
        included_chars = sum(len(line["text"]) for line in included)
        payload["truncated"] = (len(lines) > len(included) or included_chars < total_text_chars)
        payload["truncation"] = {
            "lines_omitted": max(0, len(lines) - len(included)),
            "text_characters_omitted": max(0, total_text_chars - included_chars),
        }

    def encoded_length() -> int:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    update_truncation()
    while encoded_length() > max_chars:
        last_text = next((line for line in reversed(included) if line["text"]), None)
        if last_text is not None:
            original = last_text["text"]
            low, high, best = 0, len(original) - 1, None
            while low <= high:
                middle = (low + high) // 2
                last_text["text"] = original[:middle]
                last_text["text_truncated"] = True
                update_truncation()
                if encoded_length() <= max_chars:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            last_text["text"] = original[:best] if best is not None else ""
            last_text["text_truncated"] = True
        elif included:
            included.pop()
        else:
            # The public minimum is chosen to fit the fixed provenance fields.
            raise ValueError("max_chars is too small for observation metadata")
        update_truncation()
    return payload


async def _snap() -> tuple[Image, dict]:
    img, sha = await client.snapshot()
    pixel_identity = _pixel_sha256(img)
    info = {
        "frame_sha": sha,
        "resolution": {"width": img.width, "height": img.height},
        "ms_since_frame_capture": None,
        "cached_element_tree": any(key[:2] == (sha, pixel_identity) for key in _tree_cache),
    }
    return img, info


def _scale_img(img, max_w: int, quality: int):
    if img.width > max_w:
        r = max_w / img.width
        img = img.resize((max_w, int(img.height * r)))
        scale = r
    else:
        scale = 1.0
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue(), scale


# ---------------------------------------------------------------- screenshot

@mcp.tool()
async def screenshot(max_width: int = 1600, quality: int = 80) -> Image:
    """Live screenshot of the target machine's screen (JPEG).

    Returns the image; screen_elements() returns the same frame's OCR tree with
    clickable bboxes in full-resolution screen pixels."""
    img, info = await _snap()
    data, _ = _scale_img(img, max_width, quality)
    return Image(data=data, format="jpeg")


@mcp.tool()
async def observe_screen(
    max_lines: int = 40,
    max_chars: int = 12000,
    save_frame: bool = False,
) -> ToolResult:
    """Capture a fresh screen and return compact OCR text without image content.

    Bboxes are native target-screen pixels. Confidence is a model estimate, not
    proof that text is correct; low-confidence lines need visual verification.
    max_chars limits the serialized observation JSON. The timestamp is when this
    server received the capture; device capture time and physical target identity
    are unavailable. save_frame writes a private, unique PNG to this server's
    local artifact directory and returns its path, size, hash, and provenance.
    """
    if not 1 <= max_lines <= 100:
        return _text_result({
            "ok": False, "error": "max_lines must be between 1 and 100",
        })
    if not 1024 <= max_chars <= 32768:
        return _text_result({
            "ok": False, "error": "max_chars must be between 1024 and 32768",
        })

    try:
        # Always acquire a new frame, even when OCR for identical pixels is cached.
        img, source_sha = await client.snapshot()
    except Exception:
        return _text_result({
            "ok": False,
            "error": "fresh KVM capture failed; no previous capture was returned",
        })

    captured_at = _utc_now()
    observation_id = str(uuid.uuid4())
    frame_sha256 = _pixel_sha256(img)
    try:
        tree, cached = _tree_for(img, source_sha, letters=False, min_score=0.0,
                                 pixel_sha256=frame_sha256)
    except Exception:
        return _text_result({
            "ok": False,
            "error": "OCR failed for the fresh capture; no cached observation was returned",
        })

    ocr_ts = float(tree["frame"].get("ocr_ts", time.time()))
    lines = []
    for block in tree.get("blocks", []):
        for line in block.get("lines", []):
            score = float(line.get("score", 0.0))
            lines.append({
                "text": str(line.get("text", "")),
                "bbox_xywh_px": line.get("bbox", [0, 0, 0, 0]),
                "confidence": round(score, 3),
                "confidence_quality": "low" if score < 0.60 else "estimated",
            })

    artifact_dir = None
    saved_frame = None
    if save_frame:
        try:
            artifact_dir = _artifact_directory()
            saved_frame = {
                "path": str(artifact_dir / f"{observation_id}.png"),
                "bytes": 18446744073709551615,
                "sha256": "0" * 64,
                "format": "png",
                "provenance": {
                    "observation_id": observation_id,
                    "capture_received_at_utc": captured_at,
                    "frame_sha256": frame_sha256,
                    "source": "fresh screen capture received by this local MCP server",
                },
            }
        except Exception:
            return _text_result({
                "ok": False,
                "error": "fresh capture could not be saved; no previous artifact was returned",
            })

    base = {
        "ok": True,
        "observation_id": observation_id,
        "capture_received_at_utc": captured_at,
        "capture_time_source": "local MCP server receipt time",
        "device_capture_timestamp_utc": None,
        "device_capture_timestamp_available": False,
        "target_identity": {"value": None, "status": "unknown"},
        "frame_sha256": frame_sha256,
        "frame_hash_basis": "SHA-256 of decoded RGB pixels and dimensions",
        "snapshot_response_sha256_prefix12": source_sha,
        "resolution_px": {"width": img.width, "height": img.height},
        "ocr": {
            "timestamp_utc": datetime.fromtimestamp(ocr_ts, timezone.utc)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "cached": cached,
            "cache_age_ms": max(0, round((time.time() - ocr_ts) * 1000)) if cached else 0,
        },
        "interpretation": "OCR is approximate; low-confidence lines may be wrong.",
        "saved_frame": saved_frame,
        "limits": {"max_lines": max_lines, "max_chars": max_chars},
    }
    total_text_chars = sum(len(line["text"]) for line in lines)
    try:
        payload = _observation_payload(base, lines, max_lines=max_lines,
                                       max_chars=max_chars,
                                       total_text_chars=total_text_chars)
    except Exception:
        return _text_result({
            "ok": False, "error": "observation could not fit the requested output limit",
        })
    if save_frame:
        saved_path = None
        try:
            assert artifact_dir is not None
            saved_frame = _save_frame(img, observation_id, captured_at,
                                      frame_sha256, artifact_dir)
            saved_path = Path(saved_frame["path"])
            base["saved_frame"] = saved_frame
            payload = _observation_payload(base, lines, max_lines=max_lines,
                                           max_chars=max_chars,
                                           total_text_chars=total_text_chars)
        except Exception:
            if saved_path is not None:
                try:
                    saved_path.unlink()
                except OSError:
                    pass
            return _text_result({
                "ok": False,
                "error": "fresh capture could not be saved; no previous artifact was returned",
            })
    # A single JSON text block is visible to ordinary MCP clients and avoids a
    # duplicate structured-content copy of the same JSON. No image/resource blocks.
    return _text_result(payload)


# ---------------------------------------------------------------- element tree

@mcp.tool()
async def screen_elements(
    letters: bool = False,
    min_score: float = 0.45,
    max_width: int = 1600,
    force_refresh: bool = False,
) -> dict:
    """OCR the live screen into a clickable element tree.

    Structure: blocks -> lines -> words (and optionally letters), each word with
    an id, text, and bbox [x,y,w,h] in full-resolution screen pixels (2560x1440).
    Word/letter boxes inside a line are width-estimated and accurate to a few px;
    line boxes come from OCR and are precise. Also returns a downscaled image so
    you can see the frame the tree describes.

    Use force_refresh to re-OCR even if the frame hasn't changed."""
    img, sha = await client.snapshot()
    tree, cached = _tree_for(img, sha, letters=letters, min_score=min_score,
                             force_refresh=force_refresh)
    data, scale = _scale_img(img, max_width, 80)
    return ToolResult(
        content=[Image(data=data, format="jpeg"),
                 # JSON text block so text clients can read the tree without an image decoder
                 json.dumps(tree)],
        structured_content=tree,
    )


# ---------------------------------------------------------------- click helpers

@mcp.tool()
async def click_at(
    x: int,
    y: int,
    button: Literal["left", "right", "middle"] = "left",
    clicks: int = 1,
    gap_ms: int = 90,
) -> dict:
    """Move the target's mouse to (x, y) in screen pixels and click.

    clicks=2 for double-click. The cursor lands exactly at (x, y) up to -2/+1 px."""
    foreign = await client.interference_check()
    for i in range(max(1, clicks)):
        if i:
            await asyncio.sleep(gap_ms / 1000)
        await client.mouse_click(x, y, button)
    return {"ok": True, "clicked": [x, y], "button": button, "clicks": clicks,
            "note": "cursor tip lands at the given px",
            "interference": foreign}


@mcp.tool()
async def click_text(
    query: str,
    nth: int = 0,
    button: Literal["left", "right", "middle"] = "left",
    exact: bool = False,
    letters: bool = False,
) -> dict:
    """OCR the live screen and click the text matching query.

    Matches words and lines (case-insensitive substring unless exact=True);
    nth picks among multiple matches (default first at top-left). Returns the
    matched text and bbox. Requires a positive match to move - never guesses
    coordinates."""
    img, sha = await client.snapshot()
    tree, _ = _tree_for(img, sha, letters=letters, min_score=0.45)
    matches = find_text(tree, query, nth, exact)
    if not matches:
        return {"ok": False, "error": f"no text matching {query!r}",
                "hint": "call screen_elements() to see what is on screen", "matches": []}
    m = matches[0]
    cx, cy = m["center"]
    foreign = await client.interference_check()
    await client.mouse_click(cx, cy, button)
    return {"ok": True, "clicked": [round(cx), round(cy)], "target": m,
            "alternatives": [{"id": n["id"], "text": n["text"]} for n in matches[1:8]]}


# ---------------------------------------------------------------- mouse

@mcp.tool()
async def move_mouse(x: int, y: int) -> dict:
    """Move the target's cursor to (x, y) in screen pixels (no click)."""
    v = await client.mouse_move(x, y)
    return {"ok": True, "px": [x, y], "device_int16": v,
            "note": "reachable x range is the middle 90% of the frame (GL firmware quirk); "
                    "use mouse_relative for the extreme edges"}


@mcp.tool()
async def mouse_relative(
    dx: int = 0, dy: int = 0,
) -> dict:
    """Nudge the cursor by a relative delta (for the unreachable 5% screen edges)."""
    await client.request("POST", "/api/hid/events/send_mouse_relative",
                         query={"delta_x": max(-127, min(127, dx)),
                                "delta_y": max(-127, min(127, dy))})
    return {"ok": True, "delta": [dx, dy]}


@mcp.tool()
async def drag(
    x1: int, y1: int, x2: int, y2: int,
    button: Literal["left", "right", "middle"] = "left",
    steps: int = 14,
    settle_ms: int = 120,
) -> dict:
    """Press at (x1,y1), interpolate to (x2,y2), release. Screen-pixel coords."""
    if not (1 <= steps <= 60):
        return {"ok": False, "error": "steps must be 1..60"}
    await client.interference_check()
    await client.mouse_move(x1, y1)
    await asyncio.sleep(settle_ms / 1000)
    await client.mouse_button(button, True)
    for i in range(1, steps + 1):
        xi = x1 + (x2 - x1) * i / steps
        yi = y1 + (y2 - y1) * i / steps
        await client.mouse_move(xi, yi)
        await asyncio.sleep(0.016)
    await asyncio.sleep(settle_ms / 1000)
    await client.mouse_button(button, False)
    return {"ok": True, "drag": [[x1, y1], [x2, y2]], "button": button}


@mcp.tool()
async def scroll(
    notches: int = 3,
    direction: Literal["down", "up", "left", "right"] = "down",
    x: int | None = None,
    y: int | None = None,
) -> dict:
    """Scroll the wheel (optionally after moving to x,y). notches = wheel detents."""
    if x is not None and y is not None:
        await client.mouse_move(x, y)
    await client.scroll(notches, direction)
    return {"ok": True, "notches": notches, "direction": direction}


# ---------------------------------------------------------------- keyboard

@mcp.tool()
async def key(
    name: str,
    action: Literal["tap", "down", "up"] = "tap",
) -> dict:
    """Send a keyboard key by KeyboardEvent.code name (MetaLeft, KeyA, Enter, F5,
    Escape, Numpad1, Space, ArrowLeft, ControlLeft, ShiftLeft, AltLeft, ...).

    MetaLeft tap opens the Start menu. Use key_combo for chorded shortcuts."""
    if action == "tap":
        await client.key_tap(name)
    elif action == "down":
        await client.key_down(name)
    else:
        await client.key_up(name)
    return {"ok": True, "key": name, "action": action}


@mcp.tool()
async def key_combo(keys: list[str]) -> dict:
    """Send a chorded shortcut, e.g. ["MetaLeft", "KeyE"] (Win+E File Explorer),
    ["ControlLeft", "ShiftLeft", "KeyR"], ["AltLeft", "Tab"]. Pressed in order,
    released in reverse."""
    await client.key_combo(keys)
    return {"ok": True, "keys": keys}


@mcp.tool()
async def type_text(text: str, delay_ms: int = 10, echo_text: bool = True) -> dict:
    """Type unicode text on the target via kvmd's keysym printer (server-side
    layout translation). Click a text field first. Set echo_text=False to omit
    the typed value from the result; tool arguments may still be logged, so this
    is not a secret-safe input channel."""
    await client.type_text(text, delay_ms / 1000)
    if echo_text:
        return {"ok": True, "typed": text}
    return {"ok": True, "characters_typed": len(text)}


# ---------------------------------------------------------------- file transfer

# MSD attaches a USB drive on the target - visible to target-side security
# tooling (PnP audit, removable-storage DLP). Off unless GLKVM_ALLOW_MSD=1.
def _msd_guard() -> dict | None:
    if os.environ.get("GLKVM_ALLOW_MSD", "").strip() not in ("1", "true", "yes"):
        return {
            "ok": False,
            "disabled": True,
            "error": ("MSD file-transfer tools are disabled by default: attaching a USB "
                      "drive on the target is visible to target-side security tooling "
                      "(device-install audit, removable-storage rules). Set "
                      "GLKVM_ALLOW_MSD=1 in the glkvm server env in .mcp.json to enable."),
        }
    return None


@mcp.tool()
async def msd_state() -> dict:
    """Virtual mass-storage state: drives (cdrom / rw partition), connected,
    stored images, storage free space."""
    _g = _msd_guard()
    if _g: return _g
    return await client.msd_state()


@mcp.tool()
async def msd_push_files(
    paths: list[str],
    image_name: str = "glkvm-mcp-files.img",
    size_mb: int = 0,
    connect_target: bool = False,
    rw: bool = True,
) -> dict:
    """Push files (workstation -> target machine).

    Builds a FAT image containing the files/dirs (basenames preserved, dirs
    recursive), uploads it to the KVM's MSD storage over streaming HTTP. With
    connect_target=True the image is also attached as a USB drive on the target
    (Windows shows it immediately; disconnect later with msd_disconnect).
    Without connecting, files only reach the KVM's storage."""
    _g = _msd_guard()
    if _g: return _g
    from .msdfat import MsdFatError, build_fat_image

    os.makedirs("/tmp/glkvm-mcp-msd", exist_ok=True)
    tmp = os.path.join("/tmp/glkvm-mcp-msd", os.path.basename(image_name) or "glkvm-mcp-files.img")
    sz, files = await build_fat_image(paths, tmp, size_mb)
    written = await client.msd_write_image(tmp, os.path.basename(image_name))
    res = {"ok": True, "image": os.path.basename(image_name), "image_bytes": sz,
           "uploaded": written, "files": files, "connected": None}
    if connect_target:
        res["connected"] = await client.msd_connect(os.path.basename(image_name),
                                                    cdrom=False, rw=rw)
    return res


@mcp.tool()
async def msd_pull_files(
    image_name: str,
    out_dir: str,
    compress: Literal["lzma", "zstd", ""] = "lzma",
) -> dict:
    """Pull files (target machine -> workstation, "reversed" transfer).

    Requires the image to have been attached read-write (msd_push_files ...
    connect_target=True or msd_connect) and copied onto INSIDE the target
    (it appears as a USB drive there); then msd_disconnect() first, then this
    streams the image back (lzma by default; FAT images compress hugely),
    unpacks it with 7z into out_dir, and removes the image from KVM storage."""
    _g = _msd_guard()
    if _g: return _g
    from .msdfat import MsdFatError, unpack_fat_image

    image_name = os.path.basename(image_name)
    zpath = os.path.join(out_dir, f"{image_name}.{'xz' if compress == 'lzma' else 'zst' if compress == 'zstd' else 'raw'}")
    total = await client.msd_download_image(image_name, zpath, compress=compress)
    out_path = zpath.replace(".xz", "").replace(".zst", "").replace(".raw", "")
    if compress == "lzma":
        import lzma
        with open(zpath, "rb") as f:
            with open(out_path, "wb") as g:
                g.write(lzma.decompress(f.read()))
        os.remove(zpath)
    elif compress == "zstd":
        return {"ok": True, "downloaded": total, "downloaded_to": zpath,
                "note": "zstd: run `zstd -d` yourself or pass compress='lzma'"}
    files = await unpack_fat_image(out_path, os.path.join(out_dir, "extracted"))
    sizes = {f: os.path.getsize(f) for f in files}
    return {"ok": True, "image_bytes": os.path.getsize(out_path), "downloaded": total,
            "out_dir": os.path.join(out_dir, "extracted"), "files": files, "sizes": sizes}


@mcp.tool()
async def msd_connect(image_name: str, cdrom: bool = False, rw: bool = True) -> dict:
    """Attach a stored MSD image to the target as a USB disk (default rw flash)
    or CD-ROM (cdrom=True, always read-only). The target OS sees it immediately."""
    _g = _msd_guard()
    if _g: return _g
    return await client.msd_connect(os.path.basename(image_name), cdrom=cdrom, rw=rw)


@mcp.tool()
async def msd_disconnect() -> dict:
    """Detach the virtual drive from the target (eject first on the target if
    it is rw-mounted; kvmd busy flag guards some cases)."""
    _g = _msd_guard()
    if _g: return _g
    return await client.msd_connect(connected=False)


@mcp.tool()
async def msd_remove_image(image_name: str) -> dict:
    """Delete an image from the KVM's MSD storage."""
    _g = _msd_guard()
    if _g: return _g
    await client.msd_remove(os.path.basename(image_name))
    await asyncio.sleep(1.2)
    st = await client.msd_state()
    return {"ok": True, "images_now": list(st.get("storage", {}).get("images", {}))}


# ---------------------------------------------------------------- state / helpers

@mcp.tool()
async def refresh_auth() -> dict:
    """Force a fresh KVM login and report status without returning credentials."""
    try:
        await client.login(force=True)
    except Exception:
        return {"ok": False, "error": "KVM authentication refresh failed"}
    return {"ok": True}

@mcp.tool()
async def human_activity(motion: bool = True) -> dict:
    """Detect concurrent human interference: compares the HID inactivity counter
    (any input into the target - ours or a human's) against this tool's last send
    time, plus an optional screen-motion probe. Returns verdict + warning flag."""
    out = await client.interference_check()
    if not motion and "screen" not in out:
        for k in ("screen",):
            out.pop(k, None)
    return out


@mcp.tool()
async def snapshot_info() -> dict:
    """Target display resolution + one fresh frame hash (cheap freshness probe)."""
    _, sha = await client.snapshot()
    return {"ok": True, "resolution": {"width": client.resolution[0], "height": client.resolution[1]},
            "frame_sha": sha}


@mcp.tool()
async def kvmd_api(method: Literal["GET", "POST"], path: str,
                   query: dict[str, Any] | None = None,
                   data: dict[str, Any] | None = None) -> Any:
    """Raw passthrough to the kvmd HTTP API (auth handled), e.g.
    GET /api/hid (LEDs), GET /api/hid/inactivity, GET /api/msd, /api/log ...

    path is the kvmd path under /api (e.g. "/api/hid"). Returns parsed JSON."""
    if not path.startswith("/api"):
        return {"error": "path must start with /api"}
    r = await client.request(method, path, query=query, data=data)
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code, "text": r.text[:500]}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
