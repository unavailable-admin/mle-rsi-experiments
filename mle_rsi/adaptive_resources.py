"""Mixed four/eight-core allocations from a shared, NUMA-aware physical CPU pool."""
import asyncio
from contextlib import asynccontextmanager
import time
from .round_resources import ResourcePool


class MixedResourcePool(ResourcePool):
    def __init__(self, root, execution, mock=False):
        super().__init__(root, execution['cpu_groups'], execution['gpu_pool'], mock)
        self.settings = execution['mixed_resources']
        self.affinity = execution.get('gpu_cpu_affinity', {})
        self.max_slots = self.settings.get('max_slots', 16)

    def needed(self, task, gpu, kind):
        return (self.settings.get('task_cpu_cores', {}).get(task, 4 if gpu else 8)
                if kind != 'grading' else 8) // 4

    def groups_for(self, count, gpu):
        available = list(self.free)
        if gpu is not None:
            local = set(self.affinity.get(str(gpu), []))
            available.sort(key=lambda group: (not set(group) <= local, group))
        else:
            available.sort()
        return available[:count] if len(available) >= count else []

    def snapshot(self):
        super().snapshot()
        from .common import read, write
        p = self.root/'scheduler.json'
        d = read(p)
        d.update(slots=self.max_slots, policy='mixed-4-8', free_cpu_cores=sum(map(len,self.free)),
                 cpu_waiting=sum(not w['gpu'] for w in self.waiters),
                 gpu_waiting=sum(w['gpu'] for w in self.waiters))
        write(p,d)

    @asynccontextmanager
    async def slot(self, task, gpu=False, kind='training'):
        self.serial += 1
        ticket = dict(id=self.serial, task=task, gpu=gpu, kind=kind, queued_at=time.time())
        self.waiters.append(ticket)
        self.snapshot()
        slot = handle = None
        claimed = []
        try:
            while slot is None:
                async with self.gate:
                    if self.free and len(self.active) < self.max_slots:
                        if any(w['gpu'] for w in self.waiters):
                            await asyncio.to_thread(self.probe)
                        # Age prevents starvation; otherwise runnable GPU work precedes CPU
                        # borrowing. Per-task grant counts keep same-class tasks fair.
                        ordered = sorted(self.waiters, key=lambda w:(
                            0 if time.time()-w['queued_at'] >= 600 else 1,
                            w['queued_at'] if time.time()-w['queued_at'] >= 600 else 0,
                            not w['gpu'], self.grants.get(w['task'],0), w['id']))
                        for w in ordered:
                            if w['kind']=='grading' and any(a['kind']=='grading' for a in self.active.values()):
                                continue
                            count = self.needed(w['task'],w['gpu'],w['kind'])
                            if len(self.free) < count:
                                # Let enough blocks accumulate for an aged CPU request.
                                # Otherwise a stream of four-core jobs can starve it forever.
                                if not w['gpu'] and time.time()-w['queued_at'] >= 600:
                                    break
                                continue
                            device, candidate_handle = (None,None)
                            if w['gpu']:
                                device,candidate_handle = self.try_gpu()
                                if device is None:
                                    continue
                            if w is not ticket:
                                if candidate_handle: candidate_handle.close()
                                if self.mock and device is not None: self.mock_used.remove(device)
                                break
                            handle = candidate_handle
                            claimed = self.groups_for(count,device)
                            for group in claimed: self.free.remove(group)
                            slot = dict(cpus=tuple(c for group in claimed for c in group),gpu=device,
                                        fd=handle.fileno() if handle else None)
                            self.waiters.remove(ticket)
                            self.grants[task] = self.grants.get(task,0)+1
                            self.active[ticket['id']] = dict(task=task,kind=kind,cpus=list(slot['cpus']),
                                gpu=device,started_at=time.time(),queue_wait_seconds=time.time()-ticket['queued_at'])
                            self.snapshot()
                            break
                if slot is None: await asyncio.sleep(.01 if self.mock else .5)
            yield slot
        finally:
            if ticket in self.waiters: self.waiters.remove(ticket)
            if slot is not None:
                self.free.extend(claimed)
                self.active.pop(ticket['id'],None)
                if self.mock and slot['gpu'] is not None: self.mock_used.remove(slot['gpu'])
            if handle: handle.close()
            self.snapshot()
