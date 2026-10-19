import json
from pathlib import Path
import pytest
from mle_rsi import inventory
from mle_rsi.backend import MLEBackend
from mle_rsi.io import write


def test_all_lite22_tasks_have_environment_guidance():
    assert len(inventory.TASK_FAMILIES)==22
    assert inventory.TASK_FAMILIES['aptos2019-blindness-detection']=='vision'
    assert inventory.TASK_FAMILIES['text-normalization-challenge-russian-language']=='normalization_russian'
    assert inventory.TASK_FAMILIES['mlsp-2013-birds']=='audio'


def test_asset_identity_detects_weight_and_tokenizer_changes(tmp_path):
    model=tmp_path/'models';model.mkdir()
    tokenizer=model/'tokenizer.json';tokenizer.write_text('one')
    host={'pretrained_models':str(model)}
    before=inventory.asset_identity(host)
    tokenizer.write_text('two')
    assert inventory.asset_identity(host)!=before
    with pytest.raises(ValueError,match='Missing model mount'):
        inventory.asset_identity({'torch_cache':str(tmp_path/'missing')})


def test_staging_does_not_change_source_permissions(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir(mode=0o700)
    weight=source/'weight';weight.write_bytes(b'weights');weight.chmod(0o600)
    def verify(host,destination):
        staged=Path(host['torch_cache'])/'weight'
        assert staged.read_bytes()==b'weights'
        assert staged.stat().st_mode & 0o777==0o644
        return {'models':[]}
    monkeypatch.setattr(inventory,'verify',verify)
    result=inventory.prepare_host({'torch_cache':str(source)},tmp_path/'bundle')
    assert weight.stat().st_mode & 0o777==0o600
    assert Path(result['host_config']).is_file()
    with pytest.raises(FileExistsError):inventory.prepare_host({},tmp_path/'bundle')


def test_real_backend_exposes_verified_inventory_without_asset_hash_dump(tmp_path):
    public=tmp_path/'public';public.mkdir();(public/'description.md').write_text('fixture')
    task={'id':'aerial-cactus-identification','public_dir':str(public)}
    write(tmp_path/'prepared'/task['id']/'manifest.json',{'metric':'auc','task':task['id']})
    write(tmp_path/'runtime/inventory.json',{'python':'3.12','models':[{'path':'/models/torch/checkpoint','status':'verified'}],
        'packages':{'cv2':{'status':'available','version':'4.11'}},'model_assets':{'private-host-path':'not-for-prompt'}})
    backend=MLEBackend(tmp_path,task,{'mode':'real'},None,None)
    prompt=backend.task.candidate_contract
    assert '/models/torch/checkpoint' in prompt and '4.11' in prompt
    assert 'not-for-prompt' not in prompt and 'MLE_RESOURCE="gpu"' in prompt


def test_failed_container_probe_never_publishes_inventory(tmp_path,monkeypatch):
    from mle_rsi import containers
    monkeypatch.setattr(containers,'measure_container',lambda *args,**kwargs:{'exit_code':1,'timed_out':False})
    with pytest.raises(RuntimeError,match='Environment verification failed'):
        inventory.verify({'python_environment':'/fixture','image':'fixture','cpus':[0]},tmp_path/'verify')
    assert not (tmp_path/'verify/inventory.json').exists()


def test_readiness_does_not_claim_english_models_cover_audio_or_russian_normalization():
    value={'models':[{'path':'/models/pretrained/english','kind':'transformers','model_type':'bert','status':'verified'}],
           'functional_checks':dict.fromkeys(['audio_decode_spectrum','english_russian_text_features'],{'status':'verified'})}
    result=inventory.task_readiness(value)
    assert result['spooky-author-identification']['pretrained_candidates']==['/models/pretrained/english']
    assert result['mlsp-2013-birds']['pretrained_candidates']==[]
    assert result['text-normalization-challenge-russian-language']['pretrained_candidates']==[]
    assert not any(r['task_quality_verified'] for r in result.values())


def test_runtime_helper_uses_verified_offline_options(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from mle_rsi import runtime
    calls=[]
    class Factory:
        @staticmethod
        def from_pretrained(path,**kwargs):
            calls.append((path,kwargs))
            return SimpleNamespace(to=lambda device:'model-on-'+device)
    manifest=tmp_path/'inventory.json'
    manifest.write_text(json.dumps({'models':[{'path':'/models/modernbert','kind':'transformers','status':'verified',
        'model_type':'modernbert','load_kwargs':{'reference_compile':False}}]}))
    monkeypatch.setattr(runtime,'Path',lambda path:manifest if path=='/runtime/inventory.json' else Path(path))
    monkeypatch.setattr(runtime,'report_model',lambda *a,**kw:None)
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False)))
    monkeypatch.setitem(sys.modules,'transformers',SimpleNamespace(AutoModel=Factory,AutoTokenizer=Factory,AutoImageProcessor=Factory))
    model,_=runtime.load_pretrained('/models/modernbert')
    assert model=='model-on-cpu'
    assert calls[0][1]=={'local_files_only':True,'reference_compile':False}
    assert calls[1][1]=={'local_files_only':True}
    with pytest.raises(ValueError,match='not in verified'):
        runtime.load_pretrained('/models/not-verified')
    assert len(calls)==2
