"""Offline integration controller. No network, remote writes or new scheduling.

Bootstrap imports verified local source snapshots. Stage builds a website-compatible
candidate and coverage/classification reports; it never changes the live Site.
"""
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
from contextlib import contextmanager
import argparse, calendar, csv, hashlib, io, json, os, sqlite3, sys, uuid

ROOT=Path(__file__).resolve().parent
D=lambda x:Decimal(str(x))
class PipelineError(ValueError):pass

def now():return datetime.now(timezone.utc).isoformat()
def digest(body):return hashlib.sha256(body).hexdigest()
def encoded(obj):return (json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
def read(file):return json.loads(Path(file).read_text(encoding='utf-8-sig'))
def atomic(file,body):
    file=Path(file);file.parent.mkdir(parents=True,exist_ok=True);tmp=file.with_name(file.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        os.replace(tmp,file)
    finally:
        if tmp.exists():tmp.unlink()
def write(file,obj):atomic(file,encoded(obj))
def input_path(root,relative):
    base=(root/'data/inputs').resolve();file=(base/relative).resolve()
    if not file.is_relative_to(base):raise PipelineError('Input path escapes the pipeline input folder.')
    return file
def csv_bytes(headers,rows):
    text=io.StringIO(newline='');w=csv.writer(text);w.writerow(headers);w.writerows(rows);return ('\ufeff'+text.getvalue()).encode('utf-8')
@contextmanager
def lock(root):
    file=root/'data/pipeline.lock';file.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(file,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise PipelineError('Another integration run owns pipeline.lock; no overlapping writes allowed.')
    try:
        os.write(fd,encoded({'pid':os.getpid(),'started_at_utc':now()}));os.close(fd);yield
    finally:file.unlink(missing_ok=True)

def archive(root,relative,body):
    target=root/'data/inputs'/relative;original=root/'data/input-history'/digest(body)
    if not original.exists():atomic(original,body)
    if not target.exists() or target.read_bytes()!=body:atomic(target,body)
    return {'file':relative.as_posix(),'sha256':digest(body),'bytes':len(body)}

def bootstrap(root,project):
    """Import only company snapshots and official Nifty prices; no cash-activity files."""
    cfg=read(root/'pipeline.json');project=project.resolve();base=project/'outputs/company-workbooks';entries=[]
    with lock(root):
        for file in sorted((base/'code/data').glob('*.json')):
            body=file.read_bytes();d=json.loads(body);proof=read(base/'code/built'/(d['identity']+'.verification.json'))
            if digest(body)!=proof['source_input_sha256']:raise PipelineError('Prepared company input hash mismatch: '+d['identity'])
            workbook=base/(d['identity']+'_Ownership_Free_Float.xlsx')
            if digest(workbook.read_bytes())!=proof['sha256']:raise PipelineError('Prior workbook hash mismatch: '+d['identity'])
            entries.append(archive(root,Path('companies')/file.name,body))
        if not entries:raise PipelineError('No verified company inputs found.')
        raw=project/'outputs/fii-dii-daily/originals';meta={}
        for line in (raw.parent/'downloads.jsonl').read_text(encoding='utf-8').splitlines():
            r=json.loads(line)
            if r['status']==200:meta[r['file']]=r
        prices=[]
        for file in sorted(raw.glob('calendar-????-Q?.json')):
            body=file.read_bytes();m=meta['originals/'+file.name]
            if digest(body)!=m['sha256']:raise PipelineError('Official price input hash mismatch: '+file.name)
            e=archive(root,Path('prices')/file.name,body);e['url']=m['url'];prices.append(e)
        extra=raw/'nse-index-gap-2024-03-02.csv';body=extra.read_bytes();m=meta['originals/'+extra.name]
        if digest(body)!=m['sha256']:raise PipelineError('Supplementary price input hash mismatch.')
        e=archive(root,Path('prices')/extra.name,body);e['url']=m['url'];prices.append(e)
        reference=read(project/'outputs/nifty50-ownership-site/dist/data.json')
        groups=archive(root,Path('groups.json'),encoded(reference['groups']))
        manifest={'schema_version':1,'companies':entries,'prices':prices,'groups':groups,'cash_market_fii_dii_included':False}
        write(root/'data/inputs/manifest.json',manifest)
        # Check prepared snapshots against the current source DB without changing it.
        stale=[];database=project/'outputs/bse-system/data/shareholding.sqlite'
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as c:
            latest={(r[0],r[1]):r[2] for r in c.execute('select company_identity,quarter_end,sha256 from latest_filings')}
        for entry in entries:
            d=read(root/'data/inputs'/entry['file'])
            for q in d['quarters']:
                key=(d['identity'],q['q']);source=latest.get(key)
                if source and q.get('sha256')!=source:stale.append({'company':key[0],'quarter':key[1],'reason':'Source filing changed after prepared input was built'})
        report={'at_utc':now(),'company_inputs':len(entries),'official_price_files':len(prices),'cash_activity_files_copied':0,'stale_company_quarters':stale,'source_project':str(project),'live_collection_executed':False}
        write(root/'data/bootstrap-report.json',report)
    return report

def load_inputs(root):
    manifest=read(root/'data/inputs/manifest.json');records=[]
    if manifest.get('cash_market_fii_dii_included') is not False:raise PipelineError('Excluded cash-activity branch is not disabled.')
    for entry in [*manifest['companies'],*manifest['prices'],manifest['groups']]:
        body=input_path(root,entry['file']).read_bytes()
        if digest(body)!=entry['sha256']:raise PipelineError('Input hash mismatch: '+entry['file'])
    for entry in manifest['companies']:records.append(read(input_path(root,entry['file'])))
    return manifest,records,read(input_path(root,manifest['groups']['file']))

def prices_from_files(root,manifest):
    observations={};calendar_count=0
    for entry in manifest['prices']:
        file=input_path(root,entry['file'])
        if file.suffix=='.json':
            rows=read(file)['data'];calendar_count+=1
            if len(rows)>=75:raise PipelineError('Possibly truncated price-source response: '+file.name)
            pairs=[(datetime.strptime(r['EOD_TIMESTAMP'],'%d-%b-%Y').date().isoformat(),r['EOD_CLOSE_INDEX_VAL']) for r in rows if r['EOD_INDEX_NAME']=='NIFTY 50']
        else:
            with file.open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if r['Index Name']=='Nifty 50']
            pairs=[(datetime.strptime(r['Index Date'],'%d-%m-%Y').date().isoformat(),r['Closing Index Value']) for r in rows]
        for day,close in pairs:
            v=D(close)
            if not v.is_finite() or v<=0:raise PipelineError('Invalid index close: '+day)
            if day in observations and observations[day]!=v:raise PipelineError('Conflicting index closes: '+day)
            observations[day]=v
    if not calendar_count or not observations:raise PipelineError('No complete official index-price inputs.')
    return [{'date':day,'close':float(close)} for day,close in sorted(observations.items())]

def classification_review(company,row):
    if row['group']!=7 or D(row['fully'])<=0:return None
    name=' '.join(row['name'].upper().split());code=None;target=None
    if name in {'FII','FIIS','FOREIGN INSTITUTIONAL INVESTOR','FOREIGN INSTITUTIONAL INVESTORS'}:code='EXPLICIT_FII_IN_OTHER_INSTITUTIONS';target=6
    elif name in {'UTI','UNIT TRUST OF INDIA'}:code='UTI_ENTITY_IDENTITY_REQUIRES_REVIEW'
    if not code:return None
    return {'company':company,'quarter':row['quarter'],'name':row['name'],'source_member':row['member'],'ordinary_shares':row['fully'],'current_group':7,'proposed_group':target,'current_float_eligibility':row['eligibility'],'issue':code,'source_url':row['url'],'action':'Review before changing the approved classification policy'}

def aggregate(records,cfg):
    byq=defaultdict(list);seen=set();review=[];identities=set()
    for company in records:
        if company['identity'] in identities:raise PipelineError('Duplicate company input: '+company['identity'])
        identities.add(company['identity']);cats=defaultdict(list)
        for row in company['rows']:
            if row['group'] not in range(1,13):raise PipelineError('Unapproved ownership-group ID.')
            cats[row['quarter']].append(row);issue=classification_review(company['identity'],row)
            if issue:review.append(issue)
        for q in company['quarters']:
            day=q['q'];y,m,dd=map(int,day.split('-'))
            if m not in (3,6,9,12) or dd!=calendar.monthrange(y,m)[1]:raise PipelineError('Not a calendar quarter end: '+day)
            if day<cfg['scope']['first_quarter_end']:continue
            key=(company['identity'],day)
            if key in seen:raise PipelineError('Duplicate company-quarter: '+str(key))
            seen.add(key);cap=None;values=None;reason='Source filing or controls unavailable'
            if q.get('controls_ok'):
                n=D(q['fully']);groups=[sum((D(r['fully']) for r in cats[day] if r['group']==g),D(0)) for g in range(1,13)]
                if n<=0 or any(v<0 for v in groups) or sum(groups)!=n:raise PipelineError('Company ownership shares do not reconcile: '+str(key))
                quote=q.get('quote')
                if quote and D(quote['close'])>0:
                    price=D(quote['close']);cap=n*price/D(10000000);values=[s*price/D(10000000) for s in groups];reason=''
                    if abs(sum(values)-cap)>D('0.00000001'):raise PipelineError('Company values do not reconcile: '+str(key))
                else:reason='Historical price not safely matched'
            byq[day].append({'identity':company['identity'],'cap':cap,'values':values,'reason':reason})
    quarters=[];coverage=[]
    for day,items in sorted(byq.items()):
        expected=cfg['scope']['expected_members_per_quarter']
        if len(items)!=expected:raise PipelineError(f'Membership coverage must contain {expected} unique companies: {day} has {len(items)}')
        good=[r for r in items if r['cap'] is not None];total=sum((r['cap'] for r in good),D(0))
        if total<=0:raise PipelineError('No usable market caps for quarter '+day)
        percentages=[sum((r['values'][g] for r in good),D(0))/total*100 for g in range(12)]
        if abs(sum(percentages)-100)>D('1e-20'):raise PipelineError('Aggregate percentages do not total 100: '+day)
        missing=[r['identity'] for r in items if r['cap'] is None]
        quarters.append({'date':day,'label':f'Q{int(day[5:7])//3}:{day[2:4]}','values':[float(v) for v in percentages],'used':len(good),'expected':expected,'estimated':bool(missing),'excluded':missing})
        coverage.append({'quarter':day,'expected':expected,'included':len(good),'covered_market_cap_crore':float(total),'excluded':missing,'status':'rescaled_estimate' if missing else 'complete'})
    if not quarters:raise PipelineError('No membership quarters found.')
    return quarters,coverage,review

def stage(root):
    cfg=read(root/'pipeline.json')
    if cfg['scope'].get('cash_market_fii_dii') is not False:raise PipelineError('FII/DII cash-market scope must remain excluded.')
    with lock(root):
        manifest,records,groups=load_inputs(root);bootstrap_report=read(root/'data/bootstrap-report.json')
        if len(groups)!=cfg['scope']['ownership_groups']:raise PipelineError('Unexpected ownership-group count.')
        if any(d['groups']!=[g['label'] for g in groups] for d in records):raise PipelineError('Company ownership taxonomy does not match the approved display groups.')
        fingerprint=digest(encoded({'controller_sha256':digest(Path(__file__).read_bytes()),'inputs':manifest,'scope':cfg['scope'],'calculation':cfg['calculation'],'stale':bootstrap_report['stale_company_quarters']}))
        latest=root/'data/latest-candidate.json'
        if latest.exists():
            previous=read(latest)
            if previous['input_fingerprint']==fingerprint:
                folder=root/'data/candidates'/previous['candidate_id']
                if all((folder/file).exists() and digest((folder/file).read_bytes())==sha for file,sha in previous['files'].items()):return {**previous,'reused_unchanged_candidate':True}
        quarters,coverage,review=aggregate(records,cfg);prices=prices_from_files(root,manifest)
        candidate_id=fingerprint[:20]+'-'+uuid.uuid4().hex[:8];folder=root/'data/candidates'/candidate_id
        folder.mkdir(parents=True)
        reasons=['Private persistent storage is not connected','Classification policy requires review','Production Excel renderer and authenticated site updater are not connected']
        if not cfg['activation'].get('user_approved'):reasons.insert(0,'Live integration awaits user approval')
        if bootstrap_report['stale_company_quarters']:reasons.append('Prepared company inputs are stale versus the source database')
        candidate={'title':'Nifty Ownership Explorer','asOf':datetime.now(timezone(timedelta(hours=5,minutes=30))).date().isoformat(),'groups':groups,'quarters':quarters,'prices':prices,'sources':{'price':'https://www.nseindia.com/reports-indices-historical-index-data','ownership':'https://www.bseindia.com/corporates/sharehold_searchnew'},'pipeline':{'candidate_id':candidate_id,'status':'review_required_not_published','classification_policy':cfg['calculation']['classification_policy'],'cash_activity_included':False}}
        files={'website-data.json':encoded(candidate),'coverage.json':encoded(coverage),'classification-review.json':encoded(review)}
        files['ownership-percent.csv']=csv_bytes(['quarter_end','companies_used','expected','status',*[g['label']+' (%)' for g in groups],'excluded'],[[q['date'],q['used'],q['expected'],'rescaled_estimate' if q['estimated'] else 'complete',*q['values'],'; '.join(q['excluded'])] for q in quarters])
        columns=['company','quarter','name','source_member','ordinary_shares','current_group','proposed_group','current_float_eligibility','issue','source_url','action']
        files['classification-review.csv']=csv_bytes(columns,[[r[k] for k in columns] for r in review])
        for name,body in files.items():atomic(folder/name,body)
        result={'candidate_id':candidate_id,'input_fingerprint':fingerprint,'created_at_utc':now(),'status':'review_required_not_published','quarters':len(quarters),'ownership_values':len(quarters)*12,'company_quarter_targets':sum(q['expected'] for q in quarters),'usable_company_quarters':sum(q['used'] for q in quarters),'complete_quarters':sum(not q['estimated'] for q in quarters),'rescaled_estimate_quarters':sum(q['estimated'] for q in quarters),'daily_price_rows':len(prices),'earliest_price':prices[0]['date'],'latest_price':prices[-1]['date'],'classification_review_rows':len(review),'classification_issue_counts':dict(Counter(r['issue'] for r in review)),'stale_source_inputs':len(bootstrap_report['stale_company_quarters']),'files':{name:digest(body) for name,body in files.items()},'publication_blocks':reasons,'network_requests':0,'ai_calls':0,'remote_writes':0,'new_schedules_created':0,'excel_files_regenerated':0}
        write(folder/'result.json',result);write(latest,result)
        with (root/'data/runs.jsonl').open('ab') as f:f.write(encoded(result))
        return {**result,'reused_unchanged_candidate':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT);sub=parser.add_subparsers(dest='command',required=True)
    boot=sub.add_parser('bootstrap',help='Import verified local inputs without network access');boot.add_argument('--project',type=Path,required=True)
    sub.add_parser('stage',help='Create a local candidate and reports; never publish');sub.add_parser('status',help='Read the last local candidate status')
    args=parser.parse_args();root=args.root.resolve()
    try:
        result=bootstrap(root,args.project) if args.command=='bootstrap' else stage(root) if args.command=='stage' else read(root/'data/latest-candidate.json')
        print(json.dumps(result,indent=2));return 0
    except (PipelineError,OSError,KeyError,ValueError,ArithmeticError,sqlite3.Error) as e:print(json.dumps({'status':'failed','error':str(e),'live_changes_applied':False}),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
