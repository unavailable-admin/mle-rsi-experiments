from mle_rsi.repair_policy import repair_best_first_source
from dream_rsi.evidence_context import diagnosis, recent_failures, reference_sources


def test_repair_consumes_one_existing_slot_once_per_failed_node():
    ns={};exec(repair_best_first_source(),ns);policy=ns['Policy']()
    nodes=[{'node_id':'r','parent_id':None,'valid':True,'fail_class':'ok','score':1e-12},
           {'node_id':'n1','parent_id':'r','valid':False,'fail_class':'runtime_error','score':0},
           {'node_id':'n2','parent_id':'r','valid':True,'fail_class':'ok','score':.8}]
    view={'revealed_nodes':nodes,'eligible_parent_ids':['r','n1','n2'],'remaining_nodes':2,'remaining_rounds':1,'max_parallelism':2}
    assert policy.decide(view,{})['parent_ids']==['n1','n2']
    nodes.append({'node_id':'n3','parent_id':'n1','valid':True,'fail_class':'ok','score':.9})
    view['eligible_parent_ids'].append('n3')
    assert policy.decide(view,{})['parent_ids']==['n3','n3']


def test_mle_failure_context_contains_actual_exception_and_source():
    error='Traceback\n'+('line\n'*50)+'ValueError: least populated class has only 1 member'
    node={'node_id':'n1','parent_id':'r','score':0,'created_order':1,'artifact':{'source':'broken()','mechanism_tags':[]},
          'evaluation':{'valid':False,'fail_class':'runtime_error','error':error,'diagnostics':{'benchmark':'mle','repairable':True,'stdout_tail':error}}}
    root={'node_id':'r','parent_id':None,'score':1e-12,'artifact':{'source':'','mechanism_tags':[]},'evaluation':{'valid':True,'fail_class':'ok'}}
    assert 'only 1 member' in recent_failures([node])[0]['cause']
    assert 'only 1 member' in diagnosis(node,{'r':root})['failure']['error']
    refs=reference_sources([root,node],root,{'r'},program='/solver.py')
    assert refs[0]['source']=='broken()' and 'repair' in refs[0]['why']
