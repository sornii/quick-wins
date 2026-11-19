# Findings Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give quick-wins a per-repository SQLite database of every finding and its outcome, and use it to skip known findings, steer the hunt, run private rules first, and propose promotions.

**Architecture:** `scripts/kb.py` is a thin argparse dispatcher; the logic lives in a small package `scripts/kblib/`, one module per responsibility. SKILL.md calls `kb.py` at each phase, as it calls `ship.py` today. Tests in `tests/` use stdlib `unittest`, a throwaway git repo per test, a fake `gh` on `PATH`, and a temp data folder.

**Tech Stack:** Python 3.11+ standard library (`sqlite3` with FTS5, `argparse`, `subprocess`, `json`, `hashlib`, `unittest`); `git`; `gh`; `npx --yes @ast-grep/cli` for ast-grep rules only.

**Spec:** `docs/specs/2026-11-19-knowledge-base-design.md`. Read it before any task; this plan cites it as "the spec".

## Global Constraints

- Standard library only. No `pip install`. Runs as `python3 -I scripts/kb.py ...`; because `-I` drops the script's directory from `sys.path`, `kb.py` inserts `os.path.dirname(os.path.abspath(__file__))` itself before importing `kblib`.
- Data folder: `~/.local/share/quick-wins/<repo-key>/` holding `kb.db`, `config.json`, `rules/`. Overridable by the environment variable `KB_DATA_DIR` (the parent of `<repo-key>/`), used by every test.
- `<repo-key>` from `origin`: host/owner/name joined by `_`, lowercase, `.git` stripped (`github.com_org_name`); no `origin` → top-level folder name.
- Never store commit or bug authors, customer data, or anything from a running app. Nothing goes to an external service other than `gh` (GitHub, already used) and `npx` (ast-grep package).
- Never write "quick win(s)", "quick-wins" or `qw` into anything a run publishes; `kb.py` output that goes into PR text must not contain database ids or stats.
- Config defaults (in `kblib/store.py`, overridden key by key by `config.json`): `hunters 5`, `explore_share 0.25`, `hot_window_days 90`, `sweep_files 50`, `seed_limit 300`, `seed_max_lines 10`, `seed_weight 0.3`, `context_lines 20`, `brief_max_lines 40`, `unknown_kind_min_decided 3`, `rule_min_precision 0.5`, `rule_min_decided 4`, `rule_from_merged 2`, `rule_from_seeds 3`, `promote_rule_hits 5`, `promote_rule_merged 0.8`, `promote_rule_clean_rounds 3`, `promote_doc_drops 3`, `promote_skill_noise 0.8`, `promote_skill_rounds 2`.
- Commits in the skill's repository: one per task, subject `Findings database: <what is true now>`, no co-author trailers. Only essential comments. Reuse existing scripts; ask when unsure.
- Every command exits 0 on success, 1 on a usage or data error with one line on stderr, and never prints a Python traceback for expected failures (missing `gh`, no network, unknown NN).

## Review Focus

