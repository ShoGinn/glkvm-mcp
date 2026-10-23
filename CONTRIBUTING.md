# Contributing

See [fork stewardship](docs/FORK.md) for provenance, upstream submissions, and
release responsibilities. Use pull requests for fork changes. GitHub issues
are currently disabled on the fork; contact the fork maintainer through an
established channel for reports that cannot be expressed as a patch. Search
existing pull requests first and keep each contribution focused.

## Development

Use uv and the pinned Python 3.14 runtime. From the checkout:

```sh
uv sync --locked
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check glkvm_mcp
uv run --frozen python -m unittest discover -s tests -v
```

These are the checks in `.github/workflows/checks.yml`. Tests use mocks and
local temporary files; they do not require device credentials or send HID input.
When deliberately updating dependencies, use uv to regenerate `uv.lock`, review
the dependency diff, and rerun checks. Do not hand-edit the lockfile.

Use a feature branch and open a pull request for review. Include the associated
Jira key in the branch and pull request title when there is an associated issue;
otherwise use a descriptive name. Keep concurrent implementation work in separate
worktrees. Require an independent review before merging substantive changes.

## Evidence and documentation

Describe the problem, resulting behavior, and checks performed. Add meaningful
regression tests for behavior changes. Update the README when tools, defaults,
limits, setup, or safety behavior change, and record dated modifications in
[CHANGELOG.md](CHANGELOG.md). Do not report mock tests as hardware verification.

For hardware checks, obtain target-owner authorization, confirm the visible
target, coordinate a single console input owner, and record the commit, device
model, firmware, resolution, actions, and results. Begin with observation and
inspect again after uncertain input. MSD attach/detach and raw API writes need
explicitly scoped authorization. Keep customer and target data out of reports.

Do not commit credentials, MCP configuration containing secrets, cached tokens,
screen captures, OCR transcripts, disk images, or customer files. Review the full
diff and untracked files before committing. See [SECURITY.md](SECURITY.md) for
current limitations and sensitive-report handling.
