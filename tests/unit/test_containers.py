import docker
import pytest
from mle_rsi.containers import kill_if_running


class Container:
    status='exited'
    def kill(self):raise docker.errors.APIError('already stopped')
    def reload(self):pass


def test_kill_exit_race_is_not_failure():kill_if_running(Container())


def test_kill_daemon_error_is_not_hidden():
    container=Container();container.status='running'
    with pytest.raises(docker.errors.APIError):kill_if_running(container)


def test_recover_terminated_container_without_relaunch(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    from mle_rsi.containers import recover_container
    from mle_rsi.io import write,read
    class Finished:
        id='owned-container'
        status='exited'
        attrs={'State':{'FinishedAt':datetime.fromtimestamp(110,timezone.utc).isoformat(),'ExitCode':0}}
        def reload(self):pass
        def logs(self):return b'completed'
        def remove(self):self.removed=True
    finished=Finished()
    class Client:
        containers=None
        def get(self,ident):
            assert ident==finished.id
            return finished
        def close(self):pass
    client=Client();client.containers=client
    monkeypatch.setattr('mle_rsi.containers.docker.from_env',lambda:client)
    write(tmp_path/'container.json',{'container_id':finished.id,'created_at':100,'timeout_seconds':30,'gpu':None})
    result=recover_container(tmp_path)
    assert result['wall_seconds']==10 and result['exit_code']==0 and finished.removed
    assert read(tmp_path/'resources.json')==result
