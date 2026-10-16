"""Probe installed packages and public model cache without reading credentials."""
import importlib.metadata
import json
from pathlib import Path
import sys


def inspect(workspace, execution=None):
    root=Path(workspace)
    versions={}
    for name in ['numpy','pandas','scipy','scikit-learn','torch','torchvision','timm','transformers','lightgbm','xgboost','catboost','Pillow','opencv-python','librosa','scikit-image']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    execution = execution or {}
    checkpoints=Path(execution.get('torch_cache',root/'cache/torch'))/'hub/checkpoints'
    models=Path(execution.get('pretrained_models',root/'runs/mlevolve-gemini-four-v3/models'))
    return {'python':sys.version.split()[0],'packages':versions,
        'torch_checkpoints':['/models/torch/hub/checkpoints/'+p.name for p in sorted(checkpoints.glob('*')) if p.is_file()],
        'pretrained_directories':['/models/pretrained/'+str(p.relative_to(models)) for p in sorted(models.glob('*/*')) if p.is_dir()],
        'notes':['Network disabled inside candidates. Weights must already exist.',
                 'This worker uses PyTorch 2.7.1 with CUDA 12.8 and RTX 5090; use the recorded package versions.',
                 'LightGBM is CPU-only. Missing packages cannot be installed inside candidates.',
                 'Import mle_runtime; load_resnet50() loads the available checkpoint or raises explicitly.',
                 'Use remaining_seconds() and progress(stage, ...) before costly fit/refit/prediction stages.']}


if __name__=='__main__':
    execution=json.loads(sys.argv[3]) if len(sys.argv)>3 else None
    Path(sys.argv[2]).write_text(json.dumps(inspect(sys.argv[1],execution),indent=2)+'\n')
