# Fork stewardship

This fork is maintained at <https://github.com/ShoGinn/glkvm-mcp> and derives
from <https://github.com/GlassOnTin/glkvm-mcp>. Preserve upstream authorship,
commit history, copyright notices, and the AGPL-3.0-or-later license. The
upstream baseline for the existing fork changes is commit
`5428b80` (2026-10-06); this records provenance, not a claim that upstream has
no newer commits.

## Scope and upstream contributions

Keep this a focused GLKVM MCP implementation. Prefer small, independently
reviewable fixes that upstream can adopt without personal agent configurations,
customer details, credentials, or environment-specific policy. Report problems
introduced by this fork to its maintainer first (fork issues are currently
disabled; use a pull request for a proposed fix or an established contact channel). Before proposing a general fix
upstream, check its current code, contribution guidance, and existing issues or
pull requests; explain the reproducible problem and offer a minimal patch with
validation. Record any upstream issue or pull request in the changelog once it
actually exists. A local patch is not an upstream submission or acceptance.

## Updating from upstream

Remote names are local conventions. This checkout currently uses `origin` for
GlassOnTin and `shoginn` for ShoGinn; clones of the fork normally use `origin`
for ShoGinn instead. Run `git remote -v` before fetching or pushing. Do not
rename existing remotes or push to upstream as part of a routine sync.

For a clone of the fork with no `upstream` remote yet:

```sh
git remote add upstream https://github.com/GlassOnTin/glkvm-mcp.git
git fetch upstream
```

On a feature branch, compare the fetched upstream tip with the fork baseline
and review every incoming change. Merge the intended upstream revision through
a pull request; avoid rewriting published fork history or force-pushing shared
branches. Resolve conflicts explicitly, run the offline checks in
[CONTRIBUTING.md](../CONTRIBUTING.md), and document changed behavior and the new
baseline. Hardware-facing changes also need a separately authorized, bounded
live check with recorded firmware and results before being called live-verified.

## Release and review checklist

- Use a feature branch and an independent review; never commit directly to main.
- Update user-facing docs and the changelog with changes and relevant dates.
- Preserve attribution and license notices. When distributing modified source,
  include prominent modification notices and dates. When distributing binaries,
  provide the corresponding source as required by the license. If a modified
  version supports remote network interaction, prominently offer those users
  access to its corresponding source as required by AGPL section 13. See the
  exact obligations in [LICENSE](../LICENSE).
- Run locked dependency installation and the CI checks; distinguish mock tests
  from actual device validation. Review dependency and workflow updates too.
- Publish source matching the released revision, including its lockfile and
  build/run instructions; identify the revision and any unverified behavior.
- Keep fork version numbers and upstream release claims distinct. Do not imply
  upstream endorsement or acceptance of fork changes.

There is no automatic upstream synchronization or release publication workflow
in this repository. Check upstream during maintenance and before a release.
