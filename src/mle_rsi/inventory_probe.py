"""Offline probe executed by the candidate Python inside its actual container."""
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

PACKAGES = {'numpy':'numpy','pandas':'pandas','scipy':'scipy','scikit-learn':'sklearn',
    'torch':'torch','torchvision':'torchvision','timm':'timm','transformers':'transformers',
    'lightgbm':'lightgbm','xgboost':'xgboost','catboost':'catboost','Pillow':'PIL',
    'opencv-python-headless':'cv2','librosa':'librosa','scikit-image':'skimage',
    'soundfile':'soundfile','sentencepiece':'sentencepiece','tokenizers':'tokenizers',
    'sentence-transformers':'sentence_transformers','accelerate':'accelerate'}


def probe(output, require_gpu=False):
    result={'schema':1,'python':sys.version.split()[0],'packages':{},'models':[],
            'torch_checkpoints':[],'pretrained_directories':[], 'network_disabled':True}
    for distribution,module in PACKAGES.items():
        try:
            imported=importlib.import_module(module)
            try:version=importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:version=getattr(imported,'__version__','unknown')
            result['packages'][module]={'status':'available','version':version,'distribution':distribution}
        except Exception as exc:
            result['packages'][module]={'status':'unavailable','error':f'{type(exc).__name__}: {exc}'}
    import torch
    torch.set_num_threads(2)
    device='cuda' if torch.cuda.is_available() else 'cpu'
    result['hardware']={'cuda_available':torch.cuda.is_available(),'torch_cuda':torch.version.cuda,
                        'device':torch.cuda.get_device_name(0) if device=='cuda' else 'cpu'}
    if require_gpu and device!='cuda':raise RuntimeError('Configured GPU is not available inside candidate container')
    def check(path,kind,loader):
        start=time.monotonic();row={'path':str(path),'kind':kind,'device':device}
        try:
            row.update(loader(path));row['status']='verified'
            if kind=='resnet50':result['torch_checkpoints'].append(str(path))
            else:result['pretrained_directories'].append(str(path))
        except Exception as exc:row.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        row['seconds']=time.monotonic()-start;result['models'].append(row)
        Path(output).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(row),flush=True)
        if device=='cuda':torch.cuda.empty_cache()
    def resnet(path):
        from torchvision.models import resnet50
        net=resnet50(weights=None);net.load_state_dict(torch.load(path,map_location='cpu',weights_only=True))
        net.to(device).eval()
        with torch.inference_mode():y=net(torch.zeros(1,3,224,224,device=device))
        assert torch.isfinite(y).all()
        return {'output_shape':list(y.shape),'loader':'mle_runtime.load_resnet50()',
                'preprocessing':'RGB; resize 256, center crop 224; ImageNet mean/std'}
    def huggingface(path):
        from transformers import AutoConfig,AutoModel,AutoTokenizer,AutoImageProcessor
        from PIL import Image
        config=AutoConfig.from_pretrained(path,local_files_only=True)
        kwargs={'reference_compile':False} if config.model_type=='modernbert' else {}
        net=AutoModel.from_pretrained(path,local_files_only=True,**kwargs).to(device).eval()
        if 'siglip' in config.model_type:
            processor=AutoImageProcessor.from_pretrained(path,local_files_only=True)
            batch=processor(images=Image.new('RGB',(256,256)),return_tensors='pt')
            with torch.inference_mode():y=net.get_image_features(**{k:v.to(device) for k,v in batch.items()})
            # Also check the text tokenizer files in a multimodal model bundle.
            AutoTokenizer.from_pretrained(path,local_files_only=True)('offline test')
        else:
            tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True)
            batch=tokenizer('offline model verification',return_tensors='pt')
            with torch.inference_mode():y=net(**{k:v.to(device) for k,v in batch.items()}).last_hidden_state
        assert torch.isfinite(y).all()
        return {'output_shape':list(y.shape),'model_type':config.model_type,
                'load_kwargs':kwargs,'loader':'mle_runtime.load_pretrained(path) returns (model, tokenizer_or_image_processor)',
                'preprocessing':'Use the bundled tokenizer / AutoImageProcessor with local_files_only=True'}
    for path in sorted(Path('/models/torch/hub/checkpoints').glob('resnet50-*.pth')):check(path,'resnet50',resnet)
    for config in sorted(Path('/models/pretrained').glob('*/*/config.json')):check(config.parent,'transformers',huggingface)
    result['notes']=['Only verified models are listed as usable. Unavailable packages are not installed in candidates.',
        'Network disabled. No downloads or remote code. Validation is a tiny inference, not task-quality evidence.',
        'LightGBM uses CPU; no audio or Russian normalization pretrained model is implied by English text weights.']
    result['functional_checks']={}
    def functional(name,fn):
        try:
            fn();result['functional_checks'][name]={'status':'verified'}
        except Exception as exc:result['functional_checks'][name]={'status':'failed','error':f'{type(exc).__name__}: {exc}'}
    def tabular():
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from lightgbm import LGBMClassifier
        from xgboost import XGBClassifier
        from catboost import CatBoostClassifier
        x=np.arange(80,dtype=float).reshape(40,2);y=np.arange(40)%2
        for model in [LogisticRegression(),LGBMClassifier(n_estimators=2,verbosity=-1,n_jobs=1,min_child_samples=2),
                      XGBClassifier(n_estimators=2,n_jobs=1),CatBoostClassifier(iterations=2,verbose=False,thread_count=1)]:
            model.fit(x,y);assert np.isfinite(model.predict_proba(x)).all()
    def images():
        import numpy as np
        import cv2
        from skimage.feature import hog
        from scipy.ndimage import median_filter
        x=np.zeros((64,64),dtype=np.uint8)
        assert np.isfinite(hog(cv2.resize(median_filter(x,3),(64,64)))).all()
    def audio():
        import io
        import numpy as np
        import soundfile as sf
        import librosa
        stream=io.BytesIO();sf.write(stream,np.zeros(16000),16000,format='WAV');stream.seek(0)
        x,sr=sf.read(stream);assert np.isfinite(librosa.feature.melspectrogram(y=x,sr=sr)).all()
    def unicode_text():
        from sklearn.feature_extraction.text import TfidfVectorizer
        x=TfidfVectorizer(analyzer='char',ngram_range=(1,3)).fit_transform(['english 123','русский 123'])
        assert x.shape[0]==2 and x.shape[1]>0
    functional('tabular_estimators',tabular);functional('image_features_restoration',images)
    functional('audio_decode_spectrum',audio);functional('english_russian_text_features',unicode_text)
    Path(output).write_text(json.dumps(result,indent=2)+'\n')
    if any(m['status']!='verified' for m in result['models']):raise RuntimeError('One or more local models failed verification; inspect inventory.json')
    if any(v['status']!='verified' for v in result['functional_checks'].values()):raise RuntimeError('Environment functional check failed')

if __name__=='__main__':probe(sys.argv[1], '--require-gpu' in sys.argv)
