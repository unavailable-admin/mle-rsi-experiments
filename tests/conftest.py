import os
import pytest


@pytest.fixture
def spec():
    return {'schema':1,'mode':'mock','tasks':[{'id':'fixture'}],
            'host':{'cpus':list(sorted(os.sched_getaffinity(0)))[:2],'gpus':[]},
            'rounds':1,'batches':2,'batch_size':2,'offline_tier':'baseline',
            'search':{'M':1,'replay_scenarios':1,'scheduling_betas':[.5],'policy_decision_timeout_s':5},
            'budget':{'total':{'api_usd':10,'execution_seconds':10000,'gpu_seconds':10000}}}
