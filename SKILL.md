---
name: quick-wins
description: Orchestrate parallel agents to hunt down a batch of small, low-risk "one-liner" bugs in a codebase, fix each one in its own git worktree and branch, cross-check the fixes for false positives with independently-biased reviewers, prove the user-visible ones in a real browser against a running app, find the commit that introduced each bug, and then (after one confirmation) open a GitHub issue + PR per fix following the repo's conventions, put them on the project board, and babysit CI. Use this whenever the user asks for "quick wins", "low-hanging fruit", "small bugs", "one-liners", a bug sweep/bug bash, or to "find N small fixes and open PRs", even if they don't say "skill" or describe every step.
---

# Quick wins: find, fix, verify, ship

This is a pipeline the user runs repeatedly: find ~N tiny bugs, fix each in isolation, prove they are real, and publish one issue + one PR per fix. The value is in **volume with discipline**: many small, independently reviewable and revertable PRs, none of which is a false positive or a hidden regression.

Run autonomously through phases 1–4. **Stop once before phase 5** (anything that publishes to GitHub) and wait for the user's go-ahead, because issues, PRs, pushes and review requests notify people and can't be quietly undone.

Keep every scratch file (PR descriptions, the ship spec, the preview worktree, temp dumps) under `/tmp/quick-wins/<repo-name>/` (called `$SCRATCH` below), and fix worktrees under `<repo>-worktrees/`. Use the per-repo subdirectory, not bare `/tmp/quick-wins/` or `/tmp`: the user runs quick-wins in several repos at once, and a shared directory mixes their `PR-NN-*.md` files. The findings database lives in a third place, `~/.local/share/quick-wins/<repo-key>/` (see Memory). Keep to these three places: a person can pre-approve them once in their agent's permissions, and anything else outside the repo means a prompt for every file.

Keep a todo list with one item per phase, and update the user with one short line between phases. In phase 3.5, add one item per fix being checked and close it only when its before shot, its after shot, both annotations and the table in its PR text all exist: a picture that was never taken then shows in the list, not at review time.

## Memory: the findings database

Every finding of every round, and what became of it, is kept in one SQLite file per repository: `~/.local/share/quick-wins/<repo-key>/kb.db` (`<repo-key>` comes from `origin`, so every worktree of a repository shares it). It is the only list of findings. Read and write it only through `python3 -I scripts/kb.py -C <checkout> <command>` (`kb.py <command> --help` for each); never with SQL. Each round's findings are numbered `NN` (two digits), which also names `PR-NN-<slug>.md`, the evidence files, `checks.tsv` and `ship.json`.

- **Memory is evidence, never a verdict.** Only a `strong` match from `kb.py check` (same file, same snippet, same surrounding code, already shipped or already dropped) skips a candidate. Everything else is verified against the current code as if it were new.
- **Nothing from it goes into anything published**: no database ids, no stats, no "found before" in commits, PRs or issues.
- **Never store** who wrote a commit or a bug, customer data, or anything seen in a running app. Code, diffs, commit hashes and PR numbers are fine.
- **A memory-off round** (`kb.py round start --memory off`) still records everything but uses none of it. Offer one now and then, so the person can compare: it is the honest test of whether memory helps.
- Settings live in `config.json` beside the database; the defaults are in `scripts/kblib/store.py`.

## Whose conventions apply

None of this skill's own. How a commit subject reads, how a branch is named, what a PR title and body look like, which labels and reviewers a PR gets, whether a fix gets an issue: each of these is learned in phase 0, from the repository being swept and from what the person running the sweep already does there. Nothing is configured, in this skill or beside it, and nothing is carried over from another repository or an earlier run. (The findings database remembers findings, not conventions: these are learned again every round.) When sources disagree, the order is:

1. what the person says in this conversation;
2. what they do in this repository: their own recent commits and pull requests (subjects, titles, branches, body sections and length, labels, who they ask for review, whether an issue is linked);
3. the repository's written rules (`AGENTS.md`, `CONTRIBUTING.md`, the PR template, `CODEOWNERS`, commit-lint and hook config). Where a hook or a CI check enforces a rule, it wins over habit;
4. what other contributors' merged pull requests there look like, when the person has no history of their own;
5. nothing found: do the least (no issue, no label, no reviewer, the commit subject as the PR title) and say so at the phase 5 stop.

