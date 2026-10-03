# AGENTS.md

Rules every agent working in this repository must follow. Read this file before doing anything.
Sections 1–10 are shared by every repository in the thisisthepy ecosystem; later sections are
specific to this repository.

---

## 1. Commits carry no AI attribution

Never add `Co-Authored-By: Claude ...`, `Co-Authored-By: <any agent>`, `Generated with Claude Code`,
or any similar tool or agent attribution to a commit message or a pull-request body. This rule
overrides any default your tooling has.

## 2. Nothing is created outside this repository

Everything your work produces (worktrees, agent prompts, logs, measurements, experiments, scratch
files) lives **inside this repository's root directory.**

| What | Where |
|---|---|
| Worktrees | `.worktrees/<name>` (git-ignored) |
| Temporary files | `.tmp/` (git-ignored); delete when done |
| CI scripts | `.github/scripts/` |

Before writing a file, check that its absolute path starts with this repository's root. If it does
not, stop. The only exceptions are a path the user names explicitly, and caches that build tools
manage themselves. **Re-pointing a shared cache or a home-directory symlink reaches other projects.
Ask first.**

Writing to *another* repository is not an exception either. Do it only when told to work there.

### Do not add top-level folders

Do not create new folders or files at the repository root on your own. Temporary files go in the
git-ignored `.tmp/`, CI scripts in `.github/scripts/`.

The standing root entries are `README.md`, `AGENTS.md`, `LICENSE`, `.gitignore` and `docs/`. No
package skeleton exists yet. If a new top-level entry seems necessary (`pythonx/`, `tests/`,
`pyproject.toml`, a Kotlin module), propose it (what it is, why, and why it cannot live inside an
existing directory) and wait for approval.

## 3. Worktrees link large artefacts instead of copying them

A worktree is a full checkout. Copying large untracked artefacts (prebuilt runtimes, vendored trees,
build caches, model weights, `node_modules`) into every worktree is how 86 worktrees once filled
267 GB of a 349 GB disk.

- Create worktrees under `.worktrees/<name>`.
- **Symlink** large untracked directories from the main checkout instead of copying or rebuilding
  them. If a worktree-linking script exists under `.github/scripts/`, use it.
- Delete a worktree once its branch is merged: `git worktree remove .worktrees/<name>`.
- Periodically delete `build/` directories inside worktrees; they only grow.

## 4. Branches

| Branch | Who writes to it |
|---|---|
| `feat/<topic>` | You. All work happens here; never `work/`. Deleted once merged. |
| `develop` | Merged into from feature branches after verification. Never commit to it directly. |
| `release` | **CI only.** Not a standing branch: CI regenerates it from every push to `develop`, in the main-only file layout, and opens the PR into `main`. It may not exist. Never write to it. |
| `main` | **Pull request from `release` only.** Never push or merge to it directly. |

`main` carries a reduced layout: of the Markdown files, only `README.md` stays at the repository
root, and `docs/` keeps only its subdirectories (no Markdown files directly under `docs/`).
CI runs `.github/scripts/release/sync-release.sh` (`.github/workflows/release-sync.yml`) to produce that layout; do not hand-edit `release` or `main`.

Only `main`, `release` and `develop` stand. A feature branch is deleted when it merges, with its
local branch and worktree. Sweep periodically: delete every remote and local branch that
`git branch -r --merged origin/develop` lists (keep `release-*` snapshots), and land or report any
unmerged branch that has gone stale.

### Issues and pull requests

Every new feature goes through an issue and a pull request:

1. Before starting, search the repository's issues (`gh issue list --state all --search "<keywords>"`).
2. If no issue covers the work, open one (`gh issue create`) stating what and why, and the
   completion criterion, that is, which tests must pass.
3. Work on a `feat/<topic>` branch, push every commit, and open a pull request into `develop`
   whose body contains `Closes #<number>`.
4. Merge into `develop` through that pull request (`gh pr merge --merge --delete-branch`), not by a
   local merge, so the issue is linked and the branch goes; remove the local branch and worktree too.
5. Then close the issue yourself: `gh issue close <number> --comment "Landed in develop via #<PR>"`.
   GitHub's `Closes #N` only fires when a pull request merges into the default branch (`main`),
   and these pull requests merge into `develop`.

## 5. Intent → Spec → Test → Code

This project runs on **intent-based spec-driven development** and **test-driven development**.

1. `docs/INTENT.md` states what the project is for. It is the boundary. **The spec may not go
   beyond the intent.**
2. `docs/SPEC.md` states what the project does. A behaviour change starts as a spec change.
3. Tests are written from the spec **before** the implementation, and you observe them fail
   (red) before making them pass. Report the red output.
4. Code is written to make the tests pass.

