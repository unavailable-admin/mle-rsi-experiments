import pytest
from mle_rsi import REPO
from mle_rsi.common import read, write, request_namespace
from mle_rsi.experiment import initialize
from mle_rsi.rounds import run
from mle_rsi.restart_task import prepare
from dream_rsi.storage import CampaignStore


def test_reset_namespace_does_not_change_other_task_ids():
    spec = {'stage2': {'task_namespaces': {'cactus': 'cactus-reset1'}}}
    assert request_namespace(spec, 'cactus') == 'cactus-reset1'
    assert request_namespace(spec, 'dog') == 'stage2'
    assert request_namespace({}, 'dog') == ''
    spec['stage2']['task_namespaces']['cactus'] = '../bad'
    with pytest.raises(ValueError): request_namespace(spec, 'cactus')


@pytest.mark.asyncio
async def test_fork_resets_only_one_task_and_preserves_budget_and_resume(tmp_path, monkeypatch):
    tasks = ['leaf-classification', 'spooky-author-identification']
    spec = read(REPO/'configs/lite22-rounds-half.json')
    spec['experiment_tasks'] = spec['development_tasks'] = tasks
    spec['stage2'] = {'version': 1}
    source = tmp_path/'source'
    initialize(source, spec, 'mock')
    from mle_rsi.common import sha
    write(source/'stage2-lineage.json', {'tasks': {t: {'protocol_hash': None} for t in tasks}, 'historical_unsettled': {}, 'increment': {'api_usd_total': 0}})
    frozen = read(source/'frozen-config.json')
    frozen['stage2_lineage_sha256'] = sha(source/'stage2-lineage.json')
    write(source/'frozen-config.json', frozen)
    (source/'runtime').mkdir(); (source/'prepared').mkdir()
    await run(source, through_round=1)
    from dream_rsi.campaign import Campaign
    from mle_rsi.rounds import control
    run_round = Campaign.run_round
    async def stop_after_batch(self, number):
        after_batch = self.scheduler.after_batch
        async def drain():
            await after_batch()
            control(source, 'stop')
        self.scheduler.after_batch = drain
        return await run_round(self, number)
    with monkeypatch.context() as patch:
        patch.setattr(Campaign, 'run_round', stop_after_batch)
        await run(source, through_round=2)
    source_counts = {}
    for t in tasks:
        store = CampaignStore(source/'campaigns'/f'dev-{t}', read_only=True)
        source_counts[t] = len(store.list('node'))
        store.close()
    assert 25 < source_counts[tasks[1]] < 50
    # Artificial settled ledger entry proves the fork cannot grant fresh budget.
    ledger = {'api': {'old': {'task': tasks[0], 'state': 'completed', 'charged_usd': 2, 'reserved_usd': 3}}, 'execution': {}}
    write(source/'budget.json', ledger)
    destination = prepare(source, tmp_path/'next', tasks[0], tmp_path/'validation', 'reset1')
    assert read(destination/'budget.json') == ledger
    assert read(destination/'frozen-config.json')['spec']['budget'] == spec['budget']
    for t, count in zip(tasks, [0, source_counts[tasks[1]]]):
        store = CampaignStore(destination/'campaigns'/f'dev-{t}', read_only=True)
        assert len(store.list('node')) == count
        store.close()
    write(destination/'control.json', {'action': 'resume'})
    await run(destination, through_round=2)
    for t in tasks:
        store = CampaignStore(destination/'campaigns'/f'dev-{t}', read_only=True)
        assert len(store.list('node')) == 50
        store.close()
        store = CampaignStore(source/'campaigns'/f'dev-{t}', read_only=True)
        assert len(store.list('node')) == source_counts[t]
        store.close()
    assert read(source/'budget.json') == ledger
