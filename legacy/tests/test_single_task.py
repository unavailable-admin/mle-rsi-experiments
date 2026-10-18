from pathlib import Path
from dataclasses import replace
import pytest
from mle_rsi import REPO
from mle_rsi.common import read, write, sha
from mle_rsi.single_task import validate, completed_search, export_checkpoints, render
from dream_rsi.models import DiscoveryNode, EvaluationReport, FailClass
from dream_rsi.seed import make_artifact


def spec():return read(REPO/'configs/spooky-solver-500.json')


def test_protocol_and_partial_search_cannot_grade(tmp_path):
    s=spec();assert validate(s)==500
    with pytest.raises(RuntimeError,match='all online/offline'):completed_search(tmp_path,s)
    write(tmp_path/'search-complete.json',{'candidate_nodes':499,'artifacts':{}})
    with pytest.raises(RuntimeError,match='Incomplete'):completed_search(tmp_path,s)
    s['single_task']['checkpoint_candidates']=[24,500]
    with pytest.raises(ValueError,match='complete rounds'):validate(s)


def test_export_freezes_public_selected_solver_and_submission(tmp_path):
    output=tmp_path/'candidate';output.mkdir();(output/'submission.csv').write_text('id,EAP,HPL,MWS\nx,.3,.3,.4\n')
    artifact=make_artifact('solver','print(1)')
    evaluation=EvaluationReport(True,True,.8,FailClass.OK,'test',metrics={'validation_score':.25},
        diagnostics={'output_dir':str(output),'submission_sha256':sha(output/'submission.csv')})
    node=DiscoveryNode('winner','root',(),1,1,.8,'s',artifact,evaluation)
    class Store:
        def get(self,kind,key):return {'best_node':node} if key=='1' else None
    export_checkpoints(tmp_path,Store(),spec())
    path=tmp_path/'checkpoints/0025'
    choice=read(path/'selected.json')
    assert choice['node_id']=='winner' and choice['metrics']['validation_score']==.25
    assert (path/'solver.py').read_text()=='print(1)'
    assert choice['diagnostics']['output_dir']==str(path.resolve())
    write(tmp_path/'search-complete.json',{'candidate_nodes':500,'artifacts':{'checkpoints/0025/solver.py':sha(path/'solver.py')}})
    completed_search(tmp_path,spec())
    (path/'solver.py').write_text('changed')
    with pytest.raises(RuntimeError,match='changed'):completed_search(tmp_path,spec())


def test_no_valid_solver_is_explicit(tmp_path):
    root=DiscoveryNode('root',None,(),0,0,1e-12,'s',make_artifact('root','# root'),EvaluationReport(False,True,1e-12,FailClass.OK,'x'))
    class Store:
        def get(self,kind,key):return {'best_node':root} if key=='1' else None
    export_checkpoints(tmp_path,Store(),spec())
    assert read(tmp_path/'checkpoints/0025/selected.json') is None


def test_five_wide_repair_is_one_slot():
    from dream_rsi.repair_guard import bounded_repair_decision
    from dream_rsi.models import BatchDecision
    from dream_rsi.tree import BatchValidator
    from scripts.audit_firstbatch_policy import view
    nodes=[dict(node_id='r',parent_id=None,valid=True,fail_class='ok',score=1e-12,stage='root'),
           dict(node_id='bad',parent_id='r',valid=False,fail_class='runtime_error',score=0,stage='draft'),
           dict(node_id='good',parent_id='r',valid=True,fail_class='ok',score=.8,stage='draft')]
    observation=replace(view(nodes,5),max_parallelism=5)
    decision=bounded_repair_decision(BatchDecision(('good',)*5,{}),observation)
    assert decision.parent_ids.count('bad')==1 and len(decision.parent_ids)==5
    assert BatchValidator.fill(decision,observation,True)==decision


def test_long_mle_ancestry_is_bounded_but_parent_source_preserved():
    from dream_rsi.evidence_context import discovery_evidence
    nodes=[]
    for i in range(101):
        nodes.append({'node_id':str(i),'parent_id':str(i-1) if i else None,'depth':i,'created_order':i,
            'child_ids':[],'score':.8,'artifact':{'source':'print(1)','proposal':'x'*400,'mechanism_tags':[]},
            'evaluation':{'valid':True,'fail_class':'ok','diagnostics':{'benchmark':'mle'}}})
    payload={'history':{'live_prefix':nodes},'parent':nodes[-1],'task':{},'stage':'improve',
             'instruction':'improve','node_dir':'/node','files_problem':{}}
    out=discovery_evidence(payload)
    assert len(out['path_from_root']['steps'])==32
    assert out['path_from_root']['total_steps']==100
    assert out['path_from_root']['omitted_steps']==68
    assert out['path_from_root']['steps'][-1]['node_id']=='100'
    assert out['files']['/node/solver.py']=='print(1)'
    assert len(nodes)==101