The formats that appear in this file (`fix/<slug>`, `#123 fix: ...`, `Closes #N`) illustrate what a repository might use. Never apply one because it is written here.

## Naming: keep this skill's name out of everything it produces

Never write "quick win", "quick wins", "quick-wins" or an abbreviation of it (`qw`, `QW_`) in anything the work produces: code, comments, commit messages, branch names, issue and PR titles and bodies, review comments, labels, file names inside the repo, database names, environment variables, test data, app keys, account names, or any made-up example name. Don't describe a fix as a quick win either. Reviewers should see a normal bug fix with a normal name; the batch it came from is the user's business, and the label undersells the fix.

Name things for what they are: `fix/<issue>-<slug>`, `verify_<repo>`, `Local API check`. Put this rule in every subagent prompt, because subagents write the commits and PR texts. The only places the phrase may appear are this skill's own directory and the local scratch directory under `/tmp`, which nobody else sees. Before publishing, grep the diffs, the PR and issue texts and the evidence for it (`grep -rniE 'quick.?wins?|\bqw[_-]'`) and fix any hit.

## Which model does what

The phases need different amounts of judgment. Where the session can spawn subagents on other models (the subagent tool's `model` parameter), match the model to the work; a model the user names in the request decides. Where it can't, everything runs on the session's model.

| Work | Model | Why |
|---|---|---|
| Phase 1 hunters | a fast, capable one | Wide reading; every candidate is re-verified afterwards |
| Phase 2 fix agents | a fast, capable one | A one-to-three-line diff with the hooks as a safety net |
| Phase 3 reviewers | the strongest available | The last defence against false positives and regressions |
| Phase 3.5 browser checks | a fast, capable one | Following a written flow; you judge the evidence |
| Verifying candidates, settling disagreements, verdicts, phase 4 | you, the orchestrator, on the strongest available | These decide what ships; don't delegate them |

The skill can't choose the session's model. If a stronger model than the session's is available (look with the models tool, your own provider first), say so in your first message and ask whether to continue or switch, before spending anything on hunters. If a model you meant to use isn't available, use the session's model and tell the user which phases ran on it.

## Phase 0: Learn the repo's rules (2 minutes, don't skip)

Run `scripts/conventions.sh <repo> > $SCRATCH/conventions.raw.txt`. It lists the written rules that exist (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, the PR template, `CODEOWNERS`, commit-lint and hook config) and shows what is actually done: the person's own recent commits, pull requests and issues in this repository, other contributors' merged pull requests, the labels the repository has, and the branches PRs are merged into. Read the files it lists. Then write what you derived to `$SCRATCH/conventions.md`, one line per rule with the evidence it rests on (`PR title: the commit subject, "Area: what is true now"; from the person's #15–#22`), in the order given under "Whose conventions apply":

- the base branch for PRs (often not the default branch, e.g. `staging`), and `git fetch` it
- branch naming, the commit subject format, and the PR title format (is it the commit subject? does it carry an issue or ticket number?)
- the PR body: the template's sections, or the headings the person's own PRs use, and how long they run
- whether each PR is paired with an issue, and how the two are linked
- labels (only ones the repository has), assignee, reviewers, draft or not
- the repo's own code rules (logger usage, async rules, i18n rules). A violation of these is a legitimate finding, but say which rule it is.
- whether tests may be added (many repos say "only when asked")

A rule with no evidence stays empty; don't fill it from habit or from this file's examples. Every subagent that writes a commit or a PR text gets the relevant lines of `conventions.md` in its prompt, with two or three real examples copied from the repository. If an earlier round's issue exists, read its project board column (the `project_status.sh` read-back query) and use it as the default status in phase 5 instead of asking.

Then open the round in the findings database, from the main checkout:

1. `kb.py round start --base <base> --areas <a,b,...>`, with the 4–5 hunting areas you will use in phase 1 (add `--memory off` only when the person asks for, or accepts, a memory-off round).
2. `kb.py sync --base <base>` reads from GitHub what happened to every PR an earlier round opened: merged, closed (with the review comments as the reason), still open, or reverted since. If it prints `sync skipped: ...` (no `gh`, signed out, offline), say so in the phase update and go on; nothing was changed.
3. `kb.py seed status --scratch $SCRATCH`. While it says "room for more", seed the database from the repository's own small fix commits: `kb.py seed prepare --base <base> --scratch $SCRATCH` (pass `--fix-regex` matching the fix subjects you found in `conventions.md` when they don't use the words fix/bug/typo/hotfix), then one fast-model subagent per `$SCRATCH/seed/batch-NNN.json`, each told to follow `$SCRATCH/seed/README.txt` and write `answer-NNN.json`, then `kb.py seed load --scratch $SCRATCH`. Seeds count for less than the skill's own results, and never skip a candidate.
4. Put the last line of `kb.py stats` in the phase 0 update.

