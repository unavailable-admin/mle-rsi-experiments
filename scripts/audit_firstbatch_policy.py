"""Probe a saved policy in the framework sandbox, without API calls or training."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'third_party/dream-rsi/my_rsi/src'))
from dream_rsi.config import ResourceLimits, PolicyLimits
from dream_rsi.models import PolicyView
from dream_rsi.policy import BranchSignalBuilder
from dream_rsi.policy_runtime import PolicyWorker


def view(nodes,remaining=2):
    nodes=[{**n,'child_ids':[child['node_id'] for child in nodes if child['parent_id']==n['node_id']],
        'created_order':i,'depth':0 if i==0 else (2 if n['parent_id']=='n1' else 1),
        'evaluated':True,'error':None if n['valid'] else 'RuntimeError: synthetic probe',
        'proposal':'','source':'','mechanism_tags':[],
        'diagnostics':{'valid':n['valid'],'score':n['score'],'fail_class':n['fail_class']}}
        for i,n in enumerate(nodes)]
    return PolicyView(root=nodes[0],revealed_nodes=tuple(nodes),
        eligible_parent_ids=tuple(n['node_id'] for n in nodes),
        branch_signals=tuple(BranchSignalBuilder().build(nodes,(),1,include_internal=True)),transition_feedback=(),
        round_index=1,remaining_rounds=1,max_parallelism=2,parent_repeats_allowed=True,remaining_nodes=remaining)


async def audit(source,guard=False):
    nodes=[{'node_id':'r','parent_id':None,'valid':True,'fail_class':'ok','score':1e-12,'stage':'draft','child_ids':['n1','n2']},
        {'node_id':'n1','parent_id':'r','valid':False,'fail_class':'runtime_error','score':0,'stage':'draft','child_ids':[]},
        {'node_id':'n2','parent_id':'r','valid':True,'fail_class':'ok','score':.8,'stage':'draft','child_ids':[]}]
    repaired=nodes+[{'node_id':'n3','parent_id':'n1','valid':True,'fail_class':'ok','score':.9,'stage':'debug','child_ids':[]}]
    failed_debug=nodes+[{'node_id':'n3','parent_id':'n1','valid':False,'fail_class':'runtime_error','score':0,'stage':'debug','child_ids':[]}]
    cases=[('unexpanded_failure',view(nodes)),('already_repaired',view(repaired)),
        ('failed_debug_child',view(failed_debug)),('budget_exhausted',view(nodes,0))]
    rows={}
    limits=PolicyLimits(wall_time_s=5,memory_bytes=256*1024**2,bounded_repair=True) if guard else ResourceLimits(wall_time_s=5,memory_bytes=256*1024**2)
    async with PolicyWorker(source,limits=limits) as worker:
        for name,observation in cases:
            await worker.reset()
            choice=await worker.decide(observation)
            rows[name]={'parents':list(choice.parent_ids),'reason':choice.reason}
    checks={
        'one_repair_and_one_valid_parent':len(rows['unexpanded_failure']['parents'])==2 and rows['unexpanded_failure']['parents'].count('n1')==1 and any(p in ('r','n2') for p in rows['unexpanded_failure']['parents']),
        'does_not_repeat_repaired_parent':'n1' not in rows['already_repaired']['parents'],
        'does_not_chain_failed_debug':not {'n1','n3'}.intersection(rows['failed_debug_child']['parents']),
        'respects_exhausted_budget':not rows['budget_exhausted']['parents'],
    }
    return {'checked_at':time.time(),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'host_bounded_repair':guard,
        'cases':rows,'checks':checks,'all_probe_checks_passed':all(checks.values()),
        'scope':'Synthetic policy behavior probes, not a universal proof or a new training experiment.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--guard',action='store_true')
    args=parser.parse_args();result=asyncio.run(audit(args.source.read_text(),args.guard))
    result['source_path']=str(args.source.resolve())
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
