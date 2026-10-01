"""Safe adapter for the user's Kora/Taskwarrior source of truth."""
from datetime import date
import os
from pathlib import Path
import re
import shutil
import subprocess


class TaskWarriorError(RuntimeError):
    """A bounded Kora/Taskwarrior operation failed validation or execution."""


class TaskWarriorClient:
    MAX_DESCRIPTION = 240
    SAFE_FIELDS = ('id', 'uuid', 'description', 'status', 'due', 'priority')

    def __init__(self, command=None, runner=None, env=None, timeout=15):
        executable = shutil.which('kora') or str(Path.home() / '.local/bin/kora')
        self.command = list(command) if command is not None else [executable, 'tasks']
        self.runner = runner or subprocess.run
        self.timeout = timeout
        allowed = ('HOME', 'PATH', 'LANG', 'LC_ALL', 'KORA_REAL_HOME', 'KORA_HOME',
                   'KORA_CONFIG_HOME', 'TASKRC', 'TASKDATA')
        self.env = ({k: os.environ[k] for k in allowed if k in os.environ}
                    if env is None else dict(env))
        self.env.setdefault('HOME', str(Path.home()))
        self.env.setdefault('PATH', os.defpath)

    @staticmethod
    def _clean_text(value, field):
        if not isinstance(value, str):
            raise TaskWarriorError(f'{field} must be text.')
        value = value.strip()
        if not value or len(value) > TaskWarriorClient.MAX_DESCRIPTION or any(ord(c) < 32 for c in value):
            raise TaskWarriorError(f'{field} must be 1–240 characters without control characters.')
        return value

    @staticmethod
    def _validate_id(task_id):
        value = str(task_id).strip()
        uuid_pattern = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
        if not (re.fullmatch(r'[1-9][0-9]{0,9}', value) or re.fullmatch(uuid_pattern, value)):
            raise TaskWarriorError('Task ID must be a numeric ID or UUID.')
        return value

    def _run(self, *args):
        try:
            result = self.runner([*self.command, *args], capture_output=True, text=True,
                                 timeout=self.timeout, env=self.env, shell=False, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise TaskWarriorError('Kora Taskwarrior command failed or timed out.') from None
        if result.returncode != 0:
            raise TaskWarriorError('Kora Taskwarrior command failed; no success is assumed.')
        return result

    def _export(self, *query):
        result = self._run('export', *query)
        try:
            payload = __import__('json').loads(result.stdout)
        except (ValueError, TypeError):
            raise TaskWarriorError('Taskwarrior export was not valid JSON.') from None
        if not isinstance(payload, list):
            raise TaskWarriorError('Taskwarrior export did not return a task list.')
        return payload

    def list_pending(self):
        tasks = self._export('status:pending')
        safe = []
        for item in tasks[:20]:
            if not isinstance(item, dict):
                continue
            task = {key: item[key] for key in self.SAFE_FIELDS if key in item}
            if isinstance(task.get('description'), str):
                safe.append(task)
        return safe

    def add_task(self, description, due_date=None, priority=None):
        description = self._clean_text(description, 'Task description')
        if isinstance(due_date, str) and not due_date.strip():
            due_date = None
        if isinstance(priority, str) and not priority.strip():
            priority = None
        args = ['add', f'description:{description}']
        if due_date is not None:
            if not isinstance(due_date, str):
                raise TaskWarriorError('Due date must use YYYY-MM-DD.')
            try:
                parsed = date.fromisoformat(due_date)
            except ValueError:
                raise TaskWarriorError('Due date must use YYYY-MM-DD.') from None
            if parsed.isoformat() != due_date:
                raise TaskWarriorError('Due date must use YYYY-MM-DD.')
            args.append(f'due:{due_date}')
        if priority is not None:
            if priority not in ('H', 'M', 'L'):
                raise TaskWarriorError('Priority must be H, M, or L.')
            args.append(f'priority:{priority}')
        result = self._run(*args)
        output = f'{result.stdout}\n{result.stderr}'
        match = re.search(r'Created task ([1-9][0-9]*)\.', output)
        if not match:
            raise TaskWarriorError('Task command returned without a verifiable ID; check Taskwarrior before retrying.')
        return {'id': int(match.group(1)), 'description': description,
                'due_date': due_date, 'priority': priority, 'status': 'pending'}

    def complete_task(self, task_id, expected_description):
        task_id = self._validate_id(task_id)
        expected_description = self._clean_text(expected_description, 'Expected task description')
        matches = self._export(task_id)
        task = next((item for item in matches if str(item.get('id')) == task_id or
                     str(item.get('uuid', '')).lower() == task_id.lower()), None)
        if not task:
            raise TaskWarriorError('Task ID was not found; no task was changed.')
        if task.get('description') != expected_description:
            raise TaskWarriorError('Task description does not match; no task was changed.')
        if task.get('status') != 'pending':
            raise TaskWarriorError('Task is not pending; no task was changed.')
        task_uuid = task.get('uuid')
        if not isinstance(task_uuid, str) or not task_uuid:
            raise TaskWarriorError('Task UUID is missing; no task was changed.')
        self._run('done', task_id, 'rc.confirmation=off')
        verified = self._export('status:completed', f'uuid:{task_uuid}')
        final = next((item for item in verified
                      if str(item.get('uuid', '')).lower() == task_uuid.lower()), None)
        if not final or final.get('status') != 'completed' or final.get('description') != expected_description:
            raise TaskWarriorError('Task completion could not be verified.')
        return {'id': task.get('id'), 'uuid': task_uuid,
                'description': expected_description, 'status': 'completed'}
