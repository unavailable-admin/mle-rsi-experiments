"""Observe one existing supervisor; never restart a worker or spend API budget."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def snapshot(root):
    state=json.loads((root/'supervisor.json').read_text())
    try:
        os.kill(state['pid'],0)
        live=Path(f'/proc/{state["pid"]}/stat').read_text().split()[2]!='Z'
    except (ProcessLookupError,FileNotFoundError):live=False
    ledger=json.loads((root/'budget.json').read_text()) if (root/'budget.json').exists() else {}
    api=list(ledger.get('api',{}).values());tasks={}
    for c in sorted((root/'campaigns').glob('*')):
        if not c.is_dir():continue
        records=[json.loads(f.read_text()) for f in c.glob('executions/*/assessment.json')]
        valid=[r for r in records if r['valid']]
        best=max(valid,key=lambda r:r['score']) if valid else None
        checkpoint=c/'evidence/checkpoint/active/checkpoint.json'
        cp=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        tasks[c.name]={'attempts':len(records),'valid':len(valid),'best_public':best['metric']['validation_score'] if best else None,
                       'completed_rounds':cp.get('completed_rounds',0),'errors':dict(Counter(r['fail_class'] for r in records if not r['valid']))}
    return {'checked_at':time.time(),'supervisor_live':live,'supervisor':state,'api_requests':len(api),
            'api_charged_usd':sum(r.get('charged_usd',0) for r in api),
            'api_reserved_usd':sum(r.get('reserved_usd',0) for r in api if 'charged_usd' not in r),'tasks':tasks}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--once',action='store_true');args=p.parse_args();previous=None
    while True:
        try:
            value=snapshot(args.root)
            tmp=args.root/'monitor-current.tmp';tmp.write_text(json.dumps(value,indent=2));tmp.replace(args.root/'monitor-current.json')
            signature=json.dumps({k:v for k,v in value.items() if k!='checked_at'},sort_keys=True)
            if signature!=previous:
                row={'live':value['supervisor_live'],'phase':value['supervisor']['phase'],'state':value['supervisor']['state'],
                     'api_usd':round(value['api_charged_usd'],4),'tasks':{k:(v['attempts'],v['valid'],v['completed_rounds']) for k,v in value['tasks'].items()}}
                print(json.dumps(row),flush=True);previous=signature
            terminal=value['supervisor']['state']!='running'
            if terminal and not value['supervisor_live']:
                if (args.root/'policy-freeze.json').exists():
                    subprocess.run([sys.executable,str(Path(__file__).with_name('report_firstbatch.py')),str(args.root)],check=True)
                print('TERMINAL',value['supervisor']['state'],flush=True);break
            if not value['supervisor_live'] and not terminal:
                print('SUPERVISOR MISSING; inspection required, no automatic restart',flush=True);break
            if args.once:break
        except (OSError,ValueError) as exc:
            print('OBSERVATION RETRY',type(exc).__name__,str(exc),flush=True)
            if args.once:raise
        time.sleep(30)
