# Design: a findings database for quick-wins

Status: draft for approval. Date: 2026-11-19.

## Intent

Every run of the skill starts from nothing. It re-finds bugs an earlier round already shipped, re-verifies
false positives an earlier round already dropped (with the same reasons), and never learns which kinds of
bugs a team actually accepts. Its only memory is `$SCRATCH/BUGS.md`, which phase 6 deletes.

This design gives each repository a local database of every finding and what happened to it, and uses it to:

1. **work faster**: skip known findings and known false positives before spending verification on them;
2. **steer**: spend more of the hunt on the bug kinds and files that pay off, without losing exploration;
3. **compound**: turn proven fixes into cheap rules that run before any hunter, and, with approval, into
   repository lint rules, docs and improvements to this skill.

## Goals, non-goals, anti-goals

Goals
- One database per repository, local, kept between runs, read and written only through `scripts/kb.py`.
- Outcomes come from outside the skill: whether the team merged, closed or reverted each PR.
- Cheap to experiment with: per-round stats, a memory-off switch, every number a config value.

Success (the measure the stats report is built around)
- Main: share of candidates that reach the orchestrator's verification and end up merged, rising over rounds.
- Guard: merged fixes per round must not fall over 3–4 rounds. If it does, the steering is failing,
  whatever the main measure says.
- Informational: time per round, and candidates by source (rule, hot-file hunter, exploration hunter).

Non-goals (v1)
- Embeddings, Chroma or any vector store. Full-text search only; revisit if lexical lookup misses.
- Sharing anything between repositories or machines.
- Publishing anything without the existing phase 5 confirmation.

Anti-goals (failure even if it technically works)
- Tunnel vision: the hunt narrows to a few bug kinds and dries up.
- Memory treated as truth: a remembered lesson overrides what the current code says.
- Bloated prompts: memory context that buries the hunter's instructions.
- Memory leaking into published work: database ids, the skill's name or stats in a commit, PR or issue.

## Constraints

- Python standard library only (SQLite ships with FTS5 here). The skill stays clone-and-go.
- Subagents may lack MCP tools; anything they need from memory works through a shell command or is pasted
  into their prompt by the orchestrator.
- Never store authors of commits or bugs, customer data, or anything from the running app. Code snippets,
  diffs, commit hashes and PR numbers are fine. Nothing is sent to an external service.
- The naming rule in SKILL.md still holds: nothing from this feature appears in anything the work publishes.
- The data folder is a third write location. README "Needs" and SKILL.md line 12 must list it, so people
  can pre-approve it in their agent's permissions.
- Small scale: tens of findings per round, hundreds per repo per year. No need for indexes beyond the obvious.

## Approaches

| | Approach | Trade-off |
|---|---|---|
| **A (picked)** | `scripts/kb.py`, a stdlib Python CLI over SQLite, called from SKILL.md like `ship.py` | Matches how the skill works; works for every subagent with a shell; SKILL.md must spell out commands |
| B | A local MCP server exposing the same operations as tools | Typed tools, but must be registered in each agent, needs a running process, and subagents may not inherit MCP |
| C | JSON-lines files searched with grep | Readable, but it is the text file the user wants to leave, and stats/sync/thresholds become hand-written scans |

Ruled out: storing findings in codebase-memory-mcp (it indexes code and is rebuilt as code changes).
It stays useful alongside A: a finding can carry the graph's qualified symbol name for `trace_path`.

## Decisions

### Storage and identity
- Folder: `~/.local/share/quick-wins/<repo-key>/`, holding `kb.db`, `config.json` and `rules/`.
- `<repo-key>` comes from the `origin` remote URL (host/owner/name, e.g. `github.com_org_name`), so every
  worktree of one repository shares one database. A repo without `origin` uses its top-level folder name.
- `kb.py` creates and migrates the schema on any command (a `schema_version` table; copy `kb.db` to
  `kb.db.bak` before migrating). SQLite in WAL mode with a busy timeout, so two sessions in one repo don't fail.
- Phase 6 never touches this folder.

