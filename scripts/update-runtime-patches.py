#!/usr/bin/env python3
"""Regenerate versioned patches from reviewed dependency edits and pinned bases."""
import hashlib,json,subprocess
from pathlib import Path
repo=Path(__file__).resolve().parents[1]
path=repo/'patches/runtime/manifest.json'
manifest=json.loads(path.read_text())
for name,entry in manifest.items():
    root=repo/'third_party'/name
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()!=entry['base_commit']:
        raise SystemExit('Unexpected submodule base: '+name)
    files=subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=root,text=True).splitlines()
    entry['files']={p:{'before':hashlib.sha256(subprocess.check_output(['git','show','HEAD:'+p],cwd=root)).hexdigest(),
                      'after':hashlib.sha256((root/p).read_bytes()).hexdigest()} for p in files}
    (path.parent/(name+'.patch')).write_bytes(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=root))
path.write_text(json.dumps(manifest,indent=2)+'\n')
