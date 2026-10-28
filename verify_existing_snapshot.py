"""Compare the staged data against every ownership value and daily price in the existing chart."""
from pathlib import Path
import argparse,json,math,hashlib
import pipeline as p
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);args=parser.parse_args();root=Path(__file__).resolve().parent
    status=p.read(root/'data/latest-candidate.json');folder=root/'data/candidates'/status['candidate_id'];candidate=p.read(folder/'website-data.json');reference=p.read(args.project/'outputs/nifty50-ownership-site/dist/data.json')
    assert candidate['groups']==reference['groups'];old={q['date']:q for q in reference['quarters']};checks=0
    assert len(candidate['quarters'])==len(old)
    for q in candidate['quarters']:
        before=old[q['date']]
        for k in ['label','used','expected','estimated','excluded']:assert q[k]==before[k],(q['date'],k)
        for a,b in zip(q['values'],before['values']):assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-10);checks+=1
        assert math.isclose(sum(q['values']),100,abs_tol=1e-9)
    assert candidate['prices']==reference['prices']
    for file,sha in status['files'].items():assert hashlib.sha256((folder/file).read_bytes()).hexdigest()==sha
    report={'ownership_values_compared':checks,'daily_price_rows_compared':len(candidate['prices']),'all_match_existing_snapshot':True,'candidate_artifact_hashes_verified':len(status['files']),'candidate_id':status['candidate_id'],'network_requests':0,'ai_calls':0,'live_site_changed':False}
    p.write(root/'data/snapshot-comparison.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
