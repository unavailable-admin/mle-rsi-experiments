import os
import stat

from mle_rsi.mount_permissions import prepare_run_inputs, readable_input


def test_restrictive_umask_and_scope(tmp_path):
    old = os.umask(0o027)
    try:
        for name in ('runtime', 'prepared'):
            (tmp_path/name).mkdir()
            (tmp_path/name/'input').write_text('data')
        secret = tmp_path/'credentials'
        secret.write_text('secret')
        (tmp_path/'prepared'/'external').symlink_to(secret)
    finally:
        os.umask(old)
    prepare_run_inputs(tmp_path)
    for name in ('runtime', 'prepared'):
        assert stat.S_IMODE((tmp_path/name).stat().st_mode) == 0o755
        assert stat.S_IMODE((tmp_path/name/'input').stat().st_mode) == 0o644
    assert stat.S_IMODE(secret.stat().st_mode) == 0o640
    readable_input(tmp_path/'prepared'/'input')
    assert stat.S_IMODE((tmp_path/'prepared'/'input').stat().st_mode) == 0o644
