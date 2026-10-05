---
name: release
description: Cut a version tag and trigger the three-platform GitHub release build. Verifies the tree is clean and green, drafts release notes from the commit log, then tags and pushes.
disable-model-invocation: true
---

Cut a release. Pushing a `v*` tag is what triggers the build and publishes binaries — this is the only way a release happens in this repo.

**This has irreversible, outward-facing effects.** It publishes executables under the user's GitHub account. Confirm the version number with the user before tagging, and never push a tag without an explicit go-ahead.

## How releasing works here

There is **no version string anywhere in the source** — no `__version__`, nothing in `app.spec` or `README.md`. The git tag *is* the version. So a release is: verify, tag, push.

`.github/workflows/build.yml` then runs three jobs in sequence:

1. `test` — `ruff check .` and `python -m pytest` on Python 3.12
2. `build` — PyInstaller onefile on Linux, Windows, and macOS
3. `release` — only on `refs/tags/*`; downloads all three artifacts and creates a GitHub Release via `softprops/action-gh-release`

The release job publishes `aacdeck` (Linux), `aacdeck.exe` (Windows), and `aacdeck-macos`. It supplies **no release body**, so notes must be added afterwards.

## Steps

### 1. Verify the tree

```bash
git status --short && git log --oneline origin/main..HEAD
```

Stop if there are uncommitted changes or unpushed commits. Tagging must happen on a commit that exists on `origin/main`.

### 2. Verify it's green locally

```bash
.venv/bin/ruff check . && .venv/bin/python -m pytest -q
```

Note the local venv is Python 3.14 while CI pins 3.12 — a local pass is strong evidence but not proof. Also confirm the last CI run on `main` succeeded:

```bash
gh run list --branch main --limit 3
```

### 3. Pick the version

```bash
git tag -l | sort -V | tail -5
git log --oneline "$(git describe --tags --abbrev=0)..HEAD"
```

Semver against the previous tag: breaking or reworked UI → major; new tool/tab or user-visible feature → minor; fixes and dependency bumps only → patch. **Propose a number and get the user's confirmation before continuing.**

### 4. Draft the notes

Group the commits since the last tag under headings the user will care about — this is an end-user tool, so lead with what changed for them, not internal refactors:

- **Features** — new tabs, new outputs, UX changes
- **Fixes** — bugs, crashes
- **Security** — CVE-driven dependency bumps (call these out explicitly; there have been several)
- **Internal** — tests, CI, docs, refactors (keep brief, last)

Show the draft to the user before tagging.

### 5. Tag and push

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

Only after the user has confirmed both the version and the notes.

### 6. Attach the notes and watch the build

```bash
gh run watch
```

The build takes roughly 3 minutes. Once the release exists, attach the drafted notes:

```bash
gh release edit vX.Y.Z --notes-file <path>
```

Then confirm all three binaries are present:

```bash
gh release view vX.Y.Z
```

Report the release URL to the user.

## If the build fails after tagging

The tag is already public, so don't delete and re-push it. Fix forward: land the fix on `main` and cut the next patch version. If the `release` job failed but binaries built cleanly, re-running just that job may be enough:

```bash
gh run rerun <run-id> --failed
```