Check `<repo>-worktrees/` for an earlier round's worktrees. Those already shipped have their branch on `origin`. If each is clean and its HEAD equals `origin/<branch>`, offer to remove them (`git worktree remove` + `git branch -D`); otherwise they clutter the review loop and the blame step.

## Phase 0.5: Build a preview of pending work

On a repo with many open PRs, hunting on the bare base branch wastes tokens on bugs a pending PR already fixes. Count them first (`gh pr list --base <base> --state open --json number --jq length`). If there are more than ~3, run `scripts/make_preview.sh <repo> <base> $SCRATCH/preview` (see the header). It creates a detached worktree with the base plus every open PR updated in the last 30 days merged in, and skips the ones that conflict. Tell the user how many merged and how many were skipped.

The preview is **read-only and for hunting only**. Fixes still branch off the real base in phase 2.

## Phase 1: Hunt (parallel explore agents)

Point the hunters at the preview worktree if there is one, otherwise at the main checkout (`<checkout>` below). The areas are the 4–5 non-overlapping ones given to `round start`, for example server methods/API, UI templates/components, shared utils/modules, i18n plus emails/notifications, jobs/integrations. Before any hunter:

1. `kb.py -C <checkout> rules run <checkout> --scratch $SCRATCH` runs the private rules (see Phase 4) and writes their hits to `$SCRATCH/rule-candidates.json`. They are candidates with `source` `rule:<id>`, nothing more: they go through `check` and your verification with the hunters' ones.
2. `kb.py hotspots --base <base> > $SCRATCH/hotspots.json`: the files that changed most in the last 90 days (formatter sweeps, lockfiles, docs and tests left out), weighted by past bugs.
3. `kb.py hunters --hot-from $SCRATCH/hotspots.json` plans the hunters (5 by default): `explore` lists the areas the exploration hunters take (the ones hunted least recently, so every area comes round), `hot` one list of files per hot-file hunter, `kind` (once there are enough results) one list of bug kinds per kind-focused hunter. In a memory-off round every hunter explores.

