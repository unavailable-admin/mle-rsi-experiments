"""Read-only application summaries; no Dream-RSI database inspection."""
import sqlite3
from pathlib import Path
from .io import read


def status(root):
    root=Path(root)
    result={'run':str(root),'control':read(root/'control.json')}
    result['tasks']={p.stem:read(p) for p in sorted((root/'status').glob('*.json'))}
    if (root/'ledger.sqlite').exists():
        import json
        with sqlite3.connect('file:'+str(root/'ledger.sqlite')+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT state,amounts FROM entries').fetchall()
        result['budget']={'entries':len(rows),'unsettled':sum(state in ('reserved','running','unknown') for state,_ in rows),
            'totals':{k:sum(json.loads(amounts)[k] for _,amounts in rows)
                      for k in ('api_usd','execution_seconds','gpu_seconds')}}
    result['medals']=sum(v.get('official',{}).get('any_medal',False) for v in result['tasks'].values())
    result['task_count']=len(read(root/'config.json')['tasks'])
    return result