1. **The same repository reached two ways** (SSH and HTTPS `origin`, main checkout and a worktree) must map to one `<repo-key>`. Test in Task 1.
2. **Whitespace-only differences** (CRLF, tabs vs spaces, trailing spaces, re-indentation) must not change `snippet_hash` or `context_hash`. Test in Task 2.
3. **A snippet that is no longer in its file** (file moved, deleted or edited) at `check` time must not crash and must never give `strong`. Test in Task 3.
4. **`gh` missing, signed out or offline during `sync`** must leave every record unchanged, print one line, exit 1, and let phase 0 continue. Test in Task 5.
5. **Two sessions writing at once and an interrupted migration**: a second connection writing during the first's transaction waits instead of failing; `kb.db.bak` exists before any migration step. Test in Task 1.

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/kb.py` | argparse dispatcher; one subcommand per spec command; prints results |
| `scripts/kblib/__init__.py` | empty |
| `scripts/kblib/store.py` | repo key, data folder, config, connection, schema and migrations, kinds, outcome names |
| `scripts/kblib/findings.py` | hashing, rounds, add/update/list/show, kinds approval |
| `scripts/kblib/check.py` | strong/weak/miss matching |
| `scripts/kblib/ingest.py` | `checks.tsv` and `ship.json` import |
| `scripts/kblib/sync.py` | PR outcomes from GitHub, revert detection |
| `scripts/kblib/seed.py` | history seed prepare/load |
| `scripts/kblib/stats.py` | per-round stats, per-kind merge rates |
| `scripts/kblib/brief.py` | prompt context, supersede |
| `scripts/kblib/hotspots.py` | change scores, hunter plan |
| `scripts/kblib/rules.py` | rule files, test, run, health |
| `scripts/kblib/promotions.py` | promotion candidates |
| `tests/helpers.py` | temp git repo builder, fake `gh`, temp `KB_DATA_DIR` |
| `tests/test_<module>.py` | one per module |
| `SKILL.md`, `README.md`, `references/browser-verification.md`, `scripts/check_evidence.py` (comment only) | phase hooks, BUGS.md removal |

Run all tests: `python3 -I -m unittest discover -s tests -t . -v` from the skill root (each test file inserts `scripts/` on `sys.path`).

---

### Task 1: Store, schema, config, test harness

**Files:**
- Create: `scripts/kb.py`, `scripts/kblib/__init__.py`, `scripts/kblib/store.py`, `tests/__init__.py`, `tests/helpers.py`, `tests/test_store.py`
- Modify: `.gitignore` (add nothing new unless test temp dirs leak; they use `tempfile`)

**Interfaces:**
- Produces:
  - `store.repo_key(repo_dir: str) -> str`
  - `store.data_dir(repo_dir: str) -> pathlib.Path` (creates it, and `rules/`)
  - `store.load_config(repo_dir: str) -> dict` (defaults from Global Constraints, `config.json` overrides)
  - `store.connect(repo_dir: str) -> sqlite3.Connection` (WAL, `busy_timeout=5000`, `row_factory=sqlite3.Row`, migrates)
  - `store.SCHEMA_VERSION: int = 1`, `store.KINDS: tuple[str, ...]` (the spec's 15 kinds, in its order), `store.OUTCOMES: frozenset`, `store.DROP_REASONS: frozenset` (`false-positive dead-code duplicate product-decision in-open-pr unsafe`)
  - Tables: `schema_version(version)`, `rounds(id, started, base, base_sha, memory, settings, areas_explored)`, `kinds(slug, status)` with status `approved|pending`, `findings(...)` with the spec's columns plus `area TEXT`, `seed_commit TEXT`, `round_id NULL` for seeds; `findings_fts` (FTS5 over `root_cause, reason, snippet, path`, kept in sync by triggers); `rules(id, engine, kind, pattern, file, active, hits, decided, merged, false_positives, created)`.
  - `tests/helpers.py`: `make_repo(origin: str | None) -> str` (git init, one commit, `origin` set), `commit(repo, path, text, subject) -> str` (returns sha), `fake_gh(responses: dict) -> contextmanager` (puts a `gh` script first on `PATH` that prints `responses[<joined argv>]` or the value under key `"graphql"` for `gh api graphql`, exit 0; `responses=None` → exits 4 "not logged in"), `temp_data_dir() -> contextmanager` (sets `KB_DATA_DIR`).
  - CLI: `kb.py init` prints the data folder path.

- [ ] **Step 1: Write the failing tests** in `tests/test_store.py`:
  - `test_repo_key_same_for_ssh_and_https`: `git@github.com:Org/Name.git` and `https://github.com/org/name` → both `github.com_org_name`.
  - `test_repo_key_same_for_worktree`: `git worktree add` of a `make_repo` → same key as the main checkout.
  - `test_repo_key_without_origin`: → top-level folder name.
  - `test_config_defaults_and_override`: `load_config()["seed_limit"] == 300`; after writing `{"seed_limit": 50}` to `config.json` → 50, other keys still default.
  - `test_connect_creates_schema_and_kinds`: `schema_version` is 1; `kinds` holds the 15 slugs as `approved`; `findings_fts` exists.
  - `test_migration_writes_backup_first`: with an existing `kb.db` at version 0 (a bare `schema_version` table holding 0), `connect()` leaves `kb.db.bak` and ends at version 1.
  - `test_concurrent_writer_waits`: connection A opens `BEGIN IMMEDIATE` and inserts a round; a thread with connection B inserts a round and blocks; A commits after 0.5 s; B completes without `OperationalError`; two rounds exist.
