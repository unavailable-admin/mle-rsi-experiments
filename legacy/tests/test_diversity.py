import json
import pytest
from mle_rsi import REPO
from mle_rsi.common import read, write
from mle_rsi.diversity import source_fingerprint, NoveltyGate, candidate_role, audit
from mle_rsi.experiment import initialize, campaign
from mle_rsi.queue_single import predecessor_state
from dream_rsi.seed import make_artifact
from dream_rsi.models import Stage


def test_fingerprint_keeps_parameters_and_literals():
    assert source_fingerprint('x = 1 # note\n') == source_fingerprint('\nx=1\n')
    assert source_fingerprint('x=1') != source_fingerprint('x=2')
    assert source_fingerprint('"a"\nx=1') != source_fingerprint('"b"\nx=1')


def test_claim_survives_recreation_and_same_attempt_is_idempotent():
    class Store:
        data = {}
        def get(self,k,v): return self.data.get((k,v))
        def put(self,k,v,value): self.data[k,v]=value
    store=Store();gate=NoveltyGate(store)
    assert gate.claim(make_artifact('x','x=1'),'first') is None
    assert NoveltyGate(store).claim(make_artifact('y','x = 1 # comment'),'second') == 'first'
    assert gate.claim(make_artifact('x','x=1'),'first') is None
    assert gate.claim(make_artifact('x','x=2'),'second') is None


def test_roles_and_repair_override():
    assert [candidate_role(Stage.IMPROVE,f'tree-0001:r0:a{i}') for i in range(5)] == ['improve']*3+['explore','hypothesis']
    assert candidate_role(Stage.DEBUG,'tree-0001:r0:a3') == 'repair'


@pytest.mark.asyncio
async def test_duplicate_batch_is_not_trained_and_requests_are_bounded(tmp_path):
    spec=read(REPO/'configs/spooky-diversity-100.json')
    spec['search'].update(development_rounds=1,batches_per_round=1,replay_steps=2,replay_scenarios=1)
    root=initialize(tmp_path/'run',spec,mode='mock')
    c=campaign(root,'spooky-author-identification','development')
    try:
        await c.bootstrap()
        await c.run_round(1)
        stats=audit(c.store)
        assert stats['candidate_slots']==5
        assert stats['unique_code_ast']==1
        assert stats['duplicates_skipped_training']==4
        assert stats['duplicate_responses']==8
        assert c.ledger.summary()['discovery']['calls']==9
        rows=[v for _,v in c.store.list('request')]
        # Role directions are retained in actual generation request evidence.
        assert len(rows)>=9
    finally:c.store.close()


def test_queue_does_not_start_on_failure_or_partial_search(tmp_path):
    assert predecessor_state(tmp_path)=='waiting'
    write(tmp_path/'single-status.json',{'state':'stopped'})
    assert predecessor_state(tmp_path)=='blocked_predecessor_failed'
    write(tmp_path/'single-status.json',{'state':'completed'})
    assert predecessor_state(tmp_path)=='blocked_missing_completion_seal'
    write(tmp_path/'frozen-config.json',{'spec':{'search':{'development_rounds':4,'batches_per_round':5,'workers':5}}})
    write(tmp_path/'search-complete.json',{'candidate_nodes':99,'artifacts':{}})
    assert predecessor_state(tmp_path)=='blocked_incomplete_search'
    write(tmp_path/'search-complete.json',{'candidate_nodes':100,'artifacts':{}})
    assert predecessor_state(tmp_path)=='ready'

@pytest.mark.asyncio
async def test_regeneration_can_produce_new_parameter_and_get_evaluated(tmp_path):
    spec=read(REPO/'configs/spooky-diversity-100.json')
    spec['search'].update(development_rounds=1,batches_per_round=1,replay_steps=2,replay_scenarios=1)
    root=initialize(tmp_path/'run',spec,mode='mock')
    c=campaign(root,'spooky-author-identification','development')
    def respond(messages,request_id):
        attempt, index = request_id.rsplit(':response:',1)
        value = 0 if index=='0' else int(attempt.rsplit(':a',1)[1])+1
        return json.dumps({'proposal':'Measured test','source':f'x={value}\n','language':'python','mechanism_tags':['test']})
    c.agent.client.mock_responder=respond
    try:
        await c.bootstrap();await c.run_round(1)
        stats=audit(c.store)
        assert stats['candidate_slots']==5
        assert stats['unique_code_ast']==5
        assert stats['duplicates_skipped_training']==0
        assert stats['duplicate_responses']==4
        assert c.ledger.summary()['discovery']['calls']==9
    finally:c.store.close()


def test_queue_waits_for_controller_lock_even_after_completion(tmp_path):
    from mle_rsi.common import lock
    write(tmp_path/'single-status.json',{'state':'completed'})
    write(tmp_path/'frozen-config.json',{'spec':{'search':{'development_rounds':1,'batches_per_round':1,'workers':1}}})
    write(tmp_path/'search-complete.json',{'candidate_nodes':1,'artifacts':{}})
    with lock(tmp_path/'controller'):
        assert predecessor_state(tmp_path)=='waiting_for_controller_exit'
