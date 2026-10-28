"""Offline, evidence-bounded reconstruction of the approved core-50 universe.

This is a retrospective membership engine, not a PDF interpreter or an IWF
calculator. Current ISINs are deliberately absent from historical member rows.
"""
import argparse
import calendar
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zipfile

ALIASES = {'ETERNAL': 'ZOMATO', 'TMPV': 'TATAMOTORS'}
POLICY = 'approved_core_50_companies_v1'
OFFICIAL_HOSTS = {'www.niftyindices.com', 'niftyindices.com',
                  'nsearchives.nseindia.com', 'www.nseindia.com'}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def iso(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Expected ISO calendar date')
    return date.fromisoformat(value)


def official(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in OFFICIAL_HOSTS or parsed.username or parsed.password:
        raise ValueError('Membership evidence must reference an official HTTPS source')


def members(rows):
    # Whitelist fields: an observed current ISIN is not a historical identifier.
    result = [{k: r[k] for k in ('company_identity', 'company_name', 'nse_symbol')} for r in rows]
    if len(result) != 50 or any(not isinstance(v, str) or not v.strip() for r in result for v in r.values()):
        raise ValueError('Expected 50 complete core-company records')
    for key in ('company_identity', 'nse_symbol'):
        if len({r[key] for r in result}) != 50:
            raise ValueError('Duplicate member ' + key)
    return sorted(result, key=lambda r: r['company_identity'])


def by_identity(rows):
    return {r['company_identity']: r for r in members(rows)}


def active_events(events):
    lookup = {e['id']: e for e in events}
    if len(lookup) != len(events):
        raise ValueError('Duplicate event ID')
    superseded = set()
    def ancestors(event_id, path):
        if event_id not in lookup or event_id in path:
            raise ValueError('Unknown or cyclic supersession')
        result = set()
        for old in lookup[event_id].get('supersedes', []):
            result.add(old)
            result.update(ancestors(old, path | {event_id}))
        return result
    for event in events:
        superseded.update(ancestors(event['id'], set()))
    result = [e for e in events if e['id'] not in superseded]
    if len({e['effective_date'] for e in result}) != len(result):
        raise ValueError('Combine same-date changes into one reviewed atomic event')
    return sorted(result, key=lambda e: e['effective_date'])


def validate_ledger(ledger):
    if ledger.get('schema_version') != 1 or ledger.get('policy') != POLICY:
        raise ValueError('Unknown membership schema/policy')
    anchor = ledger['anchor']
    iso(anchor['date'])
    members(anchor['members'])
    sources = ledger['sources']
    if not sources or len({s['id'] for s in sources}) != len(sources):
        raise ValueError('Invalid source registry')
    for source in sources:
        official(source['url'])
        if not re.fullmatch('[a-f0-9]{64}', source['sha256']):
            raise ValueError('Missing evidence hash')
    source_ids = {s['id'] for s in sources}
    for item in [anchor, *ledger['events'], *ledger['coverage']]:
        if not item.get('source_ids') or not set(item['source_ids']) <= source_ids:
            raise ValueError('Missing or unknown evidence reference')
    for event in ledger['events']:
        iso(event['effective_date'])
        if event.get('published_date'):
            iso(event['published_date'])
        for key in ('removed', 'added'):
            rows = event.get(key, [])
            ids = [r['company_identity'] for r in rows]
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate event member')
        for update in event.get('updated', []):
            if update['before']['company_identity'] != update['after']['company_identity']:
                raise ValueError('A rename cannot merge different company identities')
    for span in ledger['coverage']:
        if iso(span['from']) > iso(span['through']) or span.get('reviewed') is not True or not span.get('basis'):
            raise ValueError('Coverage requires an explicit reviewed interval and basis')
    return active_events(ledger['events'])


def covered(coverage, start, end):
    cursor = iso(start)
    end_date = iso(end)
    for span in sorted(coverage, key=lambda c: c['from']):
        left, right = iso(span['from']), iso(span['through'])
        if left > cursor:
            return False
        if right >= cursor:
            if right >= end_date:
                return True
            cursor = right + timedelta(days=1)
    return False


def apply_event(rows, event, reverse=False):
    state = by_identity(rows)
    remove = event.get('added' if reverse else 'removed', [])
    add = event.get('removed' if reverse else 'added', [])
    updates = [(u['after'], u['before']) if reverse else (u['before'], u['after'])
               for u in event.get('updated', [])]
    touched = [r['company_identity'] for r in remove] + [before['company_identity'] for before, _ in updates]
    if len(touched) != len(set(touched)):
        raise ValueError('Conflicting event operations')
    for record in remove:
        if state.get(record['company_identity']) != record:
            raise ValueError('Removal does not match the prior membership')
        del state[record['company_identity']]
    for before, after in updates:
        if before['company_identity'] != after['company_identity']:
            raise ValueError('A rename cannot merge different company identities')
        if state.get(before['company_identity']) != before:
            raise ValueError('Rename does not match the prior member')
        state[before['company_identity']] = after
    for record in add:
        if record['company_identity'] in state:
            raise ValueError('Addition already belongs to the index')
        state[record['company_identity']] = record
    return members(state.values())


def resolve(ledger, requested):
    iso(requested)
    events = validate_ledger(ledger)
    anchor = ledger['anchor']
    start, end = sorted([requested, anchor['date']])
    if not covered(ledger['coverage'], start, end):
        raise ValueError('Unreviewed membership-history coverage between requested date and anchor')
    selected = [e for e in events if start < e['effective_date'] <= end]
    reverse = requested < anchor['date']
    rows = members(anchor['members'])
    applied = list(reversed(selected)) if reverse else selected
    for event in applied:
        rows = apply_event(rows, event, reverse)
    ids = set(anchor['source_ids'])
    for event in applied:
        ids.update(event['source_ids'])
    for span in ledger['coverage']:
        if span['from'] <= end and span['through'] >= start:
            ids.update(span['source_ids'])
    return {'date': requested, 'policy': POLICY, 'method': 'reviewed_event_reconstruction',
            'members': rows, 'anchor_date': anchor['date'],
            'event_ids': [e['id'] for e in applied], 'source_ids': sorted(ids),
            'knowledge_basis': 'retrospective; not a point-in-time knowledge backtest'}


def read_workbook(path):
    result = {}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Damaged source workbook')
        strings = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in archive.namelist() else []
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(archive.read('xl/workbook.xml')).find('s:sheets', NS):
            target = rels[sheet.attrib['{' + NS['r'] + '}id']]
            filename = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            rows = []
            for row in ET.fromstring(archive.read(filename)).find('s:sheetData', NS):
                values = {}
                for cell in row:
                    if cell.find('s:f', NS) is not None:
                        raise ValueError('Evidence workbook must contain saved literal values')
                    value = cell.findtext('s:v', '', NS)
                    if cell.attrib.get('t') == 's':
                        value = strings[int(value)] if value else ''
                    elif cell.attrib.get('t') == 'inlineStr':
                        node = cell.find('s:is', NS)
                        value = ''.join(node.itertext()) if node is not None else ''
                    values[re.match('[A-Z]+', cell.attrib['r']).group()] = value
                if any(values.values()):
                    rows.append(values)
            result[sheet.attrib['name']] = rows
    return result


def split(value):
    return {v.strip() for v in value.split(';') if v.strip()}


def import_workbook(path, expected_sha256):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError('Workbook hash differs from the approved source')
    sheets = read_workbook(path)
    sources = [{'id': r['A'], 'url': r['B'], 'title': r['C'], 'published_date': r.get('D', ''),
                'retrieved_at_utc': r['E'], 'sha256': r['F']} for r in sheets['Sources'][1:]]
    registry = {s['id']: s for s in sources}
    periods = []
    for r in sheets['Periods'][1:]:
        start, end, tab = r['B'], r['C'], r['A']
        if tab != start + '_' + end or iso(start) > iso(end) or r['D'] != '50':
            raise ValueError('Invalid period boundaries')
        rows = []
        for item in sheets[tab][1:]:
            if item['D'] != start or item['E'] != end:
                raise ValueError('Member dates disagree with the period')
            symbol = item['C']
            rows.append({'company_identity': ALIASES.get(symbol, symbol),
                         'company_name': item['B'], 'nse_symbol': symbol})
        periods.append({'from': start, 'through': end, 'members': members(rows), 'source_id': r['H'],
                        'added': {ALIASES.get(s, s) for s in split(r.get('F', ''))},
                        'removed': {ALIASES.get(s, s) for s in split(r.get('G', ''))}})
    periods.sort(key=lambda p: p['from'])
    period_tabs = {n for n in sheets if re.fullmatch(r'\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', n)}
    if len(periods) != len(period_tabs) or {p['from'] + '_' + p['through'] for p in periods} != period_tabs:
        raise ValueError('Period index does not match the member tabs')
    events = []
    raw = sheets['Change Ledger'][1:]
    if len(raw) != len(periods) - 1 or len({r['A'] for r in raw}) != len(raw):
        raise ValueError('Change ledger must cover every unique transition')
    for previous, current in zip(periods, periods[1:]):
        if iso(previous['through']) + timedelta(days=1) != iso(current['from']):
            raise ValueError('Membership period gap or overlap')
        before, after = by_identity(previous['members']), by_identity(current['members'])
        removed, added = before.keys() - after.keys(), after.keys() - before.keys()
        if set(removed) != current['removed'] or set(added) != current['added']:
            raise ValueError('Period delta disagrees with the approved ledger')
        evidence = [r for r in raw if r['A'] == current['from']]
        if len(evidence) != 1 or evidence[0]['F'] != current['source_id']:
            raise ValueError('Missing matching dated change evidence')
        excluded_symbols = set()
        for exception in sheets['Exceptions'][1:]:
            if exception.get('B') and exception['A'] <= current['from'] and exception['B'] >= previous['through']:
                excluded_symbols.update(s.strip() for s in re.split(r'[;/]', exception.get('D', '')) if s.strip())
        raw_removed = {ALIASES.get(s, s) for s in split(evidence[0].get('D', '')) - excluded_symbols}
        raw_added = {ALIASES.get(s, s) for s in split(evidence[0].get('E', '')) - excluded_symbols}
        if raw_removed != set(removed) or raw_added != set(added):
            raise ValueError('Raw security delta disagrees with core-company changes and explicit exceptions')
        event = {'id': 'workbook-' + current['from'], 'effective_date': current['from'],
                 'published_date': evidence[0].get('B', ''), 'source_ids': [current['source_id']],
                 'removed': [before[k] for k in sorted(removed)], 'added': [after[k] for k in sorted(added)],
                 'updated': [{'before': before[k], 'after': after[k]} for k in sorted(before.keys() & after.keys()) if before[k] != after[k]],
                 'supersedes': [], 'normalization': 'approved workbook core-company transition',
                 'raw_change': evidence[0]}
        # The workbook has already resolved accelerated/corrected notices. Keep
        # raw security deltas and all six exceptions instead of reapplying them.
        events.append(event)
    anchor_rows = members([{'company_identity': ALIASES.get(r['C'], r['C']),
                            'company_name': r['A'], 'nse_symbol': r['C']} for r in sheets['Current Anchor'][1:]])
    if anchor_rows != periods[-1]['members']:
        raise ValueError('Final period differs from the independent current anchor tab')
    anchor_source = registry['S003']
    # The CSV was retrieved near midnight UTC: observation dates use India time.
    from datetime import datetime, timezone
    observed = datetime.fromisoformat(anchor_source['retrieved_at_utc']).astimezone(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    if observed != periods[-1]['through']:
        raise ValueError('Anchor observation date differs from coverage endpoint')
    ledger = {'schema_version': 1, 'policy': POLICY, 'source_workbook_sha256': digest,
              'sources': sources, 'anchor': {'date': observed, 'members': anchor_rows, 'source_ids': ['S003']},
              'coverage': [{'from': periods[0]['from'], 'through': periods[-1]['through'], 'reviewed': True,
                            'source_ids': sorted(registry),
                            'basis': 'Inherited approved workbook reconstruction; not a fresh audit of the original PDFs'}],
              'events': events, 'exceptions': sheets['Exceptions'][1:], 'planning_aliases': ALIASES,
              'limitations': ['Core 50 companies differs from the temporary official security basket.',
                             'Opening membership is reconstructed; no independent 2016 full-list anchor.',
                             'Source hashes are workbook manifest records; original PDFs are not rehashed by this import.',
                             'No historical ISIN backfill. No unreviewed extension beyond the stated coverage.']}
    validate_ledger(ledger)
    for period in periods:
        for boundary in ('from', 'through'):
            if resolve(ledger, period[boundary])['members'] != period['members']:
                raise ValueError('Reverse reconstruction differs from approved period')
    state = periods[0]['members']
    for event in active_events(events):
        state = apply_event(state, event)
    if state != anchor_rows:
        raise ValueError('Forward replay did not return to the anchor')
    return ledger


def verify_targets(ledger, targets, first='2016-12-31', last='2026-09-30'):
    iso(first); iso(last)
    quarters = [f'{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}'
                for y in range(int(first[:4]), int(last[:4]) + 1) for m in (3, 6, 9, 12)
                if first <= f'{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}' <= last]
    differences = []
    identities = set()
    row_count = 0
    for quarter in quarters:
        actual = resolve(ledger, quarter)['members']
        expected = [r for r in targets if r['quarter_end'] == quarter]
        a = {(r['company_identity'], r['nse_symbol']) for r in actual}
        e = {(r['company_identity'], r['nse_symbol']) for r in expected}
        if len(expected) != 50 or len(e) != 50 or a != e:
            differences.append({'quarter_end': quarter, 'expected_rows': len(expected),
                                'only_reconstructed': sorted(a - e), 'only_existing': sorted(e - a)})
        identities.update(r['company_identity'] for r in actual)
        row_count += len(actual)
    return {'ok': not differences, 'quarters': len(quarters), 'company_quarters': row_count,
            'identities': len(identities), 'first_quarter': first, 'last_quarter': last,
            'coverage': ledger['coverage'], 'events': len(ledger['events']), 'differences': differences,
            'limitations': ledger.get('limitations', [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import-workbook')
    imp.add_argument('workbook', type=Path)
    imp.add_argument('--sha256', required=True)
    imp.add_argument('--out', type=Path, required=True)
    query = sub.add_parser('resolve')
    query.add_argument('ledger', type=Path)
    query.add_argument('date')
    check = sub.add_parser('verify-targets')
    check.add_argument('ledger', type=Path)
    check.add_argument('targets', type=Path)
    check.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'import-workbook':
        ledger = import_workbook(args.workbook, args.sha256)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(ledger, indent=2), encoding='utf-8')
        print(json.dumps({'ledger': str(args.out.resolve()), 'events': len(ledger['events']),
                          'coverage_through': ledger['anchor']['date']}))
    elif args.command == 'resolve':
        print(json.dumps(resolve(json.loads(args.ledger.read_text(encoding='utf-8')), args.date), indent=2))
    else:
        report = verify_targets(json.loads(args.ledger.read_text(encoding='utf-8')),
                                json.loads(args.targets.read_text(encoding='utf-8')))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
        if not report['ok']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
