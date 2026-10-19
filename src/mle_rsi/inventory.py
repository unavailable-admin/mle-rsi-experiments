"""Prepare readable model bundles and verify the actual offline candidate environment."""
from pathlib import Path
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from .io import read, write, sha, lock

VISION = ['aptos2019-blindness-detection','dog-breed-identification','dogs-vs-cats-redux-kernels-edition',
    'plant-pathology-2020-fgvc7','siim-isic-melanoma-classification','histopathologic-cancer-detection',
    'ranzcr-clip-catheter-line-classification','the-icml-2013-whale-challenge-right-whale-redux']
TEXT = ['jigsaw-toxic-comment-classification-challenge','detecting-insults-in-social-commentary',
    'random-acts-of-pizza','spooky-author-identification']
OTHER = {'aerial-cactus-identification':'vision_optional','leaf-classification':'descriptors_and_images',
    'text-normalization-challenge-english-language':'normalization_english',
    'text-normalization-challenge-russian-language':'normalization_russian','mlsp-2013-birds':'audio',
    'denoising-dirty-documents':'image_restoration','new-york-city-taxi-fare-prediction':'tabular',
    'nomad2018-predict-transparent-conductors':'tabular','tabular-playground-series-dec-2021':'tabular',
    'tabular-playground-series-may-2022':'tabular'}
TASK_FAMILIES={**dict.fromkeys(VISION,'vision'),**dict.fromkeys(TEXT,'text'),**OTHER}


def prepare_gpu_configs(host,destination):
    if not host.get('gpus'):return host
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    library=Path('/usr/lib/x86_64-linux-gnu')
    paths=sorted(set(library.glob('libcuda.so*'))|set(library.glob('libnvidia-*.so*')))
    paths=[p for p in paths if p.is_file()]
    if not (library/'libcuda.so.1').is_file():raise ValueError('Local CUDA driver library unavailable')
    mounts=[{'Source':str(p.resolve()),'Target':str(p),'Type':'bind','ReadOnly':True} for p in paths]
    binary=shutil.which('nvidia-smi')
    if not binary:raise ValueError('nvidia-smi unavailable')
    mounts.append({'Source':binary,'Target':'/usr/bin/nvidia-smi','Type':'bind','ReadOnly':True})
    for gpu in host['gpus']:
        xml=ET.fromstring(subprocess.check_output(['nvidia-smi',f'--id={gpu}','-q','-x'],text=True))
        minor=int(xml.findtext('gpu/minor_number'))
        devices=[f'/dev/nvidia{minor}','/dev/nvidiactl','/dev/nvidia-uvm']
        if any(not Path(p).exists() for p in devices):raise ValueError('Missing NVIDIA device')
        write(destination/f'sysbox-gpu{gpu}.json',{'runtime':'sysbox-runc','shm_size':'4G',
            'devices':[f'{p}:{p}:rw' for p in devices],'mounts':mounts})
    return {**host,'gpu_config_dir':str(destination.resolve())}


def asset_identity(host):
    values={}
    for key in ('torch_cache','pretrained_models','gpu_config_dir'):
        if not host.get(key):continue
        root=Path(host[key])
        if not root.is_dir():raise ValueError('Missing model mount: '+key)
        values[key]={str(f.relative_to(root)):sha(f) for f in sorted(root.rglob('*')) if f.is_file()}
    return values


def prompt_inventory(value,task):
    # Hash manifests stay in the release; avoid sending thousands of paths to the LLM.
    return {'python':value.get('python'),'packages':value.get('packages',{}),
        'hardware':value.get('hardware',{}),'models':value.get('models',[]),
        'functional_checks':value.get('functional_checks',{}),
        'task_family':TASK_FAMILIES.get(task,'unspecified'),'notes':value.get('notes',[])}


