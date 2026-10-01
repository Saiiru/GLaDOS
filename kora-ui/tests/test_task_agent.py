import json
from types import SimpleNamespace
import pytest


def fake_result(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_add_task_uses_kora_argv_and_returns_created_id():
    from backend.task_agent import TaskWarriorClient
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        return fake_result('Created task 12.\n', 'TASKRC override: /tmp/taskrc\n')
    client = TaskWarriorClient(runner=runner, env={'PATH': '/usr/bin'})
    result = client.add_task('Pagar a conta de luz', due_date='2026-10-05', priority='H')
    assert result == {'id': 12, 'description': 'Pagar a conta de luz', 'due_date': '2026-10-05', 'priority': 'H', 'status': 'pending'}
    assert calls == [['/home/sairu/.local/bin/kora', 'tasks', 'add', 'description:Pagar a conta de luz', 'due:2026-10-05', 'priority:H']]


def test_empty_optional_task_fields_are_ignored():
    from backend.task_agent import TaskWarriorClient
    calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        return fake_result('Created task 2.\n')
    client=TaskWarriorClient(runner=runner,env={'PATH':'/usr/bin'})
    result=client.add_task('Anotar ideias',due_date='',priority='')
    assert result['due_date'] is None and result['priority'] is None
    assert calls==[['/home/sairu/.local/bin/kora','tasks','add','description:Anotar ideias']]


def test_list_pending_parses_only_safe_task_fields():
    from backend.task_agent import TaskWarriorClient
    payload = [{'id': 3, 'uuid': 'uuid-3', 'description': 'Ligar para o dentista',
                'status': 'pending', 'due': '20261004T000000Z', 'priority': 'M', 'secret': 'not exposed'}]
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        return fake_result(json.dumps(payload))
    client = TaskWarriorClient(runner=runner, env={'PATH': '/usr/bin'})
    tasks = client.list_pending()
    assert tasks == [{'id': 3, 'uuid': 'uuid-3', 'description': 'Ligar para o dentista',
                      'status': 'pending', 'due': '20261004T000000Z', 'priority': 'M'}]
    assert calls == [['/home/sairu/.local/bin/kora', 'tasks', 'export', 'status:pending']]


def test_list_pending_is_bounded_to_twenty_items():
    from backend.task_agent import TaskWarriorClient
    payload = [{'id': i + 1, 'uuid': f'uuid-{i}', 'description': f'Tarefa {i}', 'status': 'pending'}
               for i in range(30)]
    client = TaskWarriorClient(
        runner=lambda *a, **k: fake_result(json.dumps(payload)), env={'PATH': '/usr/bin'})
    assert len(client.list_pending()) == 20


def test_completion_checks_exact_description_then_uses_kora_done():
    from backend.task_agent import TaskWarriorClient
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        if args[2] == 'export' and args[3] == '7':
            return fake_result(json.dumps([{'id': 7, 'uuid': 'uuid-7', 'description': 'Enviar relatório', 'status': 'pending'}]))
        if args[2] == 'export' and args[3] == 'status:completed':
            return fake_result(json.dumps([{'id': 0, 'uuid': 'uuid-7', 'description': 'Enviar relatório', 'status': 'completed'}]))
        return fake_result("Completed task 7 'Enviar relatório'.\nCompleted 1 task.\n")
    client = TaskWarriorClient(runner=runner, env={'PATH': '/usr/bin'})
    result = client.complete_task('7', 'Enviar relatório')
    assert result['status'] == 'completed'
    assert result['description'] == 'Enviar relatório'
    assert calls == [
        ['/home/sairu/.local/bin/kora', 'tasks', 'export', '7'],
        ['/home/sairu/.local/bin/kora', 'tasks', 'done', '7', 'rc.confirmation=off'],
        ['/home/sairu/.local/bin/kora', 'tasks', 'export', 'status:completed', 'uuid:uuid-7'],
    ]


def test_completion_description_mismatch_never_mutates_task():
    from backend.task_agent import TaskWarriorClient, TaskWarriorError
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        return fake_result(json.dumps([{'id': 7, 'description': 'Enviar relatório', 'status': 'pending'}]))
    client = TaskWarriorClient(runner=runner, env={'PATH': '/usr/bin'})
    with pytest.raises(TaskWarriorError, match='description'):
        client.complete_task('7', 'Apagar arquivos')
    assert len(calls) == 1


def test_task_inputs_reject_empty_control_text_bad_dates_and_invalid_ids():
    from backend.task_agent import TaskWarriorClient, TaskWarriorError
    client = TaskWarriorClient(runner=lambda *a, **k: fake_result(), env={'PATH': '/usr/bin'})
    with pytest.raises(TaskWarriorError): client.add_task('  ')
    with pytest.raises(TaskWarriorError): client.add_task('Tarefa\nTASKRC=/tmp/other')
    with pytest.raises(TaskWarriorError): client.add_task('Tarefa', due_date='next Friday')
    with pytest.raises(TaskWarriorError): client.add_task('Tarefa', priority='X')
    with pytest.raises(TaskWarriorError, match='Task ID'):
        client.complete_task('--help', 'task')
    with pytest.raises(TaskWarriorError, match='Task ID'):
        client.complete_task('12345678-1234-1234-1234-1234567890abcdef', 'task')
