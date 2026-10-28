"""Extend the existing PC collector using verified snapshots or reviewed history.

No network requests, AI calls, security-code guesses, or data publication.
"""
import argparse
import calendar
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from membership_tracker import POLICY, iso, members, official, resolve

INDIA = timezone(timedelta(hours=5, minutes=30))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def snapshot_members(snapshot, root, universe):
    official(snapshot['source_url'])
    observed = datetime.fromisoformat(snapshot['retrieved_at_utc'].replace('Z', '+00:00'))
    if observed.tzinfo is None or observed.astimezone(INDIA).date().isoformat() != snapshot['local_date']:
        raise ValueError('Snapshot timestamp disagrees with its India observation date')
    folder = (root / 'metadata/membership').resolve()
    source = (root / snapshot['file']).resolve()
    if not source.is_relative_to(folder) or source.suffix.lower() != '.csv':
        raise ValueError('Snapshot source must be an archived membership CSV')
    body = source.read_bytes()
    if len(body) > 1_000_000 or hashlib.sha256(body).hexdigest() != snapshot['sha256']:
        raise ValueError('Archived snapshot hash mismatch')
    rows = [{k.strip(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(body.decode('utf-8-sig')))]
    if rows != snapshot['records']:
        raise ValueError('Snapshot manifest differs from its original CSV')
    result = []
    for row in rows:
        if row.get('Series') != 'EQ' or not row.get('ISIN Code', '').startswith('IN') or len(row.get('ISIN Code', '')) != 12:
            raise ValueError('Unexpected snapshot security schema')
        matches = [c for c in universe if row['Symbol'] in c['symbols']]
        if len(matches) != 1:
            raise ValueError('Snapshot symbol needs an unambiguous company-universe mapping: ' + row['Symbol'])
        result.append({'company_identity': matches[0]['identity'], 'company_name': row['Company Name'], 'nse_symbol': row['Symbol']})
    return members(result)


def membership_at(quarter, root, universe, snapshots, ledger):
    observations = [s for s in snapshots if s['local_date'] == quarter]
    if observations:
        resolved = [snapshot_members(s, root, universe) for s in observations]
        if any(r != resolved[0] for r in resolved[1:]):
            raise ValueError('Conflicting official observations on the same date; review required')
        return {'date': quarter, 'policy': POLICY, 'method': 'official_exact_date_snapshot',
                'members': resolved[0], 'event_ids': [],
                'source_sha256': observations[-1]['sha256'], 'source_url': observations[-1]['source_url']}
    if ledger is None:
        raise ValueError('No exact-date snapshot or reviewed event ledger')
    result = resolve(ledger, quarter)
    result['ledger_sha256'] = hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()
    return result


def extend(root, today=None, last_approved='2026-09-30', ledger_path=None):
    root = Path(root).resolve()
    today = today or datetime.now(INDIA).date().isoformat()
    iso(today); iso(last_approved)
    targets_file = root / 'inputs/targets.json'
    targets = json.loads(targets_file.read_text(encoding='utf-8'))
    universe = json.loads((root / 'inputs/company-universe.json').read_text(encoding='utf-8'))
    keys = [(r['company_identity'], r['quarter_end']) for r in targets]
    if len(keys) != len(set(keys)):
        raise ValueError('Existing target plan has duplicate company-quarter records')
    log = root / 'metadata/membership/snapshots.jsonl'
    snapshots = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line.strip()] if log.exists() else []
    ledger_file = Path(ledger_path) if ledger_path else root / 'metadata/membership/verified-ledger.json'
    ledger = json.loads(ledger_file.read_text(encoding='utf-8')) if ledger_file.exists() else None
    additions, gaps, conflicts, resolved_quarters = [], [], [], []
    for year in range(int(last_approved[:4]), int(today[:4]) + 1):
        for month in (3, 6, 9, 12):
            quarter = f'{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}'
            if quarter <= last_approved or quarter >= today:
                continue
            existing = [r for r in targets if r['quarter_end'] == quarter]
            try:
                evidence = membership_at(quarter, root, universe, snapshots, ledger)
                expected = {(r['company_identity'], r['nse_symbol']) for r in evidence['members']}
                if existing:
                    prior = {(r['company_identity'], r['nse_symbol']) for r in existing}
                    if len(existing) != 50 or prior != expected:
                        conflicts.append({'quarter_end': quarter, 'reason': 'Existing future targets disagree with new evidence; preserved for review'})
                    continue
                pending = []
                for row in evidence['members']:
                    matches = [c for c in universe if c['identity'] == row['company_identity'] and row['nse_symbol'] in c['symbols']]
                    if len(matches) != 1:
                        raise ValueError('Verified event member needs a company-universe mapping: ' + row['company_identity'])
                    provenance = {k: v for k, v in evidence.items() if k != 'members'}
                    pending.append({**row, 'quarter_end': quarter, 'bse_security_code': '',
                                    'mapping_status': 'resolve_from_official_bse',
                                    'membership_sheet': evidence['method'] + ':' + quarter,
                                    'membership_evidence': provenance})
                additions.extend(pending)
                resolved_quarters.append({'quarter_end': quarter, 'method': evidence['method']})
            except (ValueError, KeyError, OSError) as error:
                gaps.append({'quarter_end': quarter, 'reason': str(error)})
    if additions:
        atomic_json(targets_file, targets + additions)
    result = {'added_targets': len(additions), 'total_targets': len(targets) + len(additions),
              'membership_gaps': gaps, 'membership_conflicts': conflicts,
              'resolved_quarters': resolved_quarters, 'policy': POLICY,
              'checked_through_local_date': today, 'status': 'review_required' if gaps or conflicts else 'ok'}
    atomic_json(root / 'metadata/future-target-status.json', result)
    return result


