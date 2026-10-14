"""Probe installed packages and public model cache without reading credentials."""
import importlib.metadata
import json
from pathlib import Path
import sys


def inspect(workspace):
    root=Path(workspace)
    versions={}
    for name in ['numpy','pandas','scipy','scikit-learn','torch','torchvision','timm','transformers','lightgbm','xgboost','catboost','Pillow','opencv-python','librosa','scikit-image']:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    checkpoints=root/'cache/torch/hub/checkpoints'
    models=root/'runs/mlevolve-gemini-four-v3/models'
    return {'python':sys.version.split()[0],'packages':versions,
        'torch_checkpoints':['/models/torch/hub/checkpoints/'+p.name for p in sorted(checkpoints.glob('*')) if p.is_file()],
        'pretrained_directories':['/models/pretrained/'+str(p.relative_to(models)) for p in sorted(models.glob('*/*')) if p.is_dir()],
        'notes':['Network disabled inside candidates. Weights must already exist.',
                 'Torch 2.2 uses torch.cuda.amp.GradScaler, not torch.amp.GradScaler.',
                 'LightGBM is CPU-only. Missing packages cannot be installed inside candidates.',
                 'Import mle_runtime; load_resnet50() loads the available checkpoint or raises explicitly.',
                 'Use remaining_seconds() and progress(stage, ...) before costly fit/refit/prediction stages.']}


if __name__=='__main__':
    Path(sys.argv[2]).write_text(json.dumps(inspect(sys.argv[1]),indent=2)+'\n')
