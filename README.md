# glkvm-mcp

MCP server for the GLKVM KVM session: accurate mouse, complete keyboard, OCR element tree, human-interference detection. It drives a GL.iNet GLKVM (PiKVM kvmd fork) over its HTTPS API, with no agent installed on the target; the target sees only an HID composite device and the HDMI input.

Built and verified against a GL.iNet RM10 running kvmd 4.82 (GL fork). Other GLKVM models on the same firmware should behave the same. Stock upstream PiKVM is untested: the driver re-logins on the GL fork's stale-token 403 behaviour and falls back to screen-motion probing when `/api/hid/inactivity` 404s, none of which runs against stock kvmd.

## Requirements

- Python 3.12+ with uv
- A GLKVM device reachable over HTTPS, and its web UI password
- OCR runs locally: rapidocr-onnxruntime on CPU (frames are not sent anywhere)

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GLKVM_HOST` | `glkvm.lan` | Device hostname |
| `GLKVM_USER` | `admin` | Web UI user |
| `GLKVM_PASSWORD` | unset | Web UI password, required |
| `GLKVM_BASE` | `https://<GLKVM_HOST>` | Full base URL override |
| `GLKVM_ALLOW_MSD` | unset | Set to `1`/`true`/`yes` to enable the `msd_*` tools |
| `GLKVM_ARTIFACT_DIR` | `~/.local/share/glkvm-mcp/artifacts` | Private local directory for frames explicitly saved by `observe_screen` |

Setup and registration in Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "glkvm": {
      "command": "uv",
      "args": ["run", "--frozen", "python", "-m", "glkvm_mcp.server"],
      "env": { "GLKVM_PASSWORD": "your-device-password" }
    }
  }
}
```

## Tools

Screen:

- `screenshot` - live frame as an image, content-sized to taste
- `observe_screen` - fresh capture summarized as bounded OCR text, with native-pixel boxes, confidence estimates, capture receipt time, frame hash, and OCR cache metadata; it returns one JSON text block and no image/resource blocks. `line_selection="bottom"` selects the last screen lines for terminal prompts. `save_frame=true` stores a unique read-only PNG locally and returns its absolute path, byte count, hash, and provenance.
- `screen_elements` - OCR of the current frame into a JSON tree (blocks, lines, words, optional letters) with pixel bounding boxes
- `snapshot_info` - display resolution and a frame hash to test freshness

Input (coordinates are target-screen pixels):

- `click_at`, `click_text`, `move_mouse`, `mouse_relative`, `drag`, `scroll`
- `key`, `key_combo`, `type_text` - keys are KeyboardEvent.code names, so `MetaLeft` opens the target's Start menu; `type_text` sends arbitrary Unicode and supports `echo_text=false` to omit the typed value from its result

State and safety:

- `human_activity` - warns when input the tool did not produce is happening (keyboard/mouse inactivity counter, or screen motion as fallback)
- `refresh_auth` - forces a new KVM login and returns only success or a sanitized failure category
- `kvmd_api` - raw GET/POST passthrough for anything the driver does not wrap

`observe_screen` always requests a new KVM frame, even when its OCR result is
reused for identical pixels. `line_selection="top"` is the default. Selecting
`"bottom"` keeps the last lines in screen reading order, while returned lines
remain top-to-bottom. Truncation counts include lines outside that selected
window. Its UTC capture time is the local server's receipt
time: kvmd does not provide a device capture timestamp or verified physical
target identity. OCR confidence is an estimate, and low-confidence text may be
wrong. `max_lines` and `max_chars` bound the returned OCR payload and report any
truncation. Saved PNGs use unique names, a private directory, and read-only file
permissions; they remain local to the MCP server host. `echo_text=false` only
omits typed text from the tool result. MCP clients or server logs may still
record the input argument, so `type_text` is not a secret-safe channel. An
existing artifact directory must already be owner-only and writable; saving
refuses a permissive directory without changing its permissions.

Capture and authentication failures from `observe_screen` and `refresh_auth`
include a failure stage, category, and HTTP status when known. They omit
exception text, response bodies, URLs, and credentials. Normal HTTP and login
operations have a 30-second deadline and 5-second connect/pool timeouts; a slow
`type_text` call is limited to 9,000 characters and a 300-second typing deadline
after authentication; its total call is bounded by that deadline plus the
30-second login deadline. Per-chunk requests are bounded to 120 seconds. Delay
values are limited to 0–100 ms; oversized or over-budget requests are rejected
before input starts. A timed-out `type_text` request returns `status="uncertain"` because
kvmd may have received some or all of the input. Input requests are never
automatically retried; inspect the target before trying again.

Python 3.14 is the pinned development runtime because the OCR dependency does
not publish wheels for newer Python versions yet. Sync and run the same checks
used in CI with:

```sh
uv sync --locked
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check glkvm_mcp
uv run --frozen python -m unittest discover -s tests -v
```

After updating this checkout or its dependencies, restart the configured MCP
host and check a fresh observation before using HID tools. This confirms the
MCP connection and visible output but does not prove which physical target is
connected.

File transfer (opt-in via `GLKVM_ALLOW_MSD=1`, see below):

- `msd_state`, `msd_push_files`, `msd_pull_files`, `msd_connect`, `msd_disconnect`, `msd_remove_image`

The `msd_*` tools use the KVM's emulated USB mass-storage drive: push wraps files in a FAT image and attaches it as a USB disk on the target; pull streams the image back after the target writes files to it. They are disabled by default because attaching USB storage is visible to the target (and to any EDP/security tooling on it), while the HID + HDMI path stays quiet. For managed targets, keep the MSD tools off.

## Accuracy notes

Absolute mouse mapping was measured at 1px error on 2560x1440 firmware. GL maps only the middle 90% of the screen width to the axis range; the constants live in `glkvm_mcp/kvmd.py` and are fractions of the frame size, so other resolutions need a re-measure. OCR boxes are pixel-accurate for line boxes, a few px for word boxes.

Verified live: keyboard (keys, combos, Unicode typing over `/api/hid/print`), mouse absolute/relative/wheel/drag, caps-lock LED echo probe, snapshot, OCR tree, MSD push/pull byte-exact roundtrip. Not exercised live: `msd_connect`/`msd_disconnect` attach/detach (deliberately - it injects a USB-drive event into the session in use), horizontal wheel direction, and `human_activity` against a genuinely concurrent human operator.

## License

AGPL-3.0-or-later, see `LICENSE`.
