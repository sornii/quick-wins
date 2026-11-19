# Phase 3.5 in detail: prove a fix in a running app

Read this before starting any app instance. It covers which fixes to check, how to run the app without overloading the machine, how to drive the browser, and what to do with each outcome.

## 1. Pick the fixes worth checking

A browser check costs minutes and memory, so spend it where reading code is weakest or where being wrong would hurt users. Each surviving fix carries a `verify` tag in the findings database, set in phase 1 (`kb.py list --verify ui,http` lists the ones to check):

- `ui`: the bug or the fix is visible on a page (missing text, wrong state, broken button, wrong redirect). Check these.
- `http`: a public endpoint or webhook whose response changes. Check these with a real HTTP client.
- `none`: logging, cron-only code, comments, pure text typos, anything whose effect a user can't observe. Skip these, and keep "not run in the app" in the PR.

If the user named the fixes they want checked, check exactly those.

## 2. Find out how the repo runs, and get a safe database

Look for the repo's own way to run a local stack before inventing one: `README`, `AGENTS.md`, `package.json` scripts, `docker-compose*.yml`, `things.config.ts` or similar tooling config, and the e2e test docs. You need four things:

- the dev command and its settings file
- a free port (not the one the user's own dev server uses)
- a seeded login (email and password of a test admin/member) that a real person would use
- a database that is safe to write to

The database matters most. Clicking through the app writes data (skipping onboarding, saving a form), so use a throwaway copy of the local seeded database, named for this run (for example `verify_<repo>`), and drop it in phase 6. Never point the instance at production, shared staging, or the database the user's own dev server is using. If the repo gives no safe database and no documented way to make one, skip this phase and say so; that is better than guessing.

Check the settings file the dev command uses before booting. If it turns on scheduled jobs or holds live keys for email, SMS or payments, run from a private copy under `$SCRATCH` with the jobs switched off, and confirm from the boot log where email goes (a local catcher, not a real provider). Avoid any check whose "before" would send a real message; pick a variant of the bug that shows without one. Delete the copy afterwards, since it holds secrets.

## 3. Run the app: one instance by default, three at the very most

Every instance is started through `scripts/app_slot.sh`, never with a bare dev command. A Meteor-sized dev server takes gigabytes of memory and several cores while it builds; a handful at once freezes the user's machine, and other repos may be running the same pipeline at the same moment. The script keeps a machine-wide count and refuses to start an instance past the cap (default 2, never above 3). If it refuses, wait or stop an instance; don't work around it.

Only the orchestrator starts and stops instances. Subagents get a URL to test against and are told not to start servers, because five agents each "just starting the app" is exactly the failure this guards against.

Run `app_slot.sh status` first. It also lists dev servers started some other way (the user's own, another tool's). Each of those counts toward the load, so lower `APP_SLOT_MAX` by one for each.

**The default plan uses a single instance for the whole round:**

1. Create one worktree for verification, detached at the base, on real disk next to the fix worktrees: `git -C <repo> worktree add --detach <repo>-worktrees/verify origin/<base>`. Don't put it under `/tmp`: that is often RAM-backed (`df -T /tmp` shows `tmpfs`), so a 2 GB build tree eats memory, and hard links from the checkout fail across devices.
2. Give it its own dependencies. Hard-link the checkout's `node_modules` (`cp -al`), copy any warm build cache the repo's tooling documents (for Meteor, `.meteor/local` without its `db`), then run the repo's install command inside the worktree and restore the lockfile if it changed. A symlink is not enough here: the base may need packages the user's checkout hasn't installed yet, and the app then crashes at boot.
3. Start it: `app_slot.sh run <repo>-verify <repo>-worktrees/verify '<dev command with port, db, settings>'`, then `app_slot.sh wait <url> 900`. The port answering doesn't mean the app is healthy: read the end of the log for a crash loop before testing.
4. **Before:** run every check against this base build. Each one should show the bug.
5. Bring the fixes in without committing: `git -C <repo>-worktrees/verify cherry-pick -n <fix commits or origin/<fix branches>>` (they touch different lines, so they apply cleanly; if one conflicts, check it alone afterwards). The dev server hot-reloads; wait for the "restarted" line in the log, then a few more seconds for the client bundle, before testing.
6. **After:** run every check again. Each bug should be gone.
7. `app_slot.sh stop <repo>-verify`, and confirm `status` shows it gone.

One boot, one rebuild, every fix checked both ways. Use a second instance (base and fixed side by side) only when hot reload can't be trusted for a fix, such as server startup code or settings. A third is the hard limit and should be rare.

Stop the instances as soon as the checks are done, and also when the run is interrupted or fails. Before leaving this phase, `app_slot.sh status` must show none of yours.

## 4. Drive the browser the way a person would

The point of this phase is evidence that a real user sees the bug and then stops seeing it. A check that reaches into the app's internals proves something else, and can pass while the user's path is still broken. So the script may do only what a person at the keyboard can do:

- Open the site at an entry URL, then get around by clicking links, menus and buttons. Typing a URL in the address bar is fine where a user would have that URL (a bookmarked page, a link from an email).
- Sign in through the login form with the seeded account. Sign out through the menu.
- Find elements the way a person recognises them: by role, label and visible text (`getByRole`, `getByLabel`, `getByText`, `getByPlaceholder`). Use a test id when the repo already has one for that element. CSS classes and DOM structure are a last resort.
- Type into fields, pick from selects, upload through the file input, press keys.
- Judge by what is on screen: visible text, whether a control is enabled, the URL, a toast. Take a screenshot at the moment that shows it.
- Let Playwright's auto-waiting do the waiting; assert on what appears instead of sleeping. Don't wait for `networkidle`: apps that keep a websocket open (Meteor, Phoenix LiveView) never reach it.

And it must not:

- run code inside the page to change state (`page.evaluate`, calling app methods or the framework's client API from the console, setting reactive variables)
- inject sessions: no cookies, tokens or `localStorage` written by the script to skip the login
- edit the database or call internal endpoints in the middle of a flow to force the state the test wants
- mock or intercept network calls (`page.route`), or stub the clock
- force the UI: no `force: true`, no dispatching synthetic events, no removing `disabled`, no un-hiding elements
- treat a hidden or covered element as clickable because the DOM has it

Preconditions follow the same spirit. Prefer a seeded account that is already in the needed state (an org with no integration connected), or put the app in that state through its own screens. If the only way is to prepare data directly, do it once before the flow starts, on the throwaway database only, and say so in the evidence.

One environment setting is allowed because it changes the conditions, not the app: slowing the network (Chromium's network emulation) when a state only shows while a request is in flight. Say that you used it.

For `http` fixes, the real user is an API client: call the endpoint with `curl` or Playwright's request context, using a key or token created the way a customer creates one. That can be a real flow of its own (create the app key in the dashboard, approve it on the OAuth screen, exchange the code); do it through the UI rather than inserting a token into the database. When the response is the same before and after and the fix only removes a server-side effect, the evidence is the server log: note the log length before the call and show what was written after it. Always add a control request that should still succeed.

If the state can't be reached as a user would reach it, don't force it. Record the fix as "not verifiable as a user" with the reason. That is a useful finding: it often means the bug is less reachable than the reviewers thought.

## Names in test data

Anything the checks create is visible to someone: an app key name in a screenshot, a database name in a log, an account name. Give these plain, descriptive names for what they are (`Local API check`, `verify_<repo>`, `Test member`). Never use this skill's name or an abbreviation of it; see "Naming" in SKILL.md.

## 5. Tooling

- **Playwright on headless Chromium is the only driver.** Every check is a script that launches its own browser (`chromium.launch({ headless: true })`), with a fixed viewport, and takes `before` or `after` as an argument. That is what makes the two runs comparable and lets a failed capture be taken again. Other ways to reach a browser (an in-app or desktop browser pane, a Chrome extension, MCP browser tools, computer use) are not used here, even when the session offers them and even for a first look: they act in windows on the user's screen, their screenshots fail when a pane isn't visible, and they leave no script behind. If Playwright can't be made to run, tell the user and record the fixes as `not-verified`; don't reach for another tool.
- Use the repo's existing Playwright install and config if it has one (often in a `tests/` or `e2e/` package), pointing its base URL at your instance, with one browser project (Chromium) and one worker. Reuse its page objects and login helpers when they act through the UI; skip helpers that log in by API or seed through the database.
- If the repo has no Playwright, install it in a scratch directory, `$SCRATCH/verify-scripts/` (`npm init -y`, then `npm install playwright@<version>`), and run the scripts from there with `node`; don't add dependencies to the repo. Pick the version whose Chromium is already downloaded, so nothing large is fetched: `ls ~/.cache/ms-playwright` shows the builds (`chromium_headless_shell-1200`), and a Playwright's build number is in its `playwright-core/browsers.json`. Set `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` for the install. Only when no cached build matches, run `npx playwright install chromium` in that directory.
- Keep the scripts under `$SCRATCH/verify-scripts/` and the output under `$SCRATCH/evidence/`: `NN-<slug>-before.png` and `NN-<slug>-after.png` for `ui` (the annotated files; keep the raw captures under another name, such as `NN-<slug>-before.raw.png`), `NN-<slug>-before.txt` and `-after.txt` with the responses for `http`. `scripts/check_evidence.py` looks for exactly these names. These are throwaway: don't commit specs or screenshots to the fix branches unless the user asked for tests.
- Keep the frame to what matters. Capture the region around the element (`locator.screenshot()`, or `page.screenshot({ clip })`), not the whole page: a full page tends to show things that shouldn't be published, such as the scratch directory's path in a details pane, a home directory, or account names. Nothing can grep a picture, so open each one and look before it is uploaded.
- Point at the change. For every screenshot, read the bounding box of the element that matters (`locator.boundingBox()`, which only reads) and run `scripts/annotate.py <in.png> <out.png> <x> <y> <w> <h> "<label>"`, which draws a box, an arrow and a label on the image file (never on the page). Keep the label to the observed value in a few words (`focus: nothing`, `"the the"`, `disabled: true`); the PR text explains the rest, and a long label covers the page. Use the same form before and after.
- Make sure the target can be seen. Scrolling an element "into view" can leave it under a sticky header. Bring it to the middle of the viewport with the mouse wheel, then check that nothing covers its centre (`document.elementFromPoint`, read only); if something does, or the element is visually hidden (`sr-only`), point at the visible control it belongs to. Look at every annotated screenshot before publishing it.
- Record the state you assert on, not only a picture: some differences don't show in a screenshot (a `disabled` button that looks the same, a cursor). Print the visible text, enabled/disabled and computed cursor next to each screenshot.
- Run the checks one browser at a time against an instance. Parallel browsers against one dev server make timing-dependent results flaky.

## 6. What each outcome means

| Base build | With the fix | Meaning | Do |
|---|---|---|---|
| bug shows | bug gone | verified | Write "Verified in a browser: <what was seen before and after>" in the PR |
| bug does not show | n/a | probably a false positive, or the precondition is wrong | Re-read the code path with what you saw; fix the precondition and retry once, otherwise drop the fix or reword it as latent |
| bug shows | bug still shows | fix is incomplete or aimed at the wrong place | Reopen the fix; don't ship it as is |
| can't reach the state as a user | n/a | not verifiable as a user | Keep the fix on the code-review evidence and say so |
| app wouldn't start, no safe database | n/a | not run | Say "not run in the app" and why |
| bug shows | bug gone, but a screenshot is missing | not verified yet | Take the missing capture again. If it can't be taken, the fix is not verified, however clear the page text was |

Only the first row is `verified`. Write the result of every fix that survived phase 3 to `$SCRATCH/evidence/checks.tsv`, one line each, including the ones you didn't check:

```
03  save-button-label  ui    verified
07  settings-toast     ui    not-verified  the desktop panel can't be driven by Playwright
09  retry-log-level    none  skipped
```

Then run `python3 -I scripts/check_evidence.py $SCRATCH`. It passes only when each `verified` fix has both annotated pictures (or both response texts) and its `PR-NN-<slug>.md` shows them, and when no other fix's PR text claims a browser check. The phase isn't finished until it passes; phase 5 runs it again before the stop, and phase 6 runs it with `--published` to confirm the pictures reached the PRs before the scratch directory is deleted. The reason for the gate: "checked in a browser" in a PR is a claim a reviewer can't test, and the pair of pictures is what backs it.

Put the result, in a line or two, where the repository's PR template keeps its testing notes, and the screenshots where it has a place for them; when it has neither, add a "Before and after" section. Write the body with local paths in a Before/After table and let `gh` upload them (`gh pr create` takes the same flags):

```sh
gh pr edit <n> --body-file body.md --attach NN-<slug>-before.png --attach NN-<slug>-after.png
```

```markdown
| Before | After |
|---|---|
| ![before](NN-<slug>-before.png) | ![after](NN-<slug>-after.png) |
```

Use the evidence file names in the table as they are: `gh` rewrites each reference to the uploaded image, and `check_evidence.py` looks for those names in the PR text.

For `http` checks, put the before/after responses in the table as text, with tokens and secrets replaced by `<token>`. Screenshots show whatever the signed-in account shows (name, email, org); use a seeded test account, or tell the user what is visible before publishing. `annotate.py` needs Pillow (`python3 -c 'import PIL'`).