### Records
`findings`: `id`, `round_id`, `kind`, `pattern` (a short slug for the root cause within the kind, e.g.
`missing-await/collection-result-used`), `root_cause` (one sentence: why it is wrong), `path`, `symbol`,
`snippet`, `snippet_hash` (whitespace-normalised), `context_hash`, `base_sha`, `fix_diff`, `source`
(`hunter:hot`, `hunter:explore`, `hunter:kind`, `rule:<id>`, `seed`), `outcome`, `outcome_phase`,
`reason`, `browser_check` (`verified` / `not-verified` / `skipped`, from `checks.tsv`), `introduced_in`,
`pr_number`, `pr_url`, `changed_in_review`, `superseded_by`, `created`, `updated`.
Full-text index over `root_cause`, `reason`, `snippet`, `path`.

`rounds`: `id`, `started`, `base`, `base_sha`, `memory` (`on`/`off`), `settings` (config snapshot).
`kinds`: the approved kind list. `rules`: see below.

### Outcomes
`seed`, `candidate`, `confirmed` (passed the orchestrator's verification), `fixed` (fix committed),
`shipped` (PR open), `merged`, `closed`, `reverted`, `dropped:<why>` with `outcome_phase` recording where
(1 verification, 2 fix agent, 3 reviewers, 3.5 browser). `<why>` is one of `false-positive`, `dead-code`,
`duplicate`, `product-decision`, `in-open-pr`, `unsafe`.
*My call:* "proposed / verified" from the conversation became `candidate` / `confirmed`, because phase 3.5
already uses "verified" for browser checks and two meanings would confuse agents.

### Bug kinds
A fixed starting list from SKILL.md's examples and past rounds: `missing-await`, `promise-as-boolean`,
`missing-return-after-error`, `wrong-arg-order`, `null-check-misses-undefined`, `wrong-i18n-key`,
`missing-i18n-key`, `visible-typo`, `label-for-mismatch`, `wrong-link-or-route`, `wrong-template-param`,
`wrong-query-options`, `unhandled-throw-hangs-request`, `wrong-log-level`, `dropped-log-context`.
An agent may record `kind=new:<slug>`; new kinds are listed at the phase 5 stop and join the list only when
the person approves (otherwise they are mapped onto an existing kind). `pattern` slugs are free, but
`kb.py patterns <kind>` lists existing ones so agents reuse them.

### Matching (skip known findings)
`kb.py check <candidates.json>` (batched, run by the orchestrator in phase 1) answers per candidate:
- **strong**: same `path`, `snippet_hash` and `context_hash` (whitespace-normalised hash of the 20 lines
  around the snippet) as a record that is `shipped` (PR still open) or
  `dropped:false-positive|dead-code|product-decision`, and not superseded → drop it, and list it in the
  phase update as "skipped as known (#id, reason)". *My call:* the context hash means a false positive is
  re-checked as soon as the code around it changes (e.g. the guard that made it safe was removed).
- a match against a `merged` or `reverted` record is never a skip: the buggy code came back, so it is a
  real candidate, flagged "reintroduced (#id)".
- **weak**: same `snippet_hash` elsewhere, or a full-text hit on root cause in the same file → keep it, show
  the earlier record next to it during verification. Same snippet in another file is usually a sibling copy,
  which is a real separate bug.
- **miss**: nothing → normal verification.
Memory is evidence, never a verdict: only `strong` skips; everything else is re-checked against current code.

### Outcomes from GitHub (`kb.py sync`, phase 0)
One batched `gh api graphql` query for every `shipped` record's PR:
merged → `merged` (+ merge commit); closed unmerged → `closed`, reason = the last review comments (trimmed);
open → unchanged (pending; stats count only decided PRs); commits pushed by anyone after the skill's
commit → `changed_in_review = 1`. Reverts: search `origin/<base>` for `This reverts commit <merge sha>` or
`Revert "<PR title>"` → `reverted`. "Merged" means merged into the PR's base, not released.

### Feeding memory into prompts (`kb.py brief`)
Prints at most ~40 lines for hunter and prosecutor prompts: current false-positive lessons for this repo
(reason + path, newest first, superseded excluded), bug kinds ranked by merge rate (stage 4), and the hot
files list for hot-file hunters. Lessons are written as one line each, anchored to a file, never as
transcripts. `kb.py supersede <id> [--by <id>]` retires a lesson that stopped being true.

### History seed (`kb.py seed`, phase 0, first run and when `seed_limit` grows)
- Candidates: commits on `origin/<base>` whose subject marks a fix by the repository's own convention
  (from `conventions.md`; fallback: `fix|bug|typo|hotfix`), plus merged PR titles for squash-merging repos.
- Keep diffs of at most `seed_max_lines` (10) changed lines, excluding lockfiles, generated and vendored
  files, translation-file bulk edits. Newest first, up to `seed_limit` (300).
- `kb.py seed prepare` writes batches to `$SCRATCH/seed/`; a fast-model subagent per batch writes back
  `{commit, kind, pattern, root_cause}` or `skip`; `kb.py seed load` imports them as `outcome=seed`.
- Reverted fix commits become seeds with `outcome=reverted`.
- Seeds count with weight `seed_weight` (0.3) in every statistic.

### Hunting where code changes most (`kb.py hotspots`, phase 1)
- `git log origin/<base> --since=<hot_window_days, 90> --numstat`, skipping commits that touch more than
  `sweep_files` (50) files or whose subject reads like a formatter/lint sweep, and the excluded paths above.
- Score per file = lines changed (normalised to 0–1) × (1 + bug density from the database).
- Hunter split: the total stays `hunters` (5, today's count). `explore_share` (25%, rounded up: 2 of 5)
  are exploration hunters with no memory steering; they take the areas of today's split that were hunted
  least recently (tracked per round), so every area is covered every few rounds. The rest are hot-file
  hunters (top files) and, from stage 4, kind-focused hunters. *My call:* rotating keeps the cost per round
  flat; raise `hunters` to buy back depth.
- Code only in open PRs stays out; the preview's review notes cover it as today.

### Rules (stage 3)
- A rule is `rules/<id>.yml` (ast-grep, run with `npx --yes @ast-grep/cli`, installed in `$SCRATCH` per run)
  or `rules/<id>.html.json` (a regex template check run by `kb.py` itself, for Blaze and other templates
  ast-grep can't parse). Each carries `kind`, `pattern`, source finding ids, one must-match and one
  must-not-match example.
- Created when a `pattern` has 2+ merged findings, or 3+ seeds. The orchestrator drafts it after phase 4;
  `kb.py rules test` must pass (examples, and it matches its source snippets) before it is saved. Private
  rules need no approval: they live only in the data folder.
- `kb.py rules run <checkout>` runs first in phase 1; hits become candidates with `source=rule:<id>` and go
  through verification, reachability and the reviewers like any other.
- Each rule tracks hits and outcomes; it turns itself off when precision is below `rule_min_precision`
  (50%) after `rule_min_decided` (4) decided hits.

### Steering by merge rate (stage 4)
Per kind: `(merged + 1) / (decided + 2)`, seeds weighted by `seed_weight`, reverts counting as two
failures, `changed_in_review` as half a success. *My call:* "decided" is every finding that reached the
orchestrator's verification and has a final outcome: merged, closed, reverted, or dropped at any phase
(except `duplicate` and `in-open-pr`, which say nothing about the kind). This matches the main success
measure, so a kind that is mostly noise ranks low even if its few PRs merge. Kinds with fewer than 3 decided outcomes count as unknown
and go to the exploration hunters. Kind-focused hunters get the top kinds; the exploration share never
shrinks.

### Promotions (stage 5)
`kb.py promotions` lists, at the phase 5 stop, in its own section:
| Step | Threshold (config) | Draft |
|---|---|---|
| Repo rule | rule with 5+ hits, 80%+ merged, no false positive in the last 3 rounds | PR adding it to the repo's own checks: an ESLint built-in rule where one fits, the repo's template linter (here `scripts/lint-blaze-html.js`), otherwise an ast-grep rule + CI step |
| Repo docs | a non-mechanical lesson behind 3+ dropped findings | PR editing the repo's conventions doc or `AGENTS.md` |
| Skill update | a process lesson, e.g. an area or source at 80%+ noise over 2 rounds | diff to this skill's SKILL.md |

Nothing is published without approval. Approved repo promotions go through the normal pipeline (own
worktree, the repository's conventions, one PR each). A rule added to CI must pass on today's code: the PR
fixes every remaining match, or adds the rule as a warning. Rule fixtures count as tests where a repository
restricts tests (this one: "only when asked"), so they need explicit approval. Skill updates become a local
commit in the skill's repository.

### Stats and the memory-off switch
- `kb.py stats [--rounds N]`: per round, candidates and survivors per phase, merged/closed/reverted/pending,
  then the same by kind and by source. Shown in the phase 0 update line.
- `kb.py round start --memory off`: the round still records everything, but `check` returns miss for all,
  `brief` prints nothing, rules don't run and the hunter split ignores steering. Stats separate on/off rounds.
- `config.json` holds every number above; `kb.py` has the defaults.

### Changes per phase
- **0:** `round start`, `sync`, `seed` (when due), `stats`.
- **1:** `rules run` → `hotspots` → hunters (with `brief`) → `check` → orchestrator verification → `add`
  every candidate with its outcome. `BUGS.md` becomes a view: `kb.py bugs-md` renders this round's
  confirmed findings in today's format, so later phases and the person read it unchanged.
- **2:** fix agents' results: `fixed` + `fix_diff`, or `dropped:<why>` with phase 2.
- **3:** drops with reasons (phase 3); the prosecutor's prompt gets `brief`.
- **3.5:** `kb.py import-checks $SCRATCH/evidence/checks.tsv`.
- **4:** `introduced_in` per finding.
- **5:** new kinds and promotions in the stop; after publishing, `kb.py import-ship $SCRATCH/ship.json`
  records PR numbers (`ship.py` itself is unchanged).
- **6:** unchanged, except it never touches the data folder.

## Rollout

Each stage ships on its own and is checked before the next, by automated tests (approved by the person):
`tests/` in the skill's repository, stdlib `unittest` only, each test building a throwaway git repo in a
temp dir, a fake `gh` first on `PATH` that returns canned PR states, and a seeded `kb.db` in a temp data
folder (the data folder location must be overridable for this). With that, all five stages can be built in
one sitting; real rounds on a repository afterwards confirm the behavior and tune the config numbers.
The "Done when" lines below are what the tests prove.

1. **Database, outcomes, seed.** `init`, `round`, `add`, `check`, `sync`, `import-checks`, `import-ship`,
   `bugs-md`, `seed`, `stats`, and the SKILL.md phase hooks.
   Done when: a round's findings land in the database; the next round's `sync` marks a merged and a closed
   PR correctly; a strong match from an earlier round is skipped before verification; `seed` loads a repo's
   history within its limit.
2. **Lessons and hot files.** `brief`, `supersede`, `hotspots`, the hunter split.
   Done when: a false positive dropped in round N appears in round N+1's brief and isn't re-proposed;
   formatter sweeps don't top the hot list.
3. **Private rules.** Rule format, `rules test`, `rules run`, auto-off.
   Done when: a rule built from a merged pattern finds its source snippet on the old commit, runs before
   hunters, and its hits flow through verification.
4. **Steering.** Merge-rate weights and kind-focused hunters.
   Done when: the split follows the stats while the exploration share stays fixed; memory-off rounds still work.
5. **Promotions.** `promotions`, drafts, and the approval flow at phase 5.
   Done when: a rule over threshold is proposed with a draft that passes on today's code, and nothing is
   published without a yes.

For the plan (the person's usual working rules): no co-author trailers, reuse existing code and
ask when unsure, only essential comments, one commit per step.

## Left to the builder

- The exact `kb.py` subcommand flags and JSON shapes, and how output is formatted for prompts.
- The graphql query for `sync`, and trimming of review comments.
- Normalising snippets for hashing; the formatter-sweep subject patterns; path exclusion lists.
- Whether to offer a one-off import of a leftover `$SCRATCH/BUGS.md` from before this feature.