- [ ] **Step 2: Run** `python3 -I -m unittest tests.test_store -v` → FAIL (`ModuleNotFoundError: kblib`).
- [ ] **Step 3: Implement** `store.py` and the `init` subcommand. Migrations are a list of SQL scripts indexed by target version; before applying any, copy `kb.db` to `kb.db.bak` with `sqlite3.Connection.backup`.
- [ ] **Step 4: Run** the same command → PASS.
- [ ] **Step 5: Commit** `Findings database: store, schema and test harness`.

### Task 2: Rounds and findings

**Files:**
- Create: `scripts/kblib/findings.py`, `tests/test_findings.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 1 `connect`, `load_config`, `KINDS`, `OUTCOMES`, `DROP_REASONS`.
- Produces:
  - `findings.normalise(text: str) -> str` (CRLF→LF, each line stripped, blank lines removed)
  - `findings.snippet_hash(snippet: str) -> str` (sha1 hex of `normalise`)
  - `findings.context_hash(repo_dir: str, path: str, snippet: str, lines: int) -> str | None` (finds the normalised snippet in the file's normalised lines; hashes `lines` lines before and after; `None` when file or snippet is missing)
  - `findings.start_round(conn, base, base_sha, memory: str, settings: dict, areas: list[str]) -> int`; `findings.current_round(conn) -> sqlite3.Row | None` (latest)
  - `findings.add_findings(conn, repo_dir, round_id, items: list[dict]) -> list[int]`. Item keys: required `kind, root_cause, path, snippet, source, outcome`; optional `pattern, symbol, area, verify, slug, nn, in_batch (default 1), reason, outcome_phase, base_sha`. Computes both hashes. `kind` not in `kinds` and of the form `new:<slug>` inserts `<slug>` as `pending`; any other unknown kind → `ValueError`. `outcome` must be in `OUTCOMES` or `dropped:<r>` with `r` in `DROP_REASONS`.
  - `findings.resolve(conn, ref: str) -> int` (`"#12"` or `"12"` is an id; `"07"`-style two-digit is NN in the current round; unknown → `LookupError`)
  - `findings.update_finding(conn, ref: str, **fields) -> None` (same validation; `fix_diff_from=<worktree>` stores `git -C <wt> show HEAD --format= --patch`; sets `updated`)
  - `findings.list_findings(conn, round_id, runners_up: bool, verify: set[str] | None) -> list[sqlite3.Row]`, `findings.format_list(rows) -> str` (grouped by kind, one line each: `NN slug: path — root_cause. verify: <tag>`)
  - `findings.show(conn, ref) -> str` (everything a fix agent needs: NN, slug, kind, path, symbol, snippet, root cause, verify)
  - `findings.kinds_pending(conn) -> list[str]`, `findings.approve_kind(conn, slug)`, `findings.map_kind(conn, new_slug, existing)` (rewrites findings, deletes the pending kind)
  - CLI: `round start --base B --memory on|off --areas a,b,c`, `add <items.json>`, `update <ref> [--outcome O] [--phase P] [--reason R] [--fix-diff-from WT] [--introduced-in SHA] [--nn NN --slug S --verify V --in-batch 0|1]`, `list [--runners-up] [--verify ui,http]`, `show <ref>`, `patterns <kind>`, `kinds [--pending] [approve S] [map NEW EXISTING]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_findings.py`:
  - `test_hash_ignores_whitespace`: `"a = 1;\r\n\tb()"` and `"  a = 1;  \n\n    b()"` → equal `snippet_hash`; a re-indented file gives the same `context_hash`.
  - `test_context_hash_none_when_missing`: deleted file → `None`; snippet not in file → `None`.
  - `test_add_validates_kind_and_outcome`: unknown kind `foo` → `ValueError`; `new:double-submit` → added and `kinds_pending == ["double-submit"]`; outcome `dropped:bored` → `ValueError`.
  - `test_resolve_nn_and_id`: two findings with `nn="01"`, `"02"` → `resolve("02")` is the second id; `resolve("#1")` is 1; `resolve("09")` → `LookupError`.
  - `test_update_fix_diff_from_worktree`: after a commit in a worktree, `fix_diff` contains the changed line.
  - `test_list_groups_and_filters`: runners-up (`in_batch=0`) only with `runners_up=True`; `verify={"ui"}` drops a `none` finding; output has a heading per kind.
  - `test_map_kind`: `map_kind("double-submit", "visible-typo")` rewrites the finding's kind and removes the pending slug.
- [ ] **Step 2: Run** `python3 -I -m unittest tests.test_findings -v` → FAIL.
- [ ] **Step 3: Implement** `findings.py` and the CLI subcommands.
- [ ] **Step 4: Run** → PASS, and `tests.test_store` still PASS.
- [ ] **Step 5: Commit** `Findings database: rounds and findings`.

### Task 3: Matching known findings

**Files:**
- Create: `scripts/kblib/check.py`, `tests/test_check.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 2 hashes, `current_round`.
- Produces: `check.check_candidates(conn, repo_dir, items: list[dict], memory_on: bool) -> list[dict]`, one result per item in order: `{"gate": "strong"|"weak"|"miss", "match": int|None, "note": str}`. CLI `check <candidates.json>` prints one JSON object per line and a summary line `strong N, weak N, reintroduced N, miss N`; memory is read from the current round.