def task_readiness(value):
    checks=value.get('functional_checks',{})
    result={}
    for task,family in TASK_FAMILIES.items():
        check=('audio_decode_spectrum' if family=='audio' else
               'english_russian_text_features' if family in ('text','normalization_english','normalization_russian') else
               'tabular_estimators' if family=='tabular' else 'image_features_restoration')
        models=[]
        for model in value.get('models',[]):
            visual=model.get('kind')=='resnet50' or 'siglip' in model.get('model_type','')
            if model.get('status')=='verified' and ((family in ('vision','vision_optional','descriptors_and_images') and visual)
                    or (family=='text' and not visual)):
                models.append(model['path'])
        result[task]={'family':family,'environment_check':check,
            'status':checks.get(check,{}).get('status','not_verified'),'pretrained_candidates':models,
            'task_quality_verified':False}
    return result


def verify(host,destination):
    from .containers import measure_container
    from . import inventory_probe
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False)
    output=destination/'output';output.mkdir()
    if host.get('container_output_uid') is not None:
        os.chown(output,host['container_output_uid'],host['container_output_gid'])
    else:output.chmod(0o777)
    mounts=[]
    def mount(source,target):
        mounts.append({'Source':str(Path(source).resolve()),'Target':target,'Type':'bind','ReadOnly':True})
    environment=Path(host['python_environment'])
    script=destination/'probe.py';shutil.copyfile(inventory_probe.__file__,script);script.chmod(0o644)
    mount(environment,str(environment));mount(script,'/probe.py')
    for p in host.get('python_extra_mounts',[]):mount(p,p)
    for key,target in [('torch_cache','/models/torch'),('pretrained_models','/models/pretrained')]:
        if host.get(key):mount(host[key],target)
    mounts.append({'Source':str(output),'Target':'/output','Type':'bind','ReadOnly':False})
    gpu=next(iter(host.get('gpus',[])),None)
    command=[str(environment/'bin/python'),'/probe.py','/output/inventory.json']
    if gpu is not None:command+=['--require-gpu']
    from .resources import ResourcePool, siblings
    with ExitStack() as leases:
        for cpu in sorted(set().union(*(siblings(c) for c in host['cpus'][:2]))):
            leases.enter_context(lock(f'resource:cpu:{cpu}'))
        if gpu is not None:
            leases.enter_context(lock(f'resource:gpu:{gpu}'))
            if gpu not in ResourcePool(host).free_gpus():raise RuntimeError('Configured verification GPU is busy')
        result=measure_container(command,'/output',{'CONDA_PREFIX':str(environment),'BENCH_CONTAINER_IMAGE':host['image'],
            'HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1','TORCH_HOME':'/models/torch','PYTHONUNBUFFERED':'1',
            'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','MKL_NUM_THREADS':'2'},destination/'measurement',mounts,
            timeout=900,cpu_ids=host['cpus'][:2],gpu=gpu,memory=host.get('memory','32g'),gpu_config_dir=host.get('gpu_config_dir'))
    if result['exit_code'] or result['timed_out']:raise RuntimeError('Environment verification failed: '+str(destination/'measurement/stdout.log'))
    value=read(output/'inventory.json');value['task_families']=TASK_FAMILIES
    value['task_readiness']=task_readiness(value)
    value['model_assets']=asset_identity(host)
    value['verification']={'image_id':result['image_id'],'network_disabled':result['network_disabled'],
        'measurement':str(destination/'measurement/resources.json')}
    write(destination/'inventory.json',value)
    return value


def prepare_host(host,destination):
    """Copy shared assets, never chmod or mutate somebody else's model cache."""
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False)
    host=dict(host)
    host=prepare_gpu_configs(host,destination/'gpu-configs')
    for key in ('torch_cache','pretrained_models'):
        if not host.get(key):continue
        source=Path(host[key]);target=destination/key
        if not source.is_dir():raise ValueError('Missing source model directory: '+str(source))
        shutil.copytree(source,target,symlinks=False)
        for p in [target,*target.rglob('*')]:p.chmod(0o755 if p.is_dir() else 0o644)
        host[key]=str(target)
    host['runtime_inventory']=verify(host,destination/'verification')
    write(destination/'host.json',host)
    return {'host_config':str(destination/'host.json'),'models':host['runtime_inventory']['models']}
