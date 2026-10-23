# glkvm-mcp

This is the [ShoGinn fork](https://github.com/ShoGinn/glkvm-mcp) of
[GlassOnTin/glkvm-mcp](https://github.com/GlassOnTin/glkvm-mcp). The original
implementation and hardware calibration come from upstream. Fork changes add
bounded text observations, authentication recovery, input reliability, and
local quality checks. See [fork stewardship](docs/FORK.md), the
[changelog](CHANGELOG.md), [contributing](CONTRIBUTING.md), and
[security guidance](SECURITY.md).

MCP server for the GLKVM KVM session: accurate mouse, complete keyboard, OCR element tree, human-interference detection. It drives a GL.iNet GLKVM (PiKVM kvmd fork) over its HTTPS API, with no agent installed on the target; the main control path uses USB HID and HDMI capture; optional MSD tools also expose USB storage.

Upstream reports building and verifying against a GL.iNet RM10 running kvmd 4.82 (GL fork). Other models and firmware versions require their own validation. Stock upstream PiKVM is untested: the shared request driver retries a GET once after authentication recovery for a 401 or the GL fork's stale-token 403 response and falls back to screen-motion probing when `/api/hid/inactivity` 404s, these compatibility paths have not been verified against stock kvmd.

## Requirements

- Python 3.12+ with uv
- A GLKVM device reachable over HTTPS, and its web UI password
- OCR runs locally with rapidocr-onnxruntime on CPU. OCR text and tool results go to your MCP client; `screenshot` returns image content to that client.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GLKVM_HOST` | `glkvm.lan` | Device hostname |
| `GLKVM_USER` | `admin` | Web UI user |
| `GLKVM_PASSWORD` | unset | Web UI password, required |
| `GLKVM_BASE` | `https://<GLKVM_HOST>` | Full base URL override |
| `GLKVM_ALLOW_MSD` | unset | Set to `1`/`true`/`yes` to enable the `msd_*` tools |
| `GLKVM_ARTIFACT_DIR` | `~/.local/share/glkvm-mcp/artifacts` | Private local directory for frames explicitly saved by `observe_screen` |

## Installation and startup

Clone the fork and install the locked dependencies:

```sh
git clone https://github.com/ShoGinn/glkvm-mcp.git
cd glkvm-mcp
uv sync --locked
```

Provide `GLKVM_PASSWORD` through your MCP host's secret/environment mechanism.
For a local launch with that variable already exported:

```sh
uv run --frozen python -m glkvm_mcp.server
```

The server uses stdio. Configure the host to launch from the checkout; if its
working directory is elsewhere, use uv's `--directory` option with the absolute
checkout path. The following Claude Code `.mcp.json` example inherits the password
from the launching environment (do not commit credentials):

```json
{
  "mcpServers": {
    "glkvm": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/glkvm-mcp", "--frozen", "python", "-m", "glkvm_mcp.server"]
    }
  }
}
```

The configuration variables above are read at startup, except the MSD opt-in
and artifact directory, which are checked when those tools are called.

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

Python 3.14 is pinned in `.python-version` and CI. The package declares Python
3.12+; the automated workflow currently checks only 3.14. Sync and run the same checks
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

The `msd_*` tools use the KVM's emulated USB mass-storage drive: push wraps files in a FAT image and attaches it as a USB disk on the target; pull streams the image back after the target writes files to it. They are disabled by default because attaching USB storage is visible to the target (and to any EDP/security tooling on it). HID actions also affect the target and may be observed or audited. Keep MSD off unless the target owner authorizes removable storage. This flag gates only the named `msd_*` wrappers; `kvmd_api` can still POST directly to `/api/msd`, so it is not a security boundary.

## Accuracy notes

Absolute mouse mapping was measured at 1px error on 2560x1440 firmware. GL maps only the middle 90% of the screen width to the axis range; the constants live in `glkvm_mcp/kvmd.py` and are fractions of the frame size, so other resolutions need a re-measure. OCR boxes are pixel-accurate for line boxes, a few px for word boxes.

The upstream README reports live verification of keyboard (keys, combos, Unicode typing over `/api/hid/print`), mouse absolute/relative/wheel/drag, caps-lock LED echo probe, snapshot, OCR tree, MSD push/pull byte-exact roundtrip. Upstream lists these as not exercised live: `msd_connect`/`msd_disconnect` attach/detach (deliberately - it injects a USB-drive event into the session in use), horizontal wheel direction, and `human_activity` against a genuinely concurrent human operator.

Fork tests use mocks and local artifacts; they do not establish fresh hardware
verification. For each hardware check, record the commit, model, firmware,
resolution, tools exercised, and outcome. No console actions are needed to run
the offline checks above.

## Troubleshooting

- Authentication failure: check the host and user, ensure the password reaches
  the server environment, then use `refresh_auth`. Never share tokens or raw
  response bodies in an issue.
- Failed capture: discard the previous observation for action planning. Obtain
  a successful fresh observation before sending input.
- Uncertain typing result: inspect the screen before retrying; text may already
  have reached the target.
- Artifact save failure: use a directory owned by the server user with owner-only
  access and write permission. The tool refuses an existing permissive directory.
- Dependency or tool mismatch: run `uv sync --locked`, restart the MCP host, and
  obtain a fresh observation. Confirm it launches the intended checkout.

See [SECURITY.md](SECURITY.md) for TLS, token-cache, and data-handling limitations.

## License

AGPL-3.0-or-later, see `LICENSE`.