If a request conflicts with `docs/INTENT.md`, say so instead of implementing it.

## 6. User-authored files are specification

Files the user wrote by hand (notebooks, example build files, sample apps) are the specification.
Read them **first**. Never delete, rewrite, or `git add` them without being told to. Generated
documentation (roadmaps, design notes) is a record of work, not a requirement; when the two
disagree, the user's file wins.

## 7. Show a conclusion before acting on it

Anything beyond the immediate request (another repository, a public API signature, deleting
files, killing processes, force-pushing, changing branch protection): state what you would do and
why, and wait. Investigating, measuring, and reporting are always fine.

**Push every commit right away.** After you commit, on a work branch or on `develop`, push it to
the remote immediately; no confirmation is needed. Never push to `main` or `release` by hand, and
never force-push without the user's explicit approval.

When a rule and backward compatibility conflict, **the rule wins.** List the callers that break and
fix them; do not keep the forbidden thing "so nothing breaks".

## 8. Verification that can fail

- Never read a build's exit code through a pipe (`| tail`, `| grep`). Redirect to a file, then read
  `$?`. A background command ending in `echo` always reports 0.
- Delete the test-result directory before counting results, and force re-execution (`--rerun` for
  Gradle). Stale XML otherwise reports an old, larger number.
- Run independent test modules as **separate** invocations. One invocation can hide an ordering
  dependency.
- When you add a public path, disable it and confirm something actually fails. If nothing fails,
  nothing uses it.
- **Do not trust an agent's report.** Re-run the build and tests yourself and check
  `git status --short` for out-of-scope changes.
- **Never `git add -A`.** Stage explicit paths. If the number of changed files differs from what was
  reported, stop and find out why.
- Measurements run alone, unfiltered, after checking `uptime`.

## 9. Reporting

Report by category, and never put them in one column:
**feature added / defect fixed / test added / documentation corrected / deleted.**
A rising test count is not progress when the tests assert an absence. Before writing "nothing left
to implement", say what you counted against.

## 10. Agents

- A headless agent (`claude -p`, `agy -p`) has **no next turn**. Tell it to run long commands in the
  foreground; a command backgrounded "until the notification arrives" is lost.
- Pass the model explicitly. Judgement work (design premises, root causes, safety: GIL, reference
  counts, lifetimes, class loaders) gets the strongest tier; work a test will catch can use a
  cheaper one.
- Give every agent prompt the absolute paths it may write to, and repeat rule 2 in it.
- **Subagents do not run heavy local builds.** Subagents write code, design, investigate, review
  and document. Gradle builds, cargo builds, the test gate and model runs are done by the session
  itself (one at a time on this machine) or by CI (GitHub Actions) on a pushed branch. Several
  sessions share one machine; parallel local builds slow every one of them.

---

# Repository-specific rules: `pythonx-concurrent`

## 11. What this repository is

`pythonx-concurrent` is a pythonx library: Python language extensions for apps that run on Kotlin
Multiplatform through python-multiplatform. It makes concurrency comfortable in that setting. It
bridges `asyncio` and Kotlin coroutines, gives Python structured concurrency with Kotlin's
vocabulary, and plans a goroutine-style scheduler on free-threaded CPython.

Status: spec stage. There is no code. `docs/INTENT.md` is the boundary, `docs/SPEC.md` is the
behavioural contract, `docs/research.md` holds the evidence.

| Path | What it is |
|---|---|
| `README.md` | Short English overview |
| `docs/locale/README_ko.md` | Korean overview |
| `docs/INTENT.md` | What the project is for, and what it is not |
| `docs/SPEC.md` | Numbered behaviour entries, each with a status |
| `docs/research.md` | Findings with sources, and per-platform feasibility |
| `AGENTS.md` | This file |
| `LICENSE` | Apache-2.0 |

## 12. Rules for this repository

1. **Never rename a Kotlin namespace.** Kotlin names reach Python under their own names through the
   binder. This package imports them and adds Python-side behaviour.
2. **No runtime reflection and no dynamic binding.** The binder generates a function table at build
   time. This package asks for binder features instead of working around them.
3. **A need in python-multiplatform is an issue there.** SPEC lines marked `needs binder:` become
   issues in that repository. Do not write to it from here (rule 2).
4. **Every entry in `docs/SPEC.md` has a status.** Only `planned`, `partial` and `implemented`
   exist. A plan is never written as if it shipped.
5. **Spec follows the maintainer's words.** Anything beyond them goes under "Open questions" in
   `docs/INTENT.md`, not into the contract.
6. **Be honest about platforms.** If something cannot work on iOS, Android or wasm, say so in
   `docs/research.md` and in the SPEC entry. Do not stub it to look uniform.