def install(root, ledger_path):
    """Install only the target-selection adapter into the existing single PC job."""
    root = Path(root).resolve()
    if (root / 'pipeline.lock').exists():
        raise ValueError('Collector is running; install after the existing run finishes')
    for required in ('extend_targets.py', 'run_update.cjs', 'inputs/targets.json'):
        if not (root / required).is_file():
            raise ValueError('Not an existing PC collector root')
    ledger_body = Path(ledger_path).read_bytes()
    ledger = json.loads(ledger_body)
    resolve(ledger, ledger['anchor']['date'])
    backup = root / 'metadata/membership/adapter-backup'
    backup.mkdir(parents=True, exist_ok=True)
    destination = root / 'extend_targets.py'
    old_body = destination.read_bytes()
    digest = hashlib.sha256(old_body).hexdigest()
    (backup / ('extend_targets-' + digest + '.py')).write_bytes(old_body)
    # Module bytes are copied from this reviewed checkout, not downloaded code.
    for name in ('membership_tracker.py', 'future_targets.py'):
        destination_module = root / name
        source_body = (Path(__file__).parent / name).read_bytes()
        if destination_module.exists() and destination_module.read_bytes() != source_body:
            previous_body = destination_module.read_bytes()
            (backup / (name + '-' + hashlib.sha256(previous_body).hexdigest())).write_bytes(previous_body)
        temp = destination_module.with_suffix('.py.tmp')
        temp.write_bytes(source_body)
        os.replace(temp, destination_module)
    atomic_json(root / 'metadata/membership/verified-ledger.json', ledger)
    wrapper = ('"""Reviewed membership adapter for the existing daily collector."""\n'
               'import json\nfrom pathlib import Path\nfrom future_targets import extend as _extend\n'
               'ROOT = Path(__file__).resolve().parent\n'
               'def extend(root=ROOT):\n    result = _extend(root)\n    print(json.dumps(result))\n    return result\n'
               'if __name__ == "__main__":\n    extend()\n')
    temp = destination.with_suffix('.py.tmp')
    temp.write_text(wrapper, encoding='utf-8', newline='\n')
    os.replace(temp, destination)
    report = {'installed_root': str(root), 'backup': str(backup),
              'ledger_coverage_through': ledger['anchor']['date'],
              'schedule_changed': False, 'network_requests': 0}
    atomic_json(root / 'metadata/membership/adapter-installation.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['extend', 'install'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ledger', type=Path)
    args = parser.parse_args()
    if args.command == 'install' and not args.ledger:
        parser.error('install requires --ledger')
    print(json.dumps(install(args.root, args.ledger) if args.command == 'install' else extend(args.root, ledger_path=args.ledger)))
