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
from .provider import model_service
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
        from .inventory import verify as verify_environment
        inventory=verify_environment(spec['host'],root/'environment-verification')
        for task in spec['tasks']:
            if task['id'] not in spec.get('excluded_tasks',[]):tasks.prepare(root,task)
        destination=root/'runtime';destination.mkdir()
        shutil.copyfile(runtime.__file__,destination/'mle_runtime.py')
        write(destination/'inventory.json',inventory)
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
        pool=ResourcePool(spec['host'],mock=spec['mode']=='mock',status_path=root/'resources.json')
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
                service=model_service(root,task['id'],ledger,{**spec['provider'],'model':spec['model']},gate,ctl)
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
        task_gate = asyncio.Semaphore(spec.get('task_parallelism', len(spec['tasks'])))
        async def scheduled(task):
            async with task_gate:
                await task_loop(task)
        try:
            await asyncio.gather(*(scheduled(t) for t in spec['tasks']))
        finally:
            for sig in installed:loop.remove_signal_handler(sig)
            ledger.close()
        blocked=[p.stem for p in (root/'status').glob('*.json') if read(p)['state']=='blocked']
        if blocked:raise RuntimeError('Blocked tasks: '+', '.join(blocked))


async def refresh(root, refresh_id, task_id=None):
    """Run only Dream-RSI offline work against existing frozen public history."""
    root = Path(root).resolve()
    spec = validate(read(root / 'config.json'))
    release.verify(root, spec)
    selected = [t for t in spec['tasks'] if t['id'] not in spec.get('excluded_tasks', []) and (task_id is None or t['id'] == task_id)]
    if not selected:
        raise ValueError('No eligible task selected')
    with lock(str(root / 'controller')):
        ledger = Ledger(root, spec.get('budget', {}))
        pool = ResourcePool(spec['host'], mock=spec['mode'] == 'mock', status_path=root/'resources.json')
        gate = asyncio.Semaphore(spec['api_parallelism'])
        ctl = Control(root)
        control(root, 'run')
        async def refresh_task(task):
            service = model_service(root, task['id'], ledger, {**spec.get('provider', {}), 'model': spec.get('model', {})}, gate, ctl) if spec['mode'] == 'real' else None
            campaign = assemble(root, task, spec, ledger, pool, service, ctl)
            path = root / 'status' / (task['id'] + '.json')
            prior = read(path) if path.exists() else {'task': task['id']}
            write(path, {**prior, 'state': 'refreshing', 'refresh_id': refresh_id})
            try:
                receipt = await campaign.refresh(refresh_id)
                write(path, {**prior, 'refresh_id': refresh_id, 'refresh_status': receipt['refresh_status'], 'summary': campaign.summary()})
            except BaseException as exc:
                write(path, {**prior, 'state': 'stopped' if isinstance(exc, AgentStopped) else 'blocked',
                             'refresh_id': refresh_id, 'error': str(exc), 'summary': campaign.summary()})
                raise
            finally:
                campaign.close()
        try:
            outcomes = await asyncio.gather(*(refresh_task(t) for t in selected), return_exceptions=True)
            failures = [x for x in outcomes if isinstance(x, BaseException)]
            if failures:
                raise failures[0]
        finally:
            ledger.close()
