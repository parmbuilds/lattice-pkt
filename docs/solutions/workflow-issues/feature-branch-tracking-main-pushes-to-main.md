---
title: A feature branch cut from origin/main tracks main, so a sync pushes to main
date: 2026-10-15
category: workflow-issues
module: git workflow
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Creating a feature branch with `git switch -c <branch> origin/main` or `git checkout -b <branch> origin/main`"
  - "Working in VS Code with `git.confirmSync` set to false, or any tool that pushes to the upstream with an explicit `branch:upstream` refspec"
  - "An agent commits on the branch while a human has the same checkout open in an editor"
tags: [git, upstream, push, vscode, sync, branch-tracking, main]
---

# A feature branch cut from origin/main tracks main, so a sync pushes to main

## Context

The document ingestion work was built on `feat/document-ingestion-rag`, created with `git switch -c feat/document-ingestion-rag origin/main`. Agents committed each unit locally and never pushed. Even so, eight commits reached `main` on GitHub without a pull request: the ideation-and-plan docs commit and units U1 to U7. The remote-tracking reflog shows two pushes to `origin/main` from this checkout, at 19:23 and 19:57, a few minutes after the U6 and U7 commits. No build-phase agent session log contains a `git push`.

The cause is the branch's upstream. Creating a branch from a remote-tracking ref sets that ref as its upstream (git's `branch.autoSetupMerge` default), so `git branch -vv` showed `[origin/main: ahead 5]`. VS Code's "Sync Changes" pushes the current branch to its upstream with an explicit `feat/...:main` refspec. With `git.confirmSync: false` in the user settings, one click pushes with no prompt.

A plain `git push` does not do this. Under the default `push.default=simple` it refuses when the branch name differs from its upstream's ("The upstream branch of your current branch does not match the name of your current branch"). That is why the trap is easy to miss from the terminal: only tools that pass the upstream explicitly, or a repo with `push.default=upstream`, push to `main`.

## Guidance

Never leave a feature branch tracking `main`. Either create it without tracking:

```bash
git switch --no-track -c feat/my-change origin/main
```

or drop the upstream straight after creating it:

```bash
git switch -c feat/my-change origin/main
git branch --unset-upstream
```

Publish it under its own name the first time you push:

```bash
git push --set-upstream origin HEAD   # creates origin/feat/my-change and tracks it
```

Check before handing a checkout to someone else, or before leaving an editor open on it:

```bash
git branch -vv | grep '^\*'   # must not say [origin/main ...]
```

## Why This Matters

A sync from a branch that tracks `main` publishes unreviewed work to the default branch. CI may still pass, as it did here, but the change skips review, and taking it back needs a force-push to `main`. In this run the full review afterwards found real defects in the commits that had landed, including an upload-slot exhaustion path in the upload gate and refusal reasons shown to uploaders as raw codes.

## When to Apply

- Any branch created from `origin/main` or another remote-tracking ref, by a person or an agent
- Shared checkouts where an agent commits and a person also has the repo open in VS Code, Cursor or another editor with a sync button
- Before turning on `git.confirmSync: false` in an editor that syncs to the upstream
- In a repo or user config that sets `push.default=upstream`, where a bare `git push` also follows the upstream

## Examples

Before. The branch tracks `main`, and one Sync click publishes the feature commits to `main`:

```text
$ git branch -vv
* feat/document-ingestion-rag <sha> [origin/main: ahead 5] fix(ui): ...
$ git reflog show refs/remotes/origin/main
a5bc5be refs/remotes/origin/main@{...19:57:09}: update by push
9a9a249 refs/remotes/origin/main@{...19:23:50}: update by push
```

After. The branch tracks its own remote branch:

```text
$ git branch --unset-upstream
$ git push --set-upstream origin HEAD
 * [new branch]      HEAD -> feat/document-ingestion-rag
$ git branch -vv
* feat/document-ingestion-rag <sha> [origin/feat/document-ingestion-rag] ...
```

## Related

- `docs/plans/2026-10-15-1737-feat-document-ingestion-rag-plan.md`: the plan whose units landed on `main` early.
