import pytest
from mle_rsi.controller import initialize,run
from mle_rsi.reporting import status
from mle_rsi.io import read,sha,write


@pytest.mark.asyncio
async def test_two_tasks_exclusion_and_resume(tmp_path,spec):
    spec['tasks']=[{'id':'fixture'},{'id':'second'},{'id':'excluded'}]
    spec['excluded_tasks']=['excluded']
    root=initialize(tmp_path/'run',spec)
    await run(root)
    first=status(root)
    assert first['tasks']['fixture']['state']=='completed'
    assert first['tasks']['second']['state']=='completed'
    assert first['tasks']['excluded']['state']=='excluded'
    assert not (root/'campaigns/excluded').exists()
    assert first['medals']==0 and first['task_count']==3
    assert first['tasks']['fixture']['summary']['valid_candidates']==4
    official=sha(root/'official/fixture/1/result.json')
    await run(root)
    assert sha(root/'official/fixture/1/result.json')==official
    assert status(root)['tasks']['fixture']['summary']['valid_candidates']==4


@pytest.mark.asyncio
async def test_private_medal_retires_without_reentering_agent(tmp_path,spec,monkeypatch):
    spec['rounds']=2
    calls=[]
    async def grade(root,task,number,selected,spec,pool,ledger):
        calls.append(number)
        return {'any_medal':True,'private_sentinel':'must not enter agent'}
    monkeypatch.setattr('mle_rsi.controller.grade',grade)
    root=initialize(tmp_path/'run',spec)
    await run(root)
    assert calls==[1]
    assert status(root)['tasks']['fixture']['state']=='retired'
    for p in (root/'campaigns').rglob('*.json'):
        assert 'private_sentinel' not in p.read_text()
    await run(root)
    assert calls==[1]


@pytest.mark.asyncio
async def test_frozen_config_cannot_change(tmp_path,spec):
    root=initialize(tmp_path/'run',spec)
    value=read(root/'config.json');value['rounds']=9;write(root/'config.json',value)
    with pytest.raises(ValueError,match='Frozen'):await run(root)
