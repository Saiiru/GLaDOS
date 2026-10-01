from pathlib import Path


def test_existing_temporary_project_files_survive_manager_initialization(tmp_path):
    from project_manager import ProjectManager
    project = tmp_path / 'projects' / 'temp'
    project.mkdir(parents=True)
    marker = project / 'keep.txt'
    marker.write_text('user data', encoding='utf-8')
    manager = ProjectManager(str(tmp_path))
    assert manager.current_project == 'temp'
    assert marker.read_text(encoding='utf-8') == 'user data'


def test_chat_history_is_opt_in_and_can_be_persisted(tmp_path, monkeypatch):
    import json
    from project_manager import ProjectManager
    monkeypatch.delenv('ADA_PERSIST_CHAT_HISTORY', raising=False)
    manager = ProjectManager(str(tmp_path))
    manager.log_chat('User', 'private by default')
    history = tmp_path / 'projects' / 'temp' / 'chat_history.jsonl'
    assert not history.exists()

    monkeypatch.setenv('ADA_PERSIST_CHAT_HISTORY', '1')
    manager.log_chat('User', 'saved by explicit opt-in')
    entry = json.loads(history.read_text(encoding='utf-8').splitlines()[0])
    assert entry['text'] == 'saved by explicit opt-in'

def test_audioloop_honors_ada_project_root(monkeypatch, tmp_path):
    import project_manager
    class FakeProjectManager:
        def __init__(self, root):
            from pathlib import Path
            self.workspace_root = Path(root)
            self.current_project = 'temp'
    monkeypatch.setenv('ADA_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(project_manager, 'ProjectManager', FakeProjectManager)
    from ada import AudioLoop
    loop = AudioLoop(video_mode='none')
    assert loop.project_manager.workspace_root == tmp_path