Spawn one read-only explore agent per entry of the plan **in the same turn**: an exploration hunter per area, a hot-file hunter per file list (hunting those files and what they call), a kind hunter per kind list (hunting the whole checkout for those kinds only, with each kind's description from below and `kb.py patterns <kind>`). Paste `kb.py brief --role hunter` into every prompt, and for a hot-file hunter `kb.py brief --role hunter --hot-from <a file with its list>`; it is empty in a memory-off round. Ask each for ~6 confirmed findings ranked by confidence, each with `file:line`, the exact snippet, why it's a bug (1–2 sentences) and the proposed 1–3 line fix. Give each candidate the `source` of its hunter: `hunter:explore`, `hunter:hot` or `hunter:kind`, and the `area` for an exploration hunter.

Tell them what counts. Good: missing `await` whose result is used, Promise used as a boolean, missing `return` after an error callback, wrong argument order to a library call (verify against the **installed** library source, not memory), `!== null` that misses `undefined`, wrong i18n key or namespace, typos in user-visible text, `<label for>` pointing at an id that doesn't exist, wrong log level or a dropped log context. Not good: style nits, refactors, anything that needs a product decision.

Two traps to tell them about:
- A missing `await` on a path that writes data or calls an external service (Stripe, email, SMS) can re-enable a flow that has silently been broken for months. Before listing it, check the method's auth check and what side effects the fixed path now has. If it's unauthenticated or does irreversible work, it's a security or product issue, not a candidate for this batch.
- Verify that the **fix** works against the installed library, not just that the bug is real. Example: URL-encoding a query param doesn't help if the router decodes it twice.

Collect the rule hits and every hunter's candidates into one JSON list (`$SCRATCH/candidates.json`: `kind`, `root_cause`, `path`, `snippet` as it is in the file, `source`, plus `pattern`, `symbol` and `area` when known) and run `kb.py -C <checkout> check $SCRATCH/candidates.json`. `kind` is one of `kb.py kinds`, or `new:<slug>` for a bug of none of them. `pattern` is `<kind>/<short root-cause slug>`; `kb.py patterns <kind>` lists the ones already used, so reuse one when it fits: rules are built per pattern. Per candidate it answers:
- `strong`: already shipped in an open PR, or already dropped for a reason that still holds (same file, same snippet, same surrounding code). Skip it; record it as `dropped:duplicate` at phase 1 with the note as the reason, and list these in the phase update as "skipped as known".
- `weak`: something related is known (`reintroduced`, `sibling of`, `seen before`, `related`). Verify it as new, with the earlier record (`kb.py show '#<id>'`) next to it. A `reintroduced` bug came back after a merged fix: worth a line in its PR's background. A `sibling` is usually a real separate copy of a bug.
- `miss`: nothing known.

Then **verify every candidate yourself** by reading the code (one batched `sed -n` command over all locations is fast). Drop anything you can't confirm, and anything in code nothing reaches: for a template, grep that `{{> name}}` or a route renders it; for a method, that something calls it. Catching dead code here is cheaper than in phase 3, after a fix was written. With a preview, also check that each snippet exists on the **base** (`git grep -nF '<snippet>' origin/<base> -- <path>`), and record the base's line numbers, since they differ from the preview's:
- missing on the base means the bug lives in an open PR's code. Don't fix it; list it under "Review notes for open PRs" with the PR number (`git -C $SCRATCH/preview log --merges --format=%s -- <path>` lists the `preview: #N` merges that touched the file). Offer these to the user as review comments; never publish them on their own.
- present on the base but in a file a skipped (conflicting) PR touches: still valid, but check that PR's diff (`gh pr diff <n>`) so you don't duplicate its fix.

Record **every** candidate with `kb.py -C <the checkout they hunted> add <file.json>` (same fields as for `check`, plus `outcome`), dropped ones included, because the drops and their reasons are what the next round learns from:
- the ones you drop: `outcome` `dropped:<why>` (`false-positive`, `dead-code`, `duplicate`, `product-decision`, `in-open-pr`, `unsafe`), `outcome_phase` `1`, and a one-line `reason` anchored in the code ("`roles` is always an array: `getRoles()` defaults it"). Write the reason as a lesson the next hunter can use, never as a transcript.
- the survivors: `outcome` `confirmed`, with this round's `nn` (`01`, `02`, ...), a short `slug`, and `verify` for phase 3.5: `ui` when a user can see the bug on a page, `http` for an endpoint or webhook whose response changes, `none` for logging, cron-only code and pure text typos. Runners-up get `in_batch` `0`: kept, not counted, not fixed.

Show `kb.py list --runners-up` in the phase update; it groups the round by kind.

## Phase 2: Fix (one worktree and one agent per bug)

Create all worktrees up front with `scripts/make_worktrees.sh` (see the header for usage), in a sibling directory named `<repo>-worktrees` (e.g. `~/code/app-worktrees`). Each one gets a branch off the fetched base, and a symlink to the main checkout's `node_modules` so pre-commit hooks work. The branch name is provisional (`fix/<slug>`); phase 5 renames it to the repository's own format.

Spawn one general agent per bug, in batches of ~10 in the same turn. Each prompt must be self-contained:
- the output of `kb.py show NN` for its bug, the worktree path and the branch
- "re-read and confirm first; if it isn't real or the fix is unsafe, don't commit and explain why"
- minimal diff, no refactors, no tests unless the repo or user asks
- commit in the format recorded in `conventions.md` (paste those lines and two real subjects from the repository), **hooks on, no `--no-verify`**. If a linter rejects the suggested form (e.g. `consistent-return`, `arrow-body-style`), adapt the code, don't bypass the hook. If the formatter touches unrelated lines, restore them and amend.
- don't push, don't open a PR
- write a PR description to `$SCRATCH/PR-NN-<slug>.md`: a first line `# <PR title>`, then the body in the repository's PR template or, where it has none, in the sections the person's own PRs use
- for HTML ids, check the whole page for duplicates first; for shared helpers, check every caller; fix sibling copies of the same bug (another template with the same missing helper) in the same commit
- before adding an import, check it isn't already a global (ESLint `globals`, startup files that set `global.x`). An agent that "fixes" a missing import tends to also overstate the impact ("every call in this file would throw").

If the user interrupts mid-batch, agents may have half-finished. Resume them with their session IDs rather than starting fresh, and check each worktree's `git status` before assuming anything.

Afterwards, verify with one loop over the worktrees: commit count vs. base (expect 1), clean status, last commit subject. Normalize PR titles so they match the commit subjects. Record each result yourself (the agents don't): `kb.py update NN --outcome fixed --phase 2 --fix-diff-from <worktree>`, or for an agent that found the bug isn't real or the fix unsafe, `kb.py update NN --outcome dropped:<why> --phase 2 --reason '<its reason, one line>'`.

## Phase 3: Hunt for false positives (three biased reviewers)

Spawn three read-only general agents in the same turn, each reviewing **all** fixes (`git -C <wt> show HEAD`) with a different bias:

1. **Skeptical prosecutor**: assume each bug is fake until the code proves otherwise. Check the installed library source (does the logger already wrap a bare Error? does the i18n lib already fall back to the page language?). Its prompt gets `kb.py brief --role prosecutor`: what earlier rounds found not to be bugs here.
2. **Regression hunter**: what does production do differently now? Look for newly-enabled guards that block legit input, errors that now surface with misleading messages, a filter that "matched nothing" now "matching everything" (dangerous on messaging audiences), duplicate DOM ids, and sibling lines with the same bug that were left unfixed.
3. **Reachability and intent**: is the code reachable (routes, template inclusions, callers, cron jobs), or dead? Was the behavior intended? This agent also checks phase 4's "introduced in" commits if they exist by then.

Verify the points of disagreement yourself; don't just count votes. Then present a verdict table and recommend:
- **drop** false positives, dead code, and fixes whose only "safe" form is the current behavior (if the safe version equals a revert, there's nothing to ship). Remove those worktrees and branches, and record their hashes so they can be recovered from the reflog.
- **extend** incomplete fixes (amend; hooks on)
- **reword** overstated PR descriptions (e.g. "no visible change today; latent bug"), and, where the repository types its commits and branches, retype a fix that is really a cleanup (`fix` to `chore`, say).

The user said to stop only before GitHub, so apply these recommendations and tell them what you dropped and why. If a drop is a judgment call, list it clearly so they can veto it. Record each drop: `kb.py update NN --outcome dropped:<why> --phase 3 --reason '<the lesson, one line, anchored in the code>'`; and after an amend, `kb.py update NN --fix-diff-from <worktree>` again.

## Phase 3.5: Prove the visible fixes in a running app

Code reading can agree with itself and still be wrong about what a user sees. For the fixes tagged `ui` or `http` that survived phase 3 (`kb.py list --verify ui,http`, or the ones the user named), reproduce the bug on the base build and watch it disappear with the fix. Skip `none`. Skip the whole phase, and say so, when the repo can't be run locally against a database that is safe to write to.

**Read `references/browser-verification.md` before starting anything.** The three rules that matter most:

- **Drive it with Playwright on headless Chromium, from a script.** One script under `$SCRATCH/verify-scripts/` that takes `before` or `after`, so both runs are the same clicks at the same viewport, and a capture that failed can simply be run again. Don't use any other browser the session happens to offer (an in-app or desktop browser pane, a Chrome extension, MCP browser tools, computer use), not even for a first look: those drive windows on the user's screen, fail when a pane isn't visible, and leave nothing to re-run. A browser tool or skill that shows up in the session is not an invitation to use it here. If Playwright can't be made to run, stop and tell the user; don't switch tools. Put this rule in the prompt of any subagent that runs checks.
- **Never more than a few app instances.** Start every instance with `scripts/app_slot.sh run`, which enforces a machine-wide cap (default 2, never above 3) and refuses beyond it; never launch a dev server any other way, and never from a subagent. The default plan needs only one: a single verification worktree at the base, check every bug ("before"), cherry-pick all the fixes into it, let it hot-reload, check again ("after"). Stop it with `app_slot.sh stop` as soon as you're done, and on any failure or interruption. Dev servers are heavy enough that five at once freeze the user's machine.
- **Use the browser as a real user would.** Sign in through the login form, navigate by clicking, find things by their visible label or role, and judge by what is on screen. No `page.evaluate` to set state, no injected cookies or tokens, no database edits or mocked requests mid-flow, no forced clicks. If the state can't be reached the way a user reaches it, report "not verifiable as a user" instead of forcing it: a check that hacks its way in proves nothing about the user's path.

Outcomes: bug seen on the base and gone with the fix means verified, and the PR says so with what was seen. A bug that doesn't reproduce on the base is a likely false positive: re-examine it, then drop or reword. A bug still there with the fix means the fix is incomplete. Save before/after screenshots under `$SCRATCH/evidence/` as `NN-<slug>-before.png` and `NN-<slug>-after.png`, each annotated with `scripts/annotate.py` so an arrow and a short label point at the element that changed; small differences are easy to miss otherwise.

**The pictures are the proof, so the word "verified" depends on them.** A reviewer can't tell "I saw it" from "I think I saw it" without the pair, and the scratch directory is deleted at the end. So:

- Write one line per fix that survived phase 3 to `$SCRATCH/evidence/checks.tsv`: `NN slug tag outcome reason`, where the outcome is `verified`, `not-verified <reason>`, or `skipped` for a `none` fix. Fixes that were never run in the app get a line too (`not-verified not run: no safe database`).
- End the phase with `python3 -I scripts/check_evidence.py $SCRATCH` and don't leave it until it passes. Then `kb.py import-checks $SCRATCH/evidence/checks.tsv`, and record any fix dropped here as `dropped:<why>` at phase `3.5`. It fails when a `verified` fix lacks either annotated picture (or the before/after response text for `http`), when its PR text doesn't show them in a Before/After table, and when the PR text of any other fix claims a browser check.
- When a capture fails, fix the cause and take it again. If you can't, the fix is `not-verified` and its PR says "not checked in a browser", whatever you saw on the way. Never keep "verified" on the strength of text you read from the page.
- Look at every picture before it leaves the machine (open the file). It gets uploaded to GitHub, and nothing can grep an image: it must not show the scratch path, a home directory, this skill's name, or a real person's data. Capture only the region that matters (`locator.screenshot()` or a `clip`), which also keeps paths in other parts of the page out of the frame.

## Phase 4: "Introduced in" for each fix

Run `scripts/introduced_in.sh <worktrees-dir> <slug> ...` with this round's slugs (without slugs it scans every worktree, including earlier rounds'). It blames the lines each fix changed on the fix's parent commit. Blame is only a first guess: it often lands on formatter/linter sweeps ("prettier", "eslint --fix"), file moves, or an unrelated later edit of the same line. For each suspicious hit, use pickaxe to find where the buggy text first appeared, and read the diff when it's ambiguous:

```sh
git log <base> --reverse -S'<exact buggy snippet>' --format='%h %ad %an | %s' --date=short -- <path>
```

Watch for: an earlier version that was correct (`!=` later "cleaned up" into `!==`); labels that were right until the inputs were renamed; a bug that was half-fixed in one copy and not in a sibling (cite both commits).

Add it to each PR description where the repository's template has room for background, otherwise as a section of its own before the testing or checks section:
- one commit: `- <hash>: <~6-word reason>`
- several commits: a `| Commit | Why |` table

Record the commit you settled on: `kb.py update NN --introduced-in <hash>`.

Then tend the private rules. They live only in the data folder, run before every hunt, and need no approval:

1. `kb.py rules health` recounts each rule's results and turns off any rule whose hits are mostly not merged (below 50% after 4 decided).
2. `kb.py rules candidates` lists the root-cause patterns with 2+ merged fixes (or 3+ seeds) and no rule yet, with their source snippets. For each, draft `$SCRATCH/rules/<id>.json` (the format is in the `scripts/kblib/rules.py` header): an `ast-grep` rule for code, an `html-regex` rule for templates (with `unless` for "an id that is not in the file"), the root cause as `message`, the source finding ids, one `must_match` and one `must_not_match` example. Keep it as narrow as the sources: a rule that flags half the codebase costs more than it finds.
3. `kb.py rules add $SCRATCH/rules/<id>.json --scratch $SCRATCH` tests it (both examples and every source must behave) and saves it, or says why not. Fix the draft or give up on that pattern; never loosen the test.

When a remembered lesson turns out wrong (a "known not-bug" that is a bug after all), retire it: `kb.py supersede '#<id>'` (`--by '#<new id>'` when a newer finding replaces it).

## Phase 5: Publish (ask first)

Run `python3 -I scripts/check_evidence.py $SCRATCH` once more and fix what it reports before showing anything: PR texts get edited between phase 3.5 and here, and this is the last moment a missing picture costs nothing. Then stop and show the user the list of what will be created (N PRs, plus N issues if this repository pairs them; items reused from existing issues, labels, reviewers, base, board column, draft or not). Say where each convention came from, reading it off `$SCRATCH/conventions.md` ("no reviewers: none of your PRs here ask anyone"), so a wrong inference gets corrected before anything is published. For each fix say how it was checked (verified in a browser, or code review and hooks only); before/after screenshots go into the PR body: name the evidence files in the description's Before/After table, and `ship.py` uploads them when it opens the PR (for a PR that already exists, `gh pr edit --attach`; see `references/browser-verification.md`). If `kb.py kinds --pending` lists new bug kinds from this round, ask for each whether it joins the list (`kb.py kinds approve <slug>`) or belongs to an existing kind (`kb.py kinds map <slug> <existing>`). Give them a command to review every diff with its PR text, run from `<repo>-worktrees/` (`delta` gives GitHub-like syntax highlighting; without it, `git log --color=always ... | less -R`):

```sh
for d in <slug> ...; do echo "━━ $d"; cat $SCRATCH/PR-*-"$d".md; git -C "$d" log origin/<base>..HEAD -p --stat; done | delta --side-by-side
```

Wait for a yes, then:

1. **Find duplicates** (skip steps 1 and 3 when the repository doesn't pair PRs with issues and the user didn't ask for them): fetch open issue titles once (`gh issue list --state open --limit 400 --json number,title`), grep them locally, and read any close matches. If an existing issue already covers a fix, reuse its number. Use the wording for a partial link (`Part of #N`) instead of the closing line if the PR only covers part of it, and add the user as assignee on it.
2. **Write the texts**: keep each issue body and each PR body about as long as the person's own there; with nothing to go by, a **30–60 second read** (about 50–150 words). An issue holds a bug summary, the expected behavior and the location; a PR holds the sections recorded in `conventions.md`. Then write the spec at `$SCRATCH/ship.json` (shape in the `scripts/ship.py` header). It lists, fix by fix, exactly what will be published, and every value in it comes from `conventions.md`: a key with no evidence behind it is left out.
3. **Create the issues** with `scripts/ship.py <spec> create-issues`, assigned and labelled the way the person's own issues there are. Record the numbers in the spec as you go, so a crash doesn't cause duplicates.
4. **Publish**: run `scripts/ship.py <spec> ship --dry-run` and read what it would do. Then run it on one fix, check the result (`gh pr view` with title, head, base, assignees, reviewers and commits), and run the rest. Where the spec says so it renames the branch and rewrites the commit subject (a message-only amend with hooks off, since the code already passed them); then it pushes and opens the PR with the title, labels, reviewers and assignee listed.
   - If the PR base isn't the default branch, GitHub won't link or auto-close from `Closes #N`. Tell the user; don't fight it.
   - Then `kb.py import-ship $SCRATCH/ship.json`, so the next round's `sync` can read what the team does with each PR.
5. **Project board** (only when the fixes have issues and the repository's issues sit on a board): `gh project list --owner <org>`. If the token lacks the `project` scope, ask the user to run `gh auth refresh -h github.com -s project` (it's interactive, so you can't do it for them). Then run `scripts/project_status.sh` with the status the person's earlier issues sit in, or the one they name, and read it back to confirm.
6. **CI**: check every PR (`gh pr checks <n> --json name,state,link`). For failures, read `gh run view <run> --log-failed` before calling anything flaky. If it's infrastructure (e.g. `Failed to restore cache entry` when many PRs build at once), rerun the **whole** run (`gh run rerun <run>`). Rerunning only the failed jobs hits the same cache miss. If a real test fails, fix it on that branch.
7. **Finish** with a markdown table: `| # | Issue link | PR link | Summary |` (`#` is the NN) (no issue column when there are none). Offer to open the PRs in the browser (`google-chrome <urls...>` or `xdg-open`).

## Phase 6: Clean up the scratch files

Once every PR is open and its body is on GitHub, the local copies are stale duplicates. Leaving them around confuses the next run, which might reuse a spec whose issue numbers have already been used. The findings database is not a scratch file: never delete or move `~/.local/share/quick-wins/`. Do this in the same turn as the final table, without waiting to be asked:

0. Before deleting anything, run `python3 -I scripts/check_evidence.py $SCRATCH --published` (add `-R org/name NN=<pr>` for PRs that `ship.py` didn't open). It reads each verified fix's PR body from GitHub and fails if its before and after pictures aren't in it. The scratch directory holds the only copies, so add any that are missing (`gh pr edit <n> --attach`) first.
1. If phase 3.5 ran: `scripts/app_slot.sh status` must show none of this run's instances (stop any that are left), then remove the verification worktree (`git -C <repo> worktree remove --force <repo>-worktrees/verify`) and drop the throwaway database and delete the private settings copy you created for it. Do this even if the run stopped early.
2. List what's there (`ls -la`) and check that each file belongs to this run.
3. Remove the preview worktree first if you made one (`git -C <repo> worktree remove --force $SCRATCH/preview`), so git doesn't keep a dangling entry. It's throwaway and goes stale fast; remove it even if the run stopped early.
4. Delete this run's scratch directory: `rm -rf $SCRATCH` (only this repo's subdirectory; another repo's run may be using its sibling). That covers the `PR-NN-*.md` copies, the ship spec, the seed batches and any temp lists.
5. Confirm with an `ls` that they're gone. In zsh, check with `ls | grep` rather than a bare glob, because an unmatched glob is an error there.
6. Remove the fix worktrees and their local branches too: once a PR is open, its branch lives on GitHub, so the local copy is just clutter. For each one, first check that it's clean (`git status --porcelain` is empty) and that `HEAD` equals `origin/<branch>`; skip and report any that aren't. Then `git worktree remove <dir>` plus `git branch -D <branch>`, and `rmdir <repo>-worktrees` once it's empty. Never touch the remote branches or the PRs. Tell the user how to bring one back if a reviewer asks for changes: `git -C <repo> worktree add <repo>-worktrees/<slug> <branch>` (after `git fetch`).

## Reporting style

Report faithfully: say what was verified in a browser as a user, what was only hooked or linted, and what nobody tested in a running app. Don't call a fix "real" because one agent said so. Keep the user's private details out of anything sent to external services other than the GitHub actions they asked for.
