#!/usr/bin/env python3
"""Restore reviewed dependency edits after a clean recursive clone; fail on drift."""
import hashlib,json,subprocess
from pathlib import Path
repo=Path(__file__).resolve().parents[1]
manifest=json.loads((repo/'patches/runtime/manifest.json').read_text())
for name,record in manifest.items():
    sub=repo/'third_party'/name
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=sub,text=True).strip()
    if head!=record['base_commit']:raise SystemExit('Unexpected dependency version: '+name)
    actual={p:hashlib.sha256((sub/p).read_bytes()).hexdigest() for p in record['files']}
    if all(actual[p]==d['after'] for p,d in record['files'].items()):
        print(name+': patches already applied');continue
    if not all(actual[p]==d['before'] for p,d in record['files'].items()):
        raise SystemExit('Dependency has conflicting local edits: '+name)
    patch=repo/'patches/runtime'/f'{name}.patch'
    subprocess.run(['git','apply','--check',str(patch)],cwd=sub,check=True)
    subprocess.run(['git','apply',str(patch)],cwd=sub,check=True)
    assert all(hashlib.sha256((sub/p).read_bytes()).hexdigest()==d['after'] for p,d in record['files'].items())
    print(name+': patches applied and verified')
