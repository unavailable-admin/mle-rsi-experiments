"""Private official results belong to the controller, never the agent context."""
import json
import os
from pathlib import Path
from .io import read,write,sha,digest
from .process import run_accounted


def medal(value):
    if isinstance(value,dict):return value.get('any_medal') is True or any(medal(v) for v in value.values())
    return isinstance(value,list) and any(medal(v) for v in value)


async def grade(root,task,round_index,selected,spec,pool,ledger):
    directory=Path(root)/'official'/task['id']/str(round_index)
    path=directory/'result.json'
    identity=digest(selected)
    if path.exists():
        result=read(path)
        if result['selection_hash']!=identity:raise ValueError('Official selection changed')
        return result
    if spec['mode']!='real' or selected is None:
        result={'state':'verification_only' if spec['mode']!='real' else 'no_submission','any_medal':False,'valid_submission':False}
    else:
        # Only the already-selected public winner is submitted. Private results
        # are persisted outside the campaign directory and never passed back.
        diagnostics=selected['report']['diagnostics']
        submission=Path(diagnostics['output_dir'])/'submission.csv'
        if sha(submission)!=diagnostics['submission_sha256']:raise ValueError('Selected submission changed')
        write(directory/'selected.json',selected)
        entries=directory/'submissions.jsonl'
        entries.write_text(json.dumps({'competition_id':task['id'],'submission_path':str(submission)})+'\n')
        ident=f"grading:{task['id']}:{round_index}"
        async with pool.slot(task['id'],task['cores']) as slot:
            measurement=await run_accounted([spec['host']['grading_python'],str(Path(__file__).with_name('grade_worker.py')),
                'grade','--submission',str(entries),'--output-dir',str(directory/'grading'),
                '--data-dir',spec['host']['data_dir']],directory/'measurement',1800,slot['cpus'],
                ledger,ident,task['id'],identity)
            if measurement['exit_code'] or measurement['timed_out']:raise RuntimeError('Official grader failed')
        reports=list((directory/'grading').glob('*_grading_report.json'))
        if len(reports)!=1:raise ValueError('Expected exactly one official report')
        official=read(reports[0])
        result={'state':'graded','valid_submission':official['total_valid_submissions']==1,
                'any_medal':medal(official),'official_report':official}
    result.update(selection_hash=identity,task=task['id'],round=round_index)
    write(path,result)
    return result
