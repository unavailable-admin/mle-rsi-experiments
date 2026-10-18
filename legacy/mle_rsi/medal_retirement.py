"""Explicit exploratory private-feedback allocation; immutable winning checkpoints."""
import time
from .common import read, write, sha


def retire_winners(root, spec, mode):
    policy = spec.get('round_robin',{}).get('retire_on_medal',{})
    if not policy or mode != 'real': return {}
    path = root/'retired-tasks.json'
    retired = read(path) if path.exists() else {}
    # Verify preserved evidence every time, including after migration/resume.
    for task,entry in retired.items():
        folder = root/entry['checkpoint']
        for name,digest in entry['hashes'].items():
            if sha(folder/name) != digest: raise RuntimeError('Retired checkpoint changed: '+task)
    for task in spec['experiment_tasks']:
        if task in retired: continue
        candidates = []
        latest_round = 0
        for result_path in sorted((root/'rounds').glob('*/'+task+'/official-result.json')):
            number = int(result_path.parent.parent.name)
            latest_round = max(latest_round,number)
            if number < policy.get('from_round',4): continue
            result = read(result_path)
            if result.get('state')=='graded' and result.get('valid_submission') is True and result.get('any_medal') is True:
                candidates.append((number,result_path.parent))
        if not candidates: continue
        number,folder = candidates[0]
        from .rounds import verify_seal
        verify_seal(folder)
        names = ['seal.json','selected.json','solver.py','submission.csv','official-result.json']
        retired[task] = dict(round=number,activation_round=max(latest_round,policy.get('activation_round',0)),checkpoint=str(folder.relative_to(root)),retired_at=time.time(),
                             reason='private_medal',hashes={name:sha(folder/name) for name in names})
    write(path,retired)
    return retired
