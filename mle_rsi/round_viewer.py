"""Expose outer-controller state in the native search-tree viewer."""
from .common import lock, read


def task_view_state(root, task):
    """A campaign writer lock alone cannot distinguish queued from stopped."""
    path = root/'task-status'/f'{task}.json'
    if not (root/'round-status.json').exists() or not path.exists():
        return None
    row = read(path)
    state = row['state']
    if state == 'retired_medal':
        return {'state': 'completed', 'phase': '已获牌封存'}
    try:
        with lock(root/'controller', blocking=False):
            return {'state': 'stopped', 'phase': '控制器已停止'}
    except BlockingIOError:
        pass
    if state in ('blocked', 'budget_exhausted'):
        return {'state': 'stopped', 'phase': '需检查' if state == 'blocked' else '额度耗尽'}
    if read(root/'round-status.json').get('state') == 'paused' or state == 'paused':
        return {'state': 'stopped', 'phase': '已暂停'}
    labels = {'retry_wait': '连接失败，等待自动重试', 'queued': '等待调度', 'searching': '搜索中', 'grading': '评分中',
              'round_complete': '本轮完成，等待下一轮', 'offline_refresh': '离线策略更新'}
    return {'state': 'running', 'phase': labels.get(state, state)}


def install(root):
    from dream_rsi.web.data import RunReader
    original = RunReader.summary
    def summary(self, store, name):
        row = original(self, store, name)
        task = name.removeprefix('dev-')
        override = task_view_state(root, task)
        if override is not None:
            row.update(override)
        return row
    RunReader.summary = summary
