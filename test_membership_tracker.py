"""Synthetic-only tests: no user membership data or source downloads."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from membership_tracker import POLICY, active_events, apply_event, members, resolve, verify_targets
from future_targets import atomic_json, extend, install, membership_at, snapshot_members


def member(symbol, identity=None):
    return {'company_identity': identity or symbol, 'company_name': symbol + ' Ltd.', 'nse_symbol': symbol}


def fixture():
    opening = [member('CO' + str(i)) for i in range(50)]
    event = {'id': 'replacement', 'effective_date': '2026-03-30', 'published_date': '2026-02-01',
             'removed': [copy.deepcopy(opening[0])], 'added': [member('NEW')], 'updated': [],
             'source_ids': ['source'], 'supersedes': []}
    anchor = apply_event(opening, event)
    ledger = {'schema_version': 1, 'policy': POLICY,
              'sources': [{'id': 'source', 'url': 'https://www.niftyindices.com/Press_Release/test.pdf', 'sha256': 'a' * 64}],
              'anchor': {'date': '2026-04-01', 'members': anchor, 'source_ids': ['source']},
              'coverage': [{'from': '2026-01-01', 'through': '2026-12-31', 'reviewed': True,
                            'basis': 'synthetic reviewed history', 'source_ids': ['source']}],
              'events': [event]}
    return opening, ledger


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.opening, self.ledger = fixture()

    def test_missing_snapshot_reconstructs_from_later_anchor(self):
        result = resolve(self.ledger, '2026-03-29')
        self.assertEqual(result['members'], members(self.opening))
        self.assertEqual(result['event_ids'], ['replacement'])

    def test_effective_date_inclusive_and_announcement_not_membership(self):
        self.assertIn('CO0', {r['nse_symbol'] for r in resolve(self.ledger, '2026-02-15')['members']})
        self.assertIn('NEW', {r['nse_symbol'] for r in resolve(self.ledger, '2026-03-30')['members']})

    def test_forward_and_reverse_round_trip(self):
        event = self.ledger['events'][0]
        self.assertEqual(apply_event(apply_event(self.opening, event), event, True), members(self.opening))
        self.ledger['anchor'] = {'date': '2026-01-01', 'members': self.opening, 'source_ids': ['source']}
        self.assertIn('NEW', {r['nse_symbol'] for r in resolve(self.ledger, '2026-04-01')['members']})

    def test_accelerated_notice_supersedes_original(self):
        old = copy.deepcopy(self.ledger['events'][0])
        old['id'], old['effective_date'] = 'original', '2026-03-31'
        self.ledger['events'][0]['supersedes'] = ['original']
        self.ledger['events'].append(old)
        self.assertEqual(len(active_events(self.ledger['events'])), 1)
        self.assertEqual(len(resolve(self.ledger, '2026-04-01')['members']), 50)
        self.assertEqual(resolve(self.ledger, '2026-03-29')['members'], members(self.opening))

    def test_unknown_and_cyclic_supersession_rejected(self):
        self.ledger['events'][0]['supersedes'] = ['missing']
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-04-01')
        self.ledger['events'][0]['supersedes'] = ['replacement']
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-04-01')

    def test_duplicate_ids_and_same_date_events_rejected(self):
        event = copy.deepcopy(self.ledger['events'][0])
        self.ledger['events'].append(event)
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-01-01')
        event['id'] = 'second'
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-01-01')

    def test_coverage_gap_rejected_even_with_fifty_members(self):
        span = self.ledger['coverage'][0]
        self.ledger['coverage'] = [{**span, 'through': '2026-02-28'}, {**span, 'from': '2026-03-02'}]
        with self.assertRaisesRegex(ValueError, 'coverage'): resolve(self.ledger, '2026-02-01')

    def test_unreviewed_and_outside_coverage_rejected(self):
        self.ledger['coverage'][0]['reviewed'] = False
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-02-01')
        self.ledger['coverage'][0]['reviewed'] = True
        with self.assertRaises(ValueError): resolve(self.ledger, '2027-01-01')

    def test_rename_keeps_company_identity(self):
        old, new = member('OLD', 'CONTINUING'), member('RENAMED', 'CONTINUING')
        rows = members([old] + self.opening[1:])
        rename = {'removed': [], 'added': [], 'updated': [{'before': old, 'after': new}]}
        after = apply_event(rows, rename)
        self.assertEqual(next(r['nse_symbol'] for r in after if r['company_identity'] == 'CONTINUING'), 'RENAMED')
        self.assertEqual(apply_event(after, rename, True), rows)

    def test_merger_replacement_is_not_a_rename(self):
        self.ledger['events'][0]['updated'] = [{'before': member('HDFC'), 'after': member('HDFCBANK')}]
        with self.assertRaisesRegex(ValueError, 'rename'): resolve(self.ledger, '2026-01-01')

    def test_current_isin_not_backfilled(self):
        for row in self.ledger['anchor']['members']: row['current_isin'] = 'IN1234567890'
        self.assertTrue(all('current_isin' not in row for row in resolve(self.ledger, '2026-04-01')['members']))

    def test_no_arbitrary_trimming_of_temporary_51st_security(self):
        with self.assertRaises(ValueError): members(self.opening + [member('TEMPORARY')])

    def test_wrong_removal_rejected_despite_correct_count(self):
        self.ledger['events'][0]['removed'][0]['nse_symbol'] = 'UNRELATED'
        with self.assertRaises(ValueError): apply_event(self.opening, self.ledger['events'][0])

    def test_wrong_membership_not_accepted_just_because_count_is_50(self):
        targets = [{**r, 'quarter_end': '2026-03-31'} for r in resolve(self.ledger, '2026-03-31')['members']]
        targets[0] = {**member('WRONG'), 'quarter_end': '2026-03-31'}
        report = verify_targets(self.ledger, targets, '2026-03-31', '2026-03-31')
        self.assertFalse(report['ok'])

    def test_unofficial_or_unhashed_evidence_rejected(self):
        self.ledger['sources'][0]['url'] = 'https://unrelated.example/test.pdf'
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-04-01')
        self.ledger['sources'][0]['url'] = 'https://www.niftyindices.com/test.pdf'
        self.ledger['sources'][0]['sha256'] = ''
        with self.assertRaises(ValueError): resolve(self.ledger, '2026-04-01')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opening, self.ledger = fixture()
        self.universe = [{'identity': r['company_identity'], 'symbols': [r['nse_symbol']]} for r in self.opening + [member('NEW')]]
        atomic_json(self.root / 'inputs/targets.json', [])
        atomic_json(self.root / 'inputs/company-universe.json', self.universe)
        atomic_json(self.root / 'metadata/membership/verified-ledger.json', self.ledger)

    def extend(self, **kwargs):
        return extend(self.root, today='2026-07-01', last_approved='2026-03-31', **kwargs)

    def snapshot(self, date='2026-06-30'):
        rows = [{'Company Name': r['company_name'], 'Industry': 'Test', 'Symbol': r['nse_symbol'],
                 'Series': 'EQ', 'ISIN Code': 'IN1234567890'} for r in self.ledger['anchor']['members']]
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
        body = stream.getvalue().encode()
        digest = hashlib.sha256(body).hexdigest()
        file = 'metadata/membership/' + digest + '.csv'
        (self.root / file).write_bytes(body)
        return {'source_url': 'https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv',
                'local_date': date, 'retrieved_at_utc': date + 'T03:00:00Z',
                'file': file, 'sha256': digest, 'records': rows}

    def test_event_fallback_adds_complete_quarter_with_provenance(self):
        result = self.extend()
        self.assertEqual(result['added_targets'], 50)
        rows = json.loads((self.root / 'inputs/targets.json').read_text())
        self.assertEqual(rows[0]['membership_evidence']['method'], 'reviewed_event_reconstruction')
        self.assertEqual(rows[0]['bse_security_code'], '')
        self.assertEqual(self.extend()['added_targets'], 0)

    def test_uncovered_future_is_gap_never_guessed(self):
        self.ledger['coverage'][0]['through'] = '2026-04-01'
        atomic_json(self.root / 'metadata/membership/verified-ledger.json', self.ledger)
        before = (self.root / 'inputs/targets.json').read_bytes()
        result = self.extend()
        self.assertEqual(len(result['membership_gaps']), 1)
        self.assertEqual((self.root / 'inputs/targets.json').read_bytes(), before)

    def test_exact_snapshot_resolves_without_event_coverage(self):
        snapshot = self.snapshot()
        result = membership_at('2026-06-30', self.root, self.universe, [snapshot], None)
        self.assertEqual(result['method'], 'official_exact_date_snapshot')
        self.assertEqual(len(result['members']), 50)

    def test_nearby_observation_does_not_replace_quarter_end(self):
        with self.assertRaises(ValueError): membership_at('2026-06-30', self.root, self.universe, [self.snapshot('2026-07-01')], None)

    def test_bad_hash_and_tampered_manifest_rejected(self):
        snapshot = self.snapshot()
        snapshot['sha256'] = 'b' * 64
        with self.assertRaises(ValueError): snapshot_members(snapshot, self.root, self.universe)
        snapshot = self.snapshot()
        snapshot['records'][0]['Symbol'] = 'CHANGED'
        with self.assertRaises(ValueError): snapshot_members(snapshot, self.root, self.universe)

    def test_snapshot_path_escape_and_incorrect_observation_date_rejected(self):
        snapshot = self.snapshot()
        snapshot['file'] = '../elsewhere.csv'
        with self.assertRaises(ValueError): snapshot_members(snapshot, self.root, self.universe)
        snapshot = self.snapshot()
        snapshot['retrieved_at_utc'] = '2026-07-01T03:00:00Z'
        with self.assertRaises(ValueError): snapshot_members(snapshot, self.root, self.universe)

    def test_conflicting_same_date_snapshots_require_review(self):
        first = self.snapshot()
        self.ledger['anchor']['members'] = self.opening
        second = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Conflicting'): membership_at('2026-06-30', self.root, self.universe, [first, second], None)

    def test_no_partial_quarter_when_company_mapping_missing(self):
        atomic_json(self.root / 'inputs/company-universe.json', self.universe[:-1])
        self.assertEqual(self.extend()['added_targets'], 0)
        self.assertEqual(json.loads((self.root / 'inputs/targets.json').read_text()), [])

    def test_conflicting_existing_targets_preserved_for_review(self):
        self.extend()
        target = self.root / 'inputs/targets.json'
        rows = json.loads(target.read_text())
        rows[0]['nse_symbol'] = 'WRONG'
        atomic_json(target, rows)
        before = target.read_bytes()
        result = self.extend()
        self.assertEqual(len(result['membership_conflicts']), 1)
        self.assertEqual(target.read_bytes(), before)

    def test_same_day_quarter_not_added_until_day_is_complete(self):
        result = extend(self.root, today='2026-06-30', last_approved='2026-03-31')
        self.assertEqual(result['added_targets'], 0)

    def test_duplicate_targets_fail_without_mutation(self):
        target = self.root / 'inputs/targets.json'
        row = {**member('CO1'), 'quarter_end': '2026-03-31'}
        atomic_json(target, [row, row])
        before = target.read_bytes()
        with self.assertRaises(ValueError): self.extend()
        self.assertEqual(target.read_bytes(), before)

    def test_installer_keeps_backup_and_existing_schedule(self):
        (self.root / 'extend_targets.py').write_text('# original\n')
        (self.root / 'run_update.cjs').write_text('// existing scheduler entry\n')
        ledger = self.root / 'metadata/membership/verified-ledger.json'
        report = install(self.root, ledger)
        self.assertFalse(report['schedule_changed'])
        self.assertTrue(list(Path(report['backup']).glob('extend_targets-*.py')))
        self.assertEqual((self.root / 'run_update.cjs').read_text(), '// existing scheduler entry\n')
        (self.root / 'pipeline.lock').write_text('running')
        with self.assertRaises(ValueError): install(self.root, ledger)


if __name__ == '__main__':
    unittest.main()
