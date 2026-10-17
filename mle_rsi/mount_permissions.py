"""Permissions for non-secret inputs mounted into user-namespaced containers."""
from pathlib import Path
import stat


def readable_input(path):
    """Grant read/traverse only; never follow symlinks or grant write access."""
    path = Path(path)
    if path.is_symlink():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    required = 0o555 if path.is_dir() else 0o444
    if mode & required != required:
        path.chmod(mode | required)
    if path.is_dir():
        for child in path.iterdir():
            readable_input(child)


def prepare_run_inputs(root):
    # These trees contain candidate-visible data/code, never credentials or API logs.
    for name in ('runtime', 'prepared'):
        readable_input(Path(root) / name)
