"""The few lines of memory that go into hunter and reviewer prompts. No ids, no numbers."""
from kblib import findings

# Only the skill's own verdicts. Closed-PR comments come from GitHub, where anyone (and any bot) can write,
# so they never go into a prompt.
LESSONS = ('dropped:false-positive', 'dropped:dead-code', 'dropped:product-decision')

INTRO = {
    'hunter': 'Known not-bugs in this repository (checked in earlier rounds). Do not report these again unless '
              'the code around them has changed:',
    'prosecutor': 'Earlier rounds dropped these as not bugs. If a fix below looks like one of them, say so:',
}


def _lessons(conn):
    rows = conn.execute(
        f'SELECT path, reason, outcome FROM findings WHERE outcome IN ({", ".join("?" * len(LESSONS))}) '
        'AND reason IS NOT NULL AND superseded_by IS NULL ORDER BY updated DESC, id DESC', LESSONS).fetchall()
    seen, out = set(), []
    for r in rows:
        reason = ' '.join(r['reason'].split())
        if (r['path'], reason) in seen:
            continue
        seen.add((r['path'], reason))
        out.append(f'- {r["path"]}: {reason}')
    return out


def brief(conn, repo_dir, cfg, role, hot, kinds=None):
    if not findings.memory_on(conn):
        return ''
    cap = cfg['brief_max_lines']
    blocks = []
    if hot:
        blocks.append(['Files that changed most lately (look here first):'] + [f'- {p}' for p in hot])
    if kinds:
        blocks.append(['Bug kinds that have been accepted most here: ' + ', '.join(kinds)])
    fixed = sum(len(b) + 1 for b in blocks)
    lessons = _lessons(conn)
    room = cap - fixed - 1
    if lessons and room > 0:
        blocks.insert(0, [INTRO[role]] + lessons[:room])
    lines = []
    for b in blocks:
        if lines:
            lines.append('')
        lines += b
    return '\n'.join(lines[:cap]) + ('\n' if lines else '')


def supersede(conn, ref, by):
    fid = findings.resolve(conn, ref)
    target = findings.resolve(conn, by) if by else fid
    findings.update_finding(conn, f'#{fid}', superseded_by=target)
