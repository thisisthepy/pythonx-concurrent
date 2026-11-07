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
| Temporary files | the session scratchpad directory, or `.cache/` (git-ignored); delete when done |
| CI scripts | `.github/scripts/` |

Before writing a file, check that its absolute path starts with this repository's root. If it does
not, stop. The only exceptions are a path the user names explicitly, and caches that build tools
manage themselves. **Re-pointing a shared cache or a home-directory symlink reaches other projects.
Ask first.**

Writing to *another* repository is not an exception either. Do it only when told to work there.

### Do not add top-level folders

Do not create new folders or files at the repository root on your own. Temporary files go in the
git-ignored `.cache/`, CI scripts in `.github/scripts/`.

The standing root entries are `README.md`, `AGENTS.md`, `LICENSE`, `.gitignore`, `.github/`, `docs/`,
`pyproject.toml`, `pythonx/` (the package; added with the maintainer's approval on 2026-10-04 to
reserve the name on PyPI) and `tests/` (approved by the maintainer, 2026-10-24). If another top-level
entry seems necessary (for example a Kotlin module), propose it (what it is, why, and why it cannot
live inside an existing directory) and wait for approval.

## 3. Worktrees link large artefacts instead of copying them

A worktree is a full checkout. Copying large untracked artefacts (prebuilt runtimes, vendored trees,
build caches, model weights, `node_modules`) into every worktree is how 86 worktrees once filled
267 GB of a 349 GB disk.

- Create worktrees under `.worktrees/<name>`.
- **Symlink** large untracked directories from the main checkout instead of copying or rebuilding
  them. If a worktree-linking script exists under `.github/scripts/`, use it.
- **When a branch merges, clean up in the same step**: remove its worktree
  (`git worktree remove .worktrees/<name>`), delete the local and remote branch, and delete every
  build output, cache or target directory created for that work (per-branch `build/`, a
  `CARGO_TARGET_DIR`, a venv, a scratch directory). Work is not finished while any of these remain.
  This was added after 32 per-agent cargo target directories (33 GB) were left behind in one
  repository.
- Never create a per-agent or per-branch build cache (a separate `CARGO_TARGET_DIR`, a copied
  `.gradle`, a private venv). Use the repository's one canonical location.

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

### What counts as the owner's decision

Added after an agent turned the owner's question "should ExecuTorch be the NPU back end?" into an
offline per-model `.pte` pipeline with its own CI, recorded it as "decided, do not re-discuss" and
later as project intent. The owner had approved none of it (torchnative, 2026-08 to 2026-11).

- **A question is not a decision.** "X를 쓰는게 좋을까?", "should we use X?" opens a decision. If the
  owner did not answer, it stays open; never record it as settled.
- **Approving a goal is not approving a means.** "Make the Snapdragon NPU work, go ahead" approves
  the goal. The engine, the dependencies, the pipeline, the CI workflow and the artefact types an
  agent picks to reach it are separate decisions.
- **Never restate the owner's question as a different one.** When asking for a decision, quote the
  owner's words and the constraint behind them (for example "NNAPI is deprecated, so what
  abstraction reaches every vendor's NPU?"), and give options that answer that question.
- **Ask before adding** a dependency, a pipeline, a CI workflow or a kind of artefact the owner did
  not name. Show the options and wait.
- **Docs keep provenance.** A line in INTENT, SPEC, AGENTS or a design doc that states a direction
  cites the owner's words (date and source). Anything else is marked "agent proposal, not
  confirmed". Never write "decided", "settled" or "do not re-discuss" for a choice the owner did not
  make, and keep that mark when a doc is restructured.

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
- **Agents do not build.** Every agent, teammate sessions and subagents alike, may check that code
  compiles and nothing more: `cargo check`, a Gradle `compile*` task, `python -m py_compile`, a
  type checker or linter. Full builds (`cargo build`, `assemble`, wheels, native images, app
  packages), test suites, gates, benchmarks and model runs go to CI (GitHub Actions) on a pushed
  branch. The only local exceptions are device tests and measurements the coordinating session
  runs itself, one at a time, and says why. Several sessions share one machine, and agent builds
  once filled the disk and slowed every session.

---

# Repository-specific rules: `pythonx-concurrent`

## 11. What this repository is

`pythonx-concurrent` is a pythonx library: Python language extensions for apps that run on Kotlin
Multiplatform through python-multiplatform. It makes concurrency comfortable in that setting. It
bridges `asyncio` and Kotlin coroutines, gives Python structured concurrency with Kotlin's
vocabulary, and plans a goroutine-style scheduler on free-threaded CPython.

Status: Milestone 1 implemented (`Scope`, `supervisor_scope`, `CoroutineScope`, `Job`, `Deferred`,
`Dispatchers`, `KotlinEventLoop`, `with_context`, `run_blocking`, `run_blocking_call`, `with_timeout`,
`with_timeout_or_none`, `non_cancellable`, `CancelToken`). `docs/INTENT.md` is the boundary,
`docs/SPEC.md` is the behavioural contract, `docs/research.md` holds the evidence.

| Path | What it is |
|---|---|
| `README.md` | Short English overview |
| `docs/locale/README_ko.md` | Korean overview |
| `docs/INTENT.md` | What the project is for, and what it is not |
| `docs/SPEC.md` | Numbered behaviour entries, each with a status |
| `docs/research.md` | Findings with sources, and per-platform feasibility |
| `pythonx/concurrent/` | Pure-Python implementation |
| `tests/` | Test suite (CPython 3.15 and free-threaded 3.15t) |
| `AGENTS.md` | This file |
| `LICENSE` | Apache-2.0 |

## 12. Rules for this repository

**`pythonx` is a namespace package shared by every pythonx library.** Never add
`pythonx/__init__.py`: pythonx-compose, pythonx-concurrent, pythonx-graphics and pythonx-platform
all install into `pythonx`, and an `__init__.py` from any one of them would hide the others. Each
package owns only `pythonx/<its name>/`. The publish workflow refuses a wheel that contains
`pythonx/__init__.py`.

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
