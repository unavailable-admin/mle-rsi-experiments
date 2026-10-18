"""One host, shared CPU/GPU pool. Cancelling a waiter never leaks a lease."""
import asyncio
from contextlib import asynccontextmanager, ExitStack
import subprocess
from pathlib import Path
from .io import lock


def siblings(cpu):
    path = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
    if not path.exists(): return {cpu}
    found=set()
    for span in path.read_text().strip().split(','):
        a,_,b=span.partition('-')
        found.update(range(int(a), int(b or a)+1))
    return found


class ResourcePool:
    def __init__(self, host, mock=False, status_path=None):
        self.host, self.mock = host, mock
        self.status_path = status_path
        self.cpus=set(host['cpus']);self.gpus=set(host.get('gpus', []))
        if any(len(siblings(c) & self.cpus)>1 for c in self.cpus):
            raise ValueError('Configure one logical CPU per physical core')
        self.active={};self.waiters=[];self.grants={};self.serial=0

    def publish(self):
        if self.status_path is not None:
            import os
            import time
            from .io import write
            write(self.status_path, {'controller_pid': os.getpid(), 'updated_at': time.time(),
                'configured_cpus': self.host['cpus'], 'configured_gpus': self.host.get('gpus', []),
                'free_cpus': sorted(self.cpus), 'free_gpus': sorted(self.gpus),
                'active': list(self.active.values()), 'waiting': [dict(ticket=w[0], task=w[1], cores=w[2], gpu=w[3]) for w in self.waiters],
                'grants': self.grants})

    def free_gpus(self):
        if self.mock or not self.gpus:return set(self.gpus)
        query=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader,nounits'],
                             check=True,capture_output=True,text=True,timeout=5)
        processes=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid','--format=csv,noheader'],
                             check=True,capture_output=True,text=True,timeout=5)
        busy=set(processes.stdout.splitlines())
        return {int(i) for i,u,m in (row.split(',') for row in query.stdout.splitlines())
                if int(i) in self.gpus and u.strip() not in busy and int(m)<=512}

    @asynccontextmanager
    async def slot(self, task, cores, gpu=False):
        if cores>len(self.host['cpus']) or gpu and not self.host.get('gpus'):
            raise ValueError('Unsatisfiable resource request')
        self.serial+=1
        ticket=(self.serial,task,cores,gpu);self.waiters.append(ticket)
        self.publish()
        acquired=None;stack=ExitStack()
        try:
            while acquired is None:
                free=self.free_gpus() if any(w[3] for w in self.waiters) else set()
                # Runnable requests ordered by grants then arrival, without GPU head-of-line blocking.
                ready=[w for w in self.waiters if w[2]<=len(self.cpus) and (not w[3] or free)]
                ready.sort(key=lambda w:(self.grants.get(w[1],0),w[0]))
                if ready and ready[0]==ticket and (not gpu or free):
                    chosen=[];protected=set()
                    for cpu in sorted(self.cpus):
                        if cpu in protected:continue
                        chosen.append(cpu);protected.update(siblings(cpu))
                        if len(chosen)==cores:break
                    device=min(free) if gpu else None
                    if len(chosen)==cores:
                        try:
                            if not self.mock:
                                for cpu in sorted(protected):stack.enter_context(lock(f'resource:cpu:{cpu}'))
                                if device is not None:stack.enter_context(lock(f'resource:gpu:{device}'))
                        except BlockingIOError:
                            stack.close();stack=ExitStack()
                        else:
                            taken=self.cpus & protected
                            self.cpus-=taken
                            if device is not None:self.gpus.remove(device)
                            acquired={'cpus':chosen,'gpu':device}
                            self.active[ticket[0]]={'task':task,**acquired}
                            self.grants[task]=self.grants.get(task,0)+1
                            self.waiters.remove(ticket)
                            self.publish()
                if acquired is None:await asyncio.sleep(.1 if self.mock else .5)
            yield acquired
        finally:
            if ticket in self.waiters:self.waiters.remove(ticket)
            if acquired is not None:
                self.cpus.update(taken)
                if acquired['gpu'] is not None:self.gpus.add(acquired['gpu'])
                self.active.pop(ticket[0],None)
            stack.close()
            self.publish()
