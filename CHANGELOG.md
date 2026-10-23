# Changelog

This file records modifications in the ShoGinn fork. Version numbers describe
fork package metadata; they do not imply an upstream release or acceptance.
Original authorship and history remain in Git. See [fork stewardship](docs/FORK.md).

## Unreleased

### 2026-10-22 — documentation and fork stewardship

- Identify upstream and fork provenance; document contribution, synchronization,
  review, licensing, and release responsibilities.
- Correct setup and data-flow guidance; document TLS, token-cache, raw API,
  artifact-retention, and MSD limitations.
- Separate inherited hardware claims from fork validation requirements.

## 0.1.1 — fork changes dated 2026-10-22

Merged into fork main as `01cc21c` (pull request #1), including development
commit `8a8c810`. No release tag is asserted here:

- Bound shared HTTP operations and typing work; do not automatically replay
  posted HID input after authentication rejection. Report uncertain typing
  outcomes and validate input before sending it.
- Improve observation diagnostics and add bottom-line OCR selection.
- Add locked uv development tooling, Python 3.14 pinning, and CI checks for Ruff,
  ty, and the offline unittest suite.

### Earlier fork changes — 2026-10-19

Recorded in commit `28d02ad`:

- Add bounded text-only `observe_screen`, fresh-capture provenance, OCR cache
  metadata, and optional private frame artifacts.
- Add `refresh_auth` and optional typing-result echo suppression.

## Upstream baseline — 2026-10-06

Commit `5428b80` from GlassOnTin/glkvm-mcp, package version 0.1.0: original
HTTPS GLKVM driver, screen/OCR tools, HID input, activity detection, raw API,
and opt-in mass-storage tools under AGPL-3.0-or-later. Hardware claims in the
README originate from this baseline and are not new fork validation.
