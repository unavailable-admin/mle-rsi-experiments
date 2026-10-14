"""Controller-side preparation, before any paid generation."""
import os
from pathlib import Path
import shutil
import subprocess

from . import REPO, WORKSPACE
from .common import read, write


def prepare_assets(root, spec, tasks):
    root=Path(root);runtime=root/'runtime';runtime.mkdir(parents=True,exist_ok=True)
    python=str(Path(spec['execution']['python_environment'])/'bin/python')
    cpus=spec['execution'].get('cpu_groups',[spec['execution']['cpu_ids']])[0]
    prefix=['taskset','-c',','.join(map(str,cpus)),python]
    env={**os.environ,'OMP_NUM_THREADS':str(len(cpus)),'OPENBLAS_NUM_THREADS':str(len(cpus)),'MKL_NUM_THREADS':str(len(cpus))}
    shutil.copyfile(REPO/'mle_rsi/candidate_runtime.py',runtime/'mle_runtime.py')
    subprocess.run(prefix+[str(REPO/'mle_rsi/runtime_inventory.py'),str(WORKSPACE),str(runtime/'inventory.json')],env=env,check=True,timeout=120)
    for task in tasks:
        print('PREPARE PUBLIC VALIDATION',task,flush=True)
        subprocess.run(prefix+[str(REPO/'mle_rsi/validation.py'),'prepare',task,
            str(WORKSPACE/'data/mlebench'/task/'prepared/public'),str(root/'prepared'/task)],env=env,check=True,timeout=900)
    write(root/'preparation.json',{'tasks':list(tasks),'public_only':True,'runtime':read(runtime/'inventory.json')})
