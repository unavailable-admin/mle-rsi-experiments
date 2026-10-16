import asyncio
import pytest
from mle_rsi import REPO
from mle_rsi.common import read
from mle_rsi.rounds import validate
from mle_rsi.round_resources import ResourcePool

def test_eight_slot_config():
    s=read(REPO/'configs/lite22-rounds-5090-2.json')
    assert len(validate(s))==22
    assert len(s['execution']['cpu_ids'])==64
    assert s['execution']['gpu_pool']==list(range(8))
    assert s['round_robin']['active_batches']==8
    assert s['model']['request_parallelism']==5

@pytest.mark.asyncio
async def test_eight_slots_overlap_and_exclude(tmp_path):
    pool=ResourcePool(tmp_path,[list(range(i,i+8)) for i in range(0,64,8)],list(range(8)),mock=True)
    entered=asyncio.Event();active=set();peak=0
    async def work(i):
        nonlocal peak
        async with pool.slot(str(i),True) as s:
            assert s['gpu'] not in active
            active.add(s['gpu']);peak=max(peak,len(active))
            if len(active)==8:entered.set()
            await asyncio.wait_for(entered.wait(),5)
            active.remove(s['gpu'])
    await asyncio.gather(*(work(i) for i in range(8)))
    assert peak==8 and not pool.active

def test_low_memory_cuda_context_is_not_free(tmp_path,monkeypatch):
    from types import SimpleNamespace
    def run(args,**kwargs):
        query=next(v for v in args if v.startswith('--query'))
        data={'--query-gpu=index,memory.used':'0, 0\n1, 100\n', '--query-gpu=index,uuid':'0, GPU-free\n1, GPU-busy\n', '--query-compute-apps=gpu_uuid':'GPU-busy\n'}
        return SimpleNamespace(stdout=data[query])
    monkeypatch.setattr('mle_rsi.round_resources.subprocess.run',run)
    pool=ResourcePool(tmp_path,[[0]], [0,1]);pool.probe()
    assert pool.available=={0}
