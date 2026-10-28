"""Read-only archive checks and isolated recovery simulations; no network."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from future_targets import atomic_json, extend, snapshot_members
from membership_tracker import verify_targets


def verify(root, ledger_path):
    root = Path(root).resolve()
    target_file = root / 'inputs/targets.json'
    original = target_file.read_bytes()
    targets = json.loads(original)
    universe = json.loads((root / 'inputs/company-universe.json').read_text(encoding='utf-8'))
    ledger = json.loads(Path(ledger_path).read_text(encoding='utf-8'))
    report = verify_targets(ledger, targets)
    current = root / 'metadata/membership/current.json'
    if current.exists():
        snapshot = json.loads(current.read_text(encoding='utf-8'))
        rows = snapshot_members(snapshot, root, universe)
        report['latest_snapshot'] = {'date': snapshot['local_date'], 'members': len(rows),
                                     'original_csv_hash_verified': True, 'sha256': snapshot['sha256']}
    with tempfile.TemporaryDirectory(prefix='nifty-membership-check-') as folder:
        trial = Path(folder)
        missing_quarter = '2016-12-31'
        atomic_json(trial / 'inputs/targets.json', [r for r in targets if r['quarter_end'] != missing_quarter])
        atomic_json(trial / 'inputs/company-universe.json', universe)
        atomic_json(trial / 'metadata/membership/verified-ledger.json', ledger)
        recovered = extend(trial, today='2017-01-01', last_approved='2016-09-30')
        actual = [r for r in json.loads((trial / 'inputs/targets.json').read_text()) if r['quarter_end'] == missing_quarter]
        expected = [r for r in targets if r['quarter_end'] == missing_quarter]
        pairs = lambda rows: {(r['company_identity'], r['nse_symbol']) for r in rows}
        matched = recovered['added_targets'] == 50 and pairs(actual) == pairs(expected)
        report['missed_snapshot_simulation'] = {'quarter_end': missing_quarter, 'recovered': len(actual),
                                                 'matches_existing_plan': matched, 'original_files_changed': False}
        atomic_json(trial / 'inputs/targets.json', targets)
        future = extend(trial, today='2027-01-01')
        rejected = future['added_targets'] == 0 and any(r['quarter_end'] == '2026-12-31' for r in future['membership_gaps'])
        report['uncovered_future_simulation'] = {'quarter_end': '2026-12-31', 'correctly_flagged': rejected,
                                                 'added_targets': future['added_targets']}
        report['ok'] = report['ok'] and matched and rejected
    report['original_target_sha256'] = hashlib.sha256(original).hexdigest()
    report['original_targets_unchanged'] = target_file.read_bytes() == original
    report['ok'] = report['ok'] and report['original_targets_unchanged']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collector-root', type=Path, required=True)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.collector_root, args.ledger)
    atomic_json(args.out, result)
    print(json.dumps(result))
    if not result['ok']:
        raise SystemExit(1)
