"""Single-host experiment controller. Campaign owns every search/offline loop."""
import asyncio
import os
from pathlib import Path
import shutil
import signal
import time
from dream_rsi.errors import AgentStopped
from .config import validate
from .io import read,write,lock
from .ledger import Ledger
from .resources import ResourcePool
from .provider import ModelService
from .backend import assemble
from .grading import grade
from . import tasks,release,runtime


class Control:
    def __init__(self,root):self.root=Path(root)
    async def check(self,point):
        action=read(self.root/'control.json')['action']
        if action in ('pause','stop'):raise AgentStopped(action)
        value=read(self.root/'config.json')
        deadline=value.get('deadline_at')
        if deadline is not None and time.time()>=deadline:raise AgentStopped('deadline')


def control(root,action):
    if action not in ('run','pause','stop'):raise ValueError('Unknown action')
    root=Path(root)
    if not (root/'config.json').exists():raise ValueError('Not an initialized run')
    write(root/'control.json',{'action':action,'updated_at':time.time()})


def initialize(root,spec):
    root=Path(root).resolve();spec=validate(spec)
    root.mkdir(parents=True,exist_ok=False)
    write(root/'config.json',spec)
    control(root,'stop')
    if spec['mode']=='real':
        for task in spec['tasks']:
            if task['id'] not in spec.get('excluded_tasks',[]):tasks.prepare(root,task)
        destination=root/'runtime';destination.mkdir()
        shutil.copyfile(runtime.__file__,destination/'mle_runtime.py')
        write(destination/'inventory.json',spec['host'].get('runtime_inventory',{}))
        # Only new, run-owned inputs are made readable for mapped container UID.
        from .execute import readable
        for name in ('runtime','prepared'):
            for path in (root/name).rglob('*'):readable(path)
            readable(root/name)
    ledger=Ledger(root,spec.get('budget',{}));ledger.close()
    release.freeze(root,spec)
    write(root/'ready.json',{'schema':1,'initialized_at':time.time()})
    return root


async def run(root):
    root=Path(root).resolve()
    if not (root/'ready.json').exists():raise ValueError('Initialization did not complete')
    spec=validate(read(root/'config.json'))
    release.verify(root,spec)
    if spec['mode']=='real' and not set(spec['host']['cpus'])<=os.sched_getaffinity(0):
        raise ValueError('Configured CPUs unavailable')
    with lock(str(root/'controller')):
        ledger=Ledger(root,spec.get('budget',{}))
        pool=ResourcePool(spec['host'],mock=spec['mode']=='mock')
        gate=asyncio.Semaphore(spec['api_parallelism']);ctl=Control(root)
        control(root,'run')
        loop=asyncio.get_running_loop()
        installed=[]
        for sig in (signal.SIGINT,signal.SIGTERM):
            try:loop.add_signal_handler(sig,control,root,'stop');installed.append(sig)
            except (NotImplementedError,RuntimeError,ValueError):pass
        async def task_loop(task):
            path=root/'status'/(task['id']+'.json')
            state={'task':task['id'],'state':'starting'}
            if task['id'] in spec.get('excluded_tasks',[]):
                write(path,{**state,'state':'excluded'});return
            if path.exists() and read(path).get('state')=='retired':return
            service=None
            if spec['mode']=='real':
                service=ModelService(root,task['id'],ledger,{**spec['provider'],'model':spec['model']},gate,ctl)
            campaign=None
            try:
                await ctl.check('task_start')
                campaign=assemble(root,task,spec,ledger,pool,service,ctl)
                await campaign.bootstrap()
                completed=campaign.summary()['completed_rounds']
                # A crash between search completion and private grading must finish
                # that exact selected result before admitting another round.
                if completed:
                    selected=campaign.best_result()
                    official=await grade(root,task,completed,selected,spec,pool,ledger)
                    state.update(official=official,selected=selected)
                    if official['any_medal']:
                        write(path,{**state,'state':'retired','summary':campaign.summary()});return
                for number in range(completed+1,spec['rounds']+1):
                    await ctl.check('round_start')
                    write(path,{**state,'state':'searching','round':number})
                    await campaign.run_round(number)
                    selected=campaign.best_result()
                    write(path,{**state,'state':'grading','selected':selected,'summary':campaign.summary()})
                    official=await grade(root,task,number,selected,spec,pool,ledger)
                    state.update(selected=selected,official=official,summary=campaign.summary())
                    if official['any_medal']:
                        write(path,{**state,'state':'retired'});return
                write(path,{**state,'state':'completed','summary':campaign.summary()})
            except AgentStopped as exc:
                write(path,{**state,'state':'stopped','reason':str(exc),
                    'summary':campaign.summary() if campaign else None})
            except Exception as exc:
                write(path,{**state,'state':'blocked','error':f'{type(exc).__name__}: {exc}',
                    'summary':campaign.summary() if campaign else None})
            finally:
                if campaign:campaign.close()
        try:
            await asyncio.gather(*(task_loop(t) for t in spec['tasks']))
        finally:
            for sig in installed:loop.remove_signal_handler(sig)
            ledger.close()
        blocked=[p.stem for p in (root/'status').glob('*.json') if read(p)['state']=='blocked']
        if blocked:raise RuntimeError('Blocked tasks: '+', '.join(blocked))
