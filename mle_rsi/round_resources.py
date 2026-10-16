"""Fair shared candidate slots, with GPU leases acquired before consuming CPU slots."""
import asyncio
from contextlib import asynccontextmanager
import fcntl
import subprocess
import time
from . import WORKSPACE
from .common import write


class ResourcePool:
    def __init__(self, root, groups, gpu_pool, mock=False):
        self.root = root
        self.groups = [tuple(g) for g in groups]
        self.free = list(self.groups)
        self.gpus = list(gpu_pool)
        self.mock = mock
        self.waiters = []
        self.active = {}
        self.grants = {}
        self.serial = 0
        self.gate = asyncio.Lock()
        self.last_probe = 0
        self.available = set()
        self.mock_used = set()

    def snapshot(self):
        write(self.root/'scheduler.json', {
            'updated_at': time.time(), 'slots': len(self.groups),
            'active': list(self.active.values()),
            'waiting': [{'task': w['task'], 'gpu': w['gpu'], 'kind': w['kind'], 'queued_at': w['queued_at']} for w in self.waiters],
        })

    def probe(self):
        if self.mock:
            self.available = set(self.gpus) - self.mock_used
            return
        if time.monotonic() - self.last_probe < 2:
            return
        result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=5, check=True)
        self.available = {int(a) for a,b in (line.split(',') for line in result.stdout.splitlines())
                          if int(a) in self.gpus and int(b) <= 512}
        # A small CUDA context is still occupied: never admit from VRAM alone.
        identities = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader,nounits'],
                                    capture_output=True, text=True, timeout=5, check=True)
        processes = subprocess.run(['nvidia-smi', '--query-compute-apps=gpu_uuid', '--format=csv,noheader,nounits'],
                                   capture_output=True, text=True, timeout=5, check=True)
        busy = set(processes.stdout.splitlines())
        self.available -= {int(i) for i,u in (line.split(',') for line in identities.stdout.splitlines()) if u.strip() in busy}
        self.last_probe = time.monotonic()

    def try_gpu(self):
        for gpu in sorted(self.available):
            if self.mock:
                if gpu not in self.mock_used:
                    self.mock_used.add(gpu)
                    return gpu, None
                continue
            path = WORKSPACE/f'runs/gpu-leases/{__import__("socket").gethostname()}/gpu-{gpu}.lock'
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open('a+')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return gpu, handle
            except BlockingIOError:
                handle.close()
        return None, None

    @asynccontextmanager
    async def slot(self, task, gpu=False, kind='training'):
        self.serial += 1
        ticket = {'id': self.serial, 'task': task, 'gpu': gpu, 'kind': kind, 'queued_at': time.time()}
        self.waiters.append(ticket)
        self.snapshot()
        slot = None
        handle = None
        try:
            while slot is None:
                async with self.gate:
                    if self.free:
                        if any(w['gpu'] for w in self.waiters):
                            await asyncio.to_thread(self.probe)
                        # Lowest number of grants first, FIFO ties. CPU work can bypass GPU waiters.
                        ordered = sorted(self.waiters, key=lambda w: (self.grants.get(w['task'], 0), w['id']))
                        for waiter in ordered:
                            device, candidate_handle = (None, None)
                            if waiter['gpu']:
                                device, candidate_handle = self.try_gpu()
                                if device is None:
                                    continue
                            if waiter is not ticket:
                                if candidate_handle:
                                    candidate_handle.close()
                                if self.mock and device is not None:
                                    self.mock_used.remove(device)
                                break
                            handle = candidate_handle
                            slot = {'cpus': self.free.pop(0), 'gpu': device,
                                    'fd': handle.fileno() if handle else None}
                            self.waiters.remove(ticket)
                            self.grants[task] = self.grants.get(task, 0) + 1
                            self.active[ticket['id']] = {'task': task, 'kind': kind,
                                'cpus': list(slot['cpus']), 'gpu': device, 'started_at': time.time(),
                                'queue_wait_seconds': time.time()-ticket['queued_at']}
                            self.snapshot()
                            break
                if slot is None:
                    await asyncio.sleep(.1 if self.mock else .5)
            yield slot
        finally:
            if ticket in self.waiters:
                self.waiters.remove(ticket)
            if slot is not None:
                self.free.append(slot['cpus'])
                self.active.pop(ticket['id'], None)
                if self.mock and slot['gpu'] is not None:
                    self.mock_used.remove(slot['gpu'])
            if handle:
                handle.close()
            self.snapshot()
