"""Bring in what other scripts wrote: evidence/checks.tsv (phase 3.5) and ship.json (phase 5)."""
import json
import re

import check_evidence
from kblib import findings, store


def import_checks(conn, tsv_path):
    try:
        rows = check_evidence.rows_of(tsv_path)
    except SystemExit as e:
        raise store.KbError(str(e.code))
    except OSError as e:
        raise store.KbError(f'{tsv_path}: {e}')
    for row in rows:
        if row['outcome'] not in ('verified', 'not-verified', 'skipped'):
            raise store.KbError(f'{tsv_path}: {row["n"]}: unknown outcome {row["outcome"]!r}')
        findings.update_finding(conn, row['n'], browser_check=row['outcome'])
    return len(rows)


def import_ship(conn, ship_path):
    try:
        with open(ship_path) as f:
            spec = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise store.KbError(f'{ship_path}: {e}')
    count = 0
    for fx in spec.get('fixes', []):
        m = re.search(r'/pull/(\d+)', str(fx.get('pr') or ''))
        if not m:
            continue
        findings.update_finding(conn, fx['n'], outcome='shipped', outcome_phase='5', pr_number=int(m.group(1)),
                                pr_url=fx['pr'])
        count += 1
    return count
