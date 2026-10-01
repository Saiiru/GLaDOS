import importlib.util
from pathlib import Path


def load_auditor():
    path = Path(__file__).parents[1] / 'scripts' / 'audit_skill_coverage.py'
    spec = importlib.util.spec_from_file_location('audit_skill_coverage', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_distinguishes_registered_adapters_from_readme_only_skills(tmp_path, monkeypatch):
    root = tmp_path / 'skills'
    for name, body in [
        ('taskwarrior', '---\nname: taskwarrior\ndescription: Tasks\n---\nUse kora tasks.'),
        ('google-workspace', '---\nname: google-workspace\ndescription: Google\n---\nUse gws.'),
        ('sample-cli', '---\nname: sample-cli\ndescription: CLI\nprerequisites:\n  commands: [ada-test-command-that-does-not-exist]\n---\nUse it.'),
    ]:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(body)
    audit = load_auditor()
    rows = audit.audit(root)
    by_name = {row['name']: row for row in rows}
    assert by_name['taskwarrior']['coverage'] == 'adapter'
    assert 'add_task' in by_name['taskwarrior']['ada_tools']
    assert by_name['google-workspace']['coverage'] == 'guidance-only'
    assert by_name['sample-cli']['required_commands'] == 'ada-test-command-that-does-not-exist'
    assert by_name['sample-cli']['missing_commands'] == 'ada-test-command-that-does-not-exist'
    assert len(rows) == 3