Rules, from the spec: strong = same `path`, `snippet_hash`, non-null equal `context_hash`, record outcome `shipped` or `dropped:false-positive|dead-code|product-decision`, `superseded_by` null. A match on `merged`/`reverted` → `weak` with note `reintroduced (#id)`. Weak also = same `snippet_hash` in another path (note `sibling of #id`), or an FTS hit on the candidate's `root_cause` within the same path (note `related #id`). Seeds never produce strong.

- [ ] **Step 1: Write the failing tests** in `tests/test_check.py`:
  - `test_strong_for_dropped_false_positive_unchanged_code`
  - `test_weak_when_context_changed`: edit a line 5 lines above the snippet → `weak` (not `strong`).
  - `test_weak_reintroduced_after_merge`: record `merged` → `gate == "weak"`, `"reintroduced" in note`.
  - `test_weak_sibling_other_path`
  - `test_miss_when_memory_off`: same data as the strong test, round started with `memory="off"` → `miss`.
  - `test_no_crash_when_file_gone`: candidate path deleted from the checkout → `miss` or `weak`, never `strong`, no exception.
- [ ] **Step 2: Run** `python3 -I -m unittest tests.test_check -v` → FAIL.
- [ ] **Step 3: Implement** `check.py` and the subcommand.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: skip known findings`.

### Task 4: Importing browser checks and shipped PRs

**Files:**
- Create: `scripts/kblib/ingest.py`, `tests/test_ingest.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 2 `resolve`, `update_finding`.
- Produces: `ingest.import_checks(conn, tsv_path) -> int` (the `checks.tsv` format in `scripts/check_evidence.py`'s header; `verified` / `not-verified ...` / `skipped` → `browser_check`); `ingest.import_ship(conn, ship_path) -> int` (each fix in `ship.json` whose entry has a PR url or number → `pr_number`, `pr_url`, outcome `shipped`; fixes without one are untouched). CLI `import-checks <tsv>`, `import-ship <json>`, each printing the count.

- [ ] **Step 1: Write the failing tests**: `test_import_checks_sets_browser_check` (three rows → three values; comment and blank lines ignored); `test_import_ship_marks_shipped` (`ship.py` writes the opened PR's url to `fixes[].pr` and keys fixes by `fixes[].n`, the NN of the current round: one fix with `"n": "01", "pr": "https://github.com/o/n/pull/42"`, one without `pr` → first is `shipped`, `pr_number == 42`; second unchanged).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: import browser checks and shipped PRs`.

### Task 5: PR outcomes from GitHub

**Files:**
- Create: `scripts/kblib/sync.py`, `tests/test_sync.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 1 `connect`; Task 4's `shipped` records.
- Produces: `sync.sync(conn, repo_dir, base: str) -> dict` (`{"merged": n, "closed": n, "reverted": n, "pending": n}`); raises `sync.SyncError` on any `gh` failure before writing anything. CLI `sync --base B` prints the dict as one line; on `SyncError` prints `sync skipped: <reason>` and exits 1.

One `gh api graphql` call with an aliased `pullRequest(number:)` per shipped record, asking `state`, `title`, `mergeCommit { oid }`, `commits { totalCount }`, last 3 `reviews` and `comments` bodies. Merged → `merged`; `commits.totalCount > 1` → `changed_in_review = 1`. Closed → `closed`, `reason` = the last review or comment bodies joined, trimmed to 500 chars. Open → unchanged. Then, for every `merged` record, `git -C repo log origin/<base> --format=%H%x00%s%x00%b` searched for `This reverts commit <merge oid>` or subject `Revert "<title>"` → `reverted`. Owner/name from `gh repo view --json nameWithOwner`.

- [ ] **Step 1: Write the failing tests** (all with `fake_gh`):
  - `test_merged_closed_open`: three shipped records → one each; counts `{"merged":1,"closed":1,"reverted":0,"pending":1}`; closed reason holds the review text.
  - `test_changed_in_review`: merged with `totalCount: 3` → flag 1.
  - `test_revert_detected`: commit `Revert "<title>"` with body `This reverts commit <oid>.` on `origin/main` (a local bare remote in the test) → `reverted`.
  - `test_gh_failure_changes_nothing`: `fake_gh(None)` → `SyncError`; all records still `shipped`; CLI exits 1 with one stderr line and no traceback.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: read PR outcomes from GitHub`.

### Task 6: History seed

**Files:**
- Create: `scripts/kblib/seed.py`, `tests/test_seed.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 2 `add_findings`, `load_config`.
- Produces: `seed.prepare(conn, repo_dir, base, scratch: str, fix_regex: str | None) -> int` (number of batch files); `seed.load(conn, repo_dir, scratch) -> int`. CLI `seed prepare --base B --scratch DIR [--fix-regex R]`, `seed load --scratch DIR`, `seed status` (seeded count vs `seed_limit`).

Prepare: `git log origin/<base> --no-merges` newest first; subject matches `fix_regex` (default `(?i)\b(fix|bug|typo|hotfix)\b`); `--numstat` total changed lines ≤ `seed_max_lines`; skip commits touching only excluded paths (`*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `*.min.*`, `dist/`, `build/`, `vendor/`, `node_modules/`, `*.i18n.json` and `i18n/` when more than one locale file changes); skip commits already in `findings.seed_commit`; stop at `seed_limit` minus already seeded. Write `seed/batch-NNN.json` (25 per batch: `{"commit","subject","diff"}`), and `seed/README.txt` telling the subagent to write `seed/answer-NNN.json` as a list of `{"commit","kind","pattern","root_cause","path","snippet"}` or `{"commit","skip":true}`, using `store.KINDS`. Load: add each non-skip answer as `outcome=seed`, `source=seed`, `seed_commit=commit`, `round_id NULL`; then mark seeds `reverted` where a later commit's body says `This reverts commit <seed_commit>`.

- [ ] **Step 1: Write the failing tests**: `test_prepare_filters_and_limits` (repo with a 3-line fix, a 40-line fix, a `chore:` commit and a lockfile-only fix, `seed_limit=1` via config → one batch with only the 3-line fix); `test_prepare_skips_already_seeded`; `test_load_and_revert` (answer file with one entry and one skip → one seed; a revert commit → that seed `reverted`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: seed from the repository's fix history`.

### Task 7: Stats and merge rates

**Files:**
- Create: `scripts/kblib/stats.py`, `tests/test_stats.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 1, 2.
- Produces: `stats.round_stats(conn, rounds: int) -> list[dict]` (per round: `memory`, `candidates`, survivors after phase 1/2/3/3.5, `shipped`, `merged`, `closed`, `reverted`, `pending`, plus `by_kind` and `by_source` dicts of `{candidates, merged}`); `stats.format_stats(rows) -> str` (a table, memory-on and memory-off rounds labelled); `stats.kind_rates(conn, cfg) -> dict[str, tuple[float, float]]` → `(rate, decided)`.

`kind_rates` follows the spec's Steering section exactly: rate = `(merged + 1) / (decided + 2)`; decided = findings past phase 1 verification with outcome merged, closed, reverted, or dropped at any phase except `duplicate` and `in-open-pr`; seeds weighted `seed_weight` (a seed counts as merged, a reverted seed as a failure); `reverted` adds two failures; `changed_in_review` counts 0.5 merged. CLI `stats [--rounds N]` (default 5).

- [ ] **Step 1: Write the failing tests**: `test_round_stats_counts_phases` (a round with 6 candidates: 2 dropped at phase 1, 1 at phase 3, 2 merged, 1 shipped → candidates 6, after phase 1 4, after phase 3 3, merged 2, pending 1); `test_kind_rates_formula` (kind with 2 merged, 1 reverted, 2 dropped false-positive, 1 duplicate → decided 6 counting the revert twice, rate `(2+1)/(6+2) == 0.375`; a seed adds 0.3 to merged and decided); `test_off_rounds_labelled`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: per-round stats and merge rates`.

### Task 8: Stage 1 in SKILL.md, and BUGS.md removed

**Files:**
- Modify: `SKILL.md` (lines 3, 12, 82, 89, 167, 171, 177 and each phase), `README.md` ("Needs"), `references/browser-verification.md:7`, `scripts/check_evidence.py:12` (comment)

**Interfaces:**
- Consumes: CLI from Tasks 1–7 exactly as named there.

- [ ] **Step 1: Edit** per the spec's "Changes per phase" and "`BUGS.md` is gone" sections: phase 0 `round start`, `sync` (a failure is reported and the round goes on), `seed` when `seed status` shows room, `stats` in the update line; phase 1 `check` before verification, `add` for every candidate (dropped ones with their reason), `update` with NN/slug/verify for the batch, `list` in the update; phase 2 prompts get `show NN` output, results go in with `update`; phase 3 drops with `update --outcome dropped:<why> --phase 3`; phase 3.5 work list from `list --verify ui,http`, then `import-checks`; phase 4 `update --introduced-in`; phase 5 lists `kinds --pending` for approval, `import-ship` after publishing, table column `#`; phase 6 never deletes the data folder. Description loses "a BUGS.md". Line 12 and README list the data folder as the third write location. Add one paragraph, "Memory", near the top: the data folder, that memory is evidence and never a verdict, and that nothing from it goes into published text.
- [ ] **Step 2: Verify** `grep -rn "BUGS" SKILL.md README.md references scripts` → no output; `grep -rniE 'quick.?wins?|\bqw[_-]' scripts/kblib` → no output.
- [ ] **Step 3: Verify** every `kb.py <subcommand>` named in SKILL.md exists: `python3 -I scripts/kb.py <subcommand> --help` exits 0 for each.
- [ ] **Step 4: Commit** `Findings database: wire stage 1 into the phases and drop BUGS.md`.

### Task 9: Lessons in prompts

**Files:**
- Create: `scripts/kblib/brief.py`, `tests/test_brief.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 2, Task 7 `kind_rates` (shown only once Task 13 lands; before, the ranked-kinds block is omitted).
- Produces: `brief.brief(conn, repo_dir, cfg, role: "hunter" | "prosecutor", hot: list[str] | None) -> str` (empty string when the current round is memory-off; at most `brief_max_lines` lines: block "Known not-bugs here" one line per current `dropped:false-positive|dead-code|product-decision` lesson `path: reason`, newest first, superseded excluded, de-duplicated by `(path, reason)`; block "Hot files" when `hot` is given; no ids, no stats); `brief.supersede(conn, ref, by: str | None)`. CLI `brief --role R [--hot-from <hotspots.json>]`, `supersede <ref> [--by <ref>]`.

- [ ] **Step 1: Write the failing tests**: `test_brief_lists_round_n_false_positive_in_round_n_plus_1`; `test_brief_excludes_superseded`; `test_brief_caps_lines` (60 lessons → ≤ 40 lines); `test_brief_empty_when_memory_off`; `test_brief_has_no_ids` (`"#" not in output`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: known not-bugs in hunter and reviewer prompts`.

### Task 10: Hot files and the hunter plan

**Files:**
- Create: `scripts/kblib/hotspots.py`, `tests/test_hotspots.py`
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 2 `start_round` (`areas` stored), Task 6's exclusion list (import it from `seed.py`; don't copy it).
- Produces: `hotspots.hotspots(conn, repo_dir, base, cfg, top: int = 20) -> list[tuple[str, float]]`; `hotspots.plan_hunters(conn, cfg, round_id, areas: list[str], hot: list[str]) -> dict` → `{"explore": [area, ...], "hot": [[path, ...], ...], "kind": []}` and records `areas_explored` on the round. CLI `hotspots --base B [--top N]` (JSON list), `hunters --areas a,b,c [--hot-from FILE]` (JSON plan).

Hotspots: `git log origin/<base> --since=<hot_window_days> days --numstat --format=%x00%H%x00%s`; skip a commit touching more than `sweep_files` files or whose subject matches `(?i)\b(prettier|eslint --fix|format(ting)?|lint(ing)? fix|reformat)\b`; skip excluded paths; score = (lines / max lines) × (1 + findings in that path / max findings per path). Plan: explore count = `ceil(hunters × explore_share)`, at least 1; explore areas = those with the oldest last exploration (never explored first, then by round id, ties in given order); remaining hunters split the hot list round-robin; memory-off round → all hunters explore the areas in the given order, `hot` empty.

- [ ] **Step 1: Write the failing tests**: `test_formatter_sweep_not_on_top` (a 60-file prettier commit and a 51-file commit don't count; a 2-file 30-line change ranks first); `test_bug_density_raises_score`; `test_explore_rotates` (areas `a..e`, three rounds → round 1 explores `a,b`, round 2 `c,d`, round 3 `e,a`); `test_split_counts` (5 hunters → 2 explore, 3 hot); `test_memory_off_plan`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Findings database: hot files and rotating exploration`.

### Task 11: Private rules

**Files:**
- Create: `scripts/kblib/rules.py`, `tests/test_rules.py`, `tests/fixtures/` (rule examples)
- Modify: `scripts/kb.py`

**Interfaces:**
- Consumes: Task 2 `add_findings`, Task 7 counts.
- Produces:
  - Rule file `rules/<id>.json` (*plan decision:* JSON, not the spec's `.yml`, because the standard library has no YAML parser; ast-grep accepts JSON as YAML through `--inline-rules`). Shape: `{"id","engine":"ast-grep"|"html-regex","kind","pattern","sources":[ids],"must_match":str,"must_not_match":str, "ast_grep": {<ast-grep rule object incl. language>} | "regex": str, "glob": str}`.
  - `rules.candidates(conn, cfg) -> list[dict]` (patterns with ≥ `rule_from_merged` merged or ≥ `rule_from_seeds` seeds and no rule, with their source snippets)
  - `rules.test_rule(conn, rule: dict, scratch: str) -> list[str]` (errors: must_match not matched, must_not_match matched, a source snippet not matched)
  - `rules.add_rule(conn, repo_dir, path: str, scratch: str) -> str` (tests, then copies into `rules/` and inserts the row; refuses with the errors)
  - `rules.run_rules(conn, repo_dir, checkout: str, scratch: str) -> list[dict]` (candidate items for `add_findings`, `source="rule:<id>"`, `outcome="candidate"`; increments `hits`; returns `[]` in a memory-off round)
  - `rules.refresh_health(conn, cfg) -> list[str]` (recomputes decided/merged/false_positives from findings; turns off a rule below `rule_min_precision` after `rule_min_decided`; returns ids turned off)
  - CLI `rules candidates`, `rules add <file> --scratch DIR`, `rules test <file> --scratch DIR`, `rules run <checkout> --scratch DIR` (writes `rule-candidates.json` in scratch), `rules health`.
  - ast-grep runs as `npx --yes --prefix <scratch>/ast-grep @ast-grep/cli scan --inline-rules <json> --json <path>`.

- [ ] **Step 1: Write the failing tests**: `test_html_regex_rule_finds_source_on_old_commit` (a `<label for="x">` with no `id="x"` regex rule, built from two merged findings, matches its source snippet in a checkout of the old commit); `test_rule_refused_when_examples_fail`; `test_run_rules_hits_become_candidates`; `test_rule_auto_off` (4 decided hits, 1 merged → turned off); `test_rules_skip_when_memory_off`; `test_ast_grep_rule` decorated `@unittest.skipUnless(os.environ.get("KB_TEST_AST_GREP"), "needs npx and network")`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS (ast-grep test skipped by default; run once with `KB_TEST_AST_GREP=1` and record the result in the commit body).
- [ ] **Step 5: Commit** `Findings database: private rules that run before hunters`.

### Task 12: Stages 2 and 3 in SKILL.md

**Files:**
- Modify: `SKILL.md` (phases 1, 3, 4), `README.md` ("Needs": `npx` for rules)

- [ ] **Step 1: Edit**: phase 1 order becomes `rules run` → `hotspots` → `hunters` → spawn the planned hunters, each with `brief --role hunter` (hot hunters with their files) and the exploration ones with their area → `check` → verification. Rule hits are verified like any candidate. Phase 3's prosecutor gets `brief --role prosecutor`. After phase 4: `rules health`, then `rules candidates`; for each, draft a rule file in `$SCRATCH/rules/` and `rules add` it (private, no approval). Add `supersede` to the instructions for when a lesson turns out wrong.
- [ ] **Step 2: Verify** each named subcommand's `--help` exits 0; the grep from Task 8 Step 2 is still clean.
- [ ] **Step 3: Commit** `Findings database: wire lessons, hot files and rules into the phases`.

### Task 13: Steering by merge rate

**Files:**
- Modify: `scripts/kblib/hotspots.py` (`plan_hunters`), `scripts/kblib/brief.py`, `tests/test_hotspots.py`, `tests/test_brief.py`, `SKILL.md` (phase 1)

**Interfaces:**
- Consumes: Task 7 `kind_rates`.
- Produces: `plan_hunters` fills `"kind": [[kind, ...], ...]`: after the exploration hunters, the remaining hunters are split half hot (rounded up), half kind-focused; kind hunters take kinds with `decided ≥ unknown_kind_min_decided`, highest rate first, round-robin. `brief --role hunter` adds a "Kinds that ship here" block (kind names, ranked, no numbers).

- [ ] **Step 1: Write the failing tests**: `test_kind_hunters_follow_rates`; `test_unknown_kinds_not_focused` (a kind with 2 decided never in `"kind"`); `test_explore_share_fixed_with_stats` (with rich stats, explore count is still 2 of 5); `test_memory_off_ignores_rates`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS, whole suite PASS.
- [ ] **Step 5: Edit** SKILL.md phase 1: kind hunters get their kinds' definitions and `patterns <kind>`.
- [ ] **Step 6: Commit** `Findings database: steer hunters toward kinds that ship`.

### Task 14: Promotions

**Files:**
- Create: `scripts/kblib/promotions.py`, `tests/test_promotions.py`
- Modify: `scripts/kb.py`, `SKILL.md` (phase 5 stop, and a short "Promotions" subsection after phase 5)

**Interfaces:**
- Consumes: Task 11 rule rows, Task 7 counts.
- Produces: `promotions.promotions(conn, cfg) -> list[dict]` → `{"step": "repo-rule"|"repo-docs"|"skill-update", "subject": str, "evidence": str}`. CLI `promotions` prints them grouped by step.

Thresholds from the spec's table: repo-rule = active rule with `hits ≥ promote_rule_hits`, merged share ≥ `promote_rule_merged`, no false positive in its last `promote_rule_clean_rounds` rounds; repo-docs = a `(kind, pattern)` with ≥ `promote_doc_drops` findings `dropped:false-positive` and no rule; skill-update = a `source` or `area` whose drop share is ≥ `promote_skill_noise` in each of the last `promote_skill_rounds` rounds.

- [ ] **Step 1: Write the failing tests**: one test per step over threshold → listed; one per step just under → not listed (`hits 4`, merged share 0.79, 2 drops, noise in only 1 round).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Edit** SKILL.md: the phase 5 stop shows `promotions` in its own section with a draft for each (the rule or doc diff, or the SKILL.md diff); nothing is published or committed without a yes; approved repo promotions go through phases 2–5 as their own PR with the repository's conventions; a CI rule must pass on today's code (fix every match in the same PR, or add it as a warning); rule fixtures count as tests where the repository restricts tests and need an explicit yes; approved skill updates become a local commit in the skill's repository.
- [ ] **Step 6: Verify** the whole suite passes and the Task 8 greps are clean.
- [ ] **Step 7: Commit** `Findings database: propose rules, docs and skill updates`.
