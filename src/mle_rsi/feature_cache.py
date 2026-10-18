"""Host promotion of bounded, immutable, per-task deterministic feature entries.

Candidates can read published entries and propose new ones in their own output.
They cannot write the shared cache. Identity verifies declared dependencies and
bytes; it does not prove arbitrary user code is deterministic.
"""
import os
from pathlib import Path
import shutil
import tempfile
from .io import digest, read, write, sha, lock


def namespace(root, task):
    root = Path(root)
    release = read(root / 'release.json')
    return digest({'schema': 1, 'task': task['id'], 'data': release['data'].get(task['id']),
                   'assets': {k: v for k, v in release['assets'].items() if k.startswith('prepared/' + task['id'] + '/')},
                   'image': release.get('image_id'), 'runtime': release['sources'].get('mle/runtime.py')})


def directory(root, task):
    return Path(root) / 'feature-cache' / task['id']


def promote(root, task, output, settings):
    pending = Path(output) / '.feature-cache'
    if not pending.exists():
        return []
    if pending.is_symlink():
        raise ValueError('Unsafe cache proposal directory')
    target = directory(root, task)
    target.mkdir(parents=True, exist_ok=True)
    evidence = []
    expected = namespace(root, task)
    proposals = sorted(pending.iterdir())
    if len(proposals) > settings.get('max_entries_per_candidate', 16):
        raise ValueError('Too many feature cache proposals')
    with lock(str(target / '.publish')):
        total = sum(p.stat().st_size for p in target.rglob('*') if p.is_file())
        for proposal in proposals:
            if proposal.is_symlink() or not proposal.is_dir():
                raise ValueError('Unsafe cache entry')
            meta_path, data = proposal / 'metadata.json', proposal / 'features.npz'
            if any(p.is_symlink() or not p.is_file() for p in (meta_path, data)):
                raise ValueError('Unsafe feature cache files')
            if meta_path.stat().st_size > 65536 or data.stat().st_size > settings.get('max_entry_bytes', 128 * 1024**2):
                raise ValueError('Feature cache entry exceeds bound')
            meta = read(meta_path)
            key = digest(meta['identity'])
            if proposal.name != key or meta['identity']['namespace'] != expected or meta['sha256'] != sha(data):
                raise ValueError('Feature cache identity/content mismatch')
            destination = target / key
            if destination.exists():
                if read(destination / 'metadata.json')['sha256'] != meta['sha256']:
                    raise ValueError('Nondeterministic feature cache collision')
                evidence.append({'key': key, 'state': 'already_published'})
                continue
            size = data.stat().st_size + meta_path.stat().st_size
            if total + size > settings.get('max_total_bytes', 1024**3):
                evidence.append({'key': key, 'state': 'capacity_reached'})
                continue
            temporary = Path(tempfile.mkdtemp(prefix='.publish-', dir=target))
            try:
                shutil.copyfile(data, temporary / 'features.npz')
                write(temporary / 'metadata.json', meta)
                for path in temporary.iterdir():
                    path.chmod(0o444)
                temporary.chmod(0o555)
                os.rename(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.chmod(0o755)
                    shutil.rmtree(temporary)
            total += size
            evidence.append({'key': key, 'state': 'published'})
    return evidence
