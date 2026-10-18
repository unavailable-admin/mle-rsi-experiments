import asyncio
import os
import sys
import pytest
from mle_rsi.resources import ResourcePool
from mle_rsi.process import run_process


@pytest.mark.asyncio
async def test_cancel_waiter_and_budget_rejection_release_slots(spec):
    pool=ResourcePool(spec['host'],mock=True)
    async with pool.slot('a',2):
        async def waiting():
            async with pool.slot('b',1):pass
        task=asyncio.create_task(waiting());await asyncio.sleep(.02);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert len(pool.waiters)==0
    assert not pool.active and pool.cpus==set(spec['host']['cpus'])
    with pytest.raises(ValueError):
        async with pool.slot('c',1):raise ValueError('budget refused after lease')
    assert not pool.active and pool.cpus==set(spec['host']['cpus'])


@pytest.mark.asyncio
async def test_trusted_process_timeout_cleanup(tmp_path):
    result=await run_process([sys.executable,'-c','import time; time.sleep(20)'],tmp_path,.1)
    assert result['timed_out'] and result['exit_code']!=0


@pytest.mark.asyncio
async def test_host_process_is_accounted_and_not_repeated(tmp_path):
    from mle_rsi.process import run_accounted
    from mle_rsi.ledger import Ledger
    ledger=Ledger(tmp_path,{'total':{'execution_seconds':100}})
    output=tmp_path/'host'
    try:
        args=([sys.executable,'-c','print("one invocation")'],output,2,None,ledger,'score','task','hash')
        first=await run_accounted(*args)
        before=(output/'process.json').read_bytes()
        second=await run_accounted(*args)
        assert first==second and (output/'process.json').read_bytes()==before
        assert ledger.summary()['totals']['execution_seconds']==first['wall_seconds']
    finally:ledger.close()
