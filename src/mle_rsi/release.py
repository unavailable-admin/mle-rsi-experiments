"""Freeze two installed code packages, configuration, data and image identity."""
import subprocess
import socket
from contextlib import closing
from pathlib import Path
from .io import read,write,sha,digest
from . import runtime


def sources():
    import dream_rsi
    roots={'mle':Path(__file__).parent,'dream':Path(dream_rsi.__file__).parent}
    values={}
    for name,root in roots.items():
        for p in sorted(root.rglob('*.py')):
            values[name+'/'+str(p.relative_to(root))]=sha(p)
    prompts=roots['dream'].parents[1]/'prompts'
    if not prompts.is_dir():
        from importlib.resources import files
        prompts=files('dream_rsi.prompt_templates')
    for p in prompts.iterdir():
        if p.name.endswith('.txt'):values['prompts/'+p.name]=sha(p)
    return values


def git_identity(path):
    def git(*args):return subprocess.check_output(['git','-C',str(path),*args],text=True).strip()
    try:return {'commit':git('rev-parse','HEAD'),'dirty':bool(git('status','--porcelain'))}
    except (subprocess.CalledProcessError,FileNotFoundError):return {'commit':None,'dirty':None}


def assets(root):
    root=Path(root)
    return {str(p.relative_to(root)):sha(p) for name in ('prepared','runtime')
            for p in sorted((root/name).rglob('*')) if p.is_file()}


def data_identity(spec):
    return {task['id']:{str(p.relative_to(Path(task['public_dir']))):sha(p)
            for p in sorted(Path(task['public_dir']).rglob('*')) if p.is_file()}
            for task in spec['tasks'] if task['id'] not in spec.get('excluded_tasks',[])} if spec['mode']=='real' else {}


def freeze(root,spec):
    import dream_rsi
    value={'schema':1,'host':socket.gethostname(),'config_hash':digest(spec),
           'sources':sources(),'assets':assets(root),'data':data_identity(spec),
           'versions':{'mle':git_identity(Path(__file__).parents[2]),
                       'dream':git_identity(Path(dream_rsi.__file__).parent)}}
    if spec['mode']=='real':
        if any(v['dirty'] is not False for v in value['versions'].values()):
            raise ValueError('Real releases require clean committed code in both repositories')
        import docker
        with closing(docker.from_env()) as client:
            value['image_id']=client.images.get(spec['host']['image']).id
    write(Path(root)/'release.json',value)
    return value


def verify(root,spec,check_data=True):
    value=read(Path(root)/'release.json')
    if value['host']!=socket.gethostname():raise ValueError('This run has a single-controller host; migration requires a new release')
    if value['config_hash']!=digest(spec) or value['sources']!=sources() or value['assets']!=assets(root):
        raise ValueError('Frozen configuration, source or prepared assets changed')
    if check_data and value['data']!=data_identity(spec):raise ValueError('Public input data changed')
    if spec['mode']=='real':
        import docker
        with closing(docker.from_env()) as client:
            if client.images.get(spec['host']['image']).id!=value['image_id']:raise ValueError('Container image changed')
    return value
