import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent))
from test_review_security_regressions import audio_loop_class


def test_skill_search_and_read_are_read_only_and_do_not_prompt_by_default():
    loop_type = audio_loop_class()
    async def check():
        loop = loop_type.__new__(loop_type)
        loop.permissions = {}
        loop.confirm_tool = AsyncMock(side_effect=AssertionError('Read-only skill lookup should not prompt'))
        loop.skill_library = SimpleNamespace(
            search_with_guidance=lambda query: [{'name': 'audio-routing'}],
            read=lambda name: '# Skill instructions')
        calls = [SimpleNamespace(id='s', name='search_skills', args={'query': 'audio routing'}),
                 SimpleNamespace(id='r', name='read_skill', args={'name': 'audio-routing'})]
        replies = await loop.handle_tool_calls(calls)
        assert len(replies) == 2
        assert 'audio-routing' in replies[0]['content']
        assert 'Skill instructions' in replies[1]['content']
        loop.confirm_tool.assert_not_awaited()
    asyncio.run(check())


def test_codex_task_requires_confirmation_even_when_tool_permission_is_false(tmp_path):
    loop_type = audio_loop_class()
    async def check():
        loop = loop_type.__new__(loop_type)
        loop.permissions = {'run_codex_task': False}
        loop.confirm_tool = AsyncMock(return_value=False)
        loop.project_manager = SimpleNamespace(get_current_project_path=lambda: tmp_path)
        loop.codex_agent = SimpleNamespace(run=AsyncMock(side_effect=AssertionError('Codex must not start')))
        reply = await loop.handle_tool_calls([SimpleNamespace(
            id='codex-1', name='run_codex_task', args={'prompt': 'Change the code', 'skills': []})])
        loop.confirm_tool.assert_awaited_once_with(
            'run_codex_task', {'prompt': 'Change the code', 'skills': []})
        loop.codex_agent.run.assert_not_awaited()
        assert 'denied' in reply[0]['content'].lower()
    asyncio.run(check())


def test_codex_task_uses_active_project_and_only_selected_skills_after_confirmation(tmp_path):
    loop_type = audio_loop_class()
    async def check():
        loop = loop_type.__new__(loop_type)
        loop.permissions = {}
        loop.confirm_tool = AsyncMock(return_value=True)
        loop.project_manager = SimpleNamespace(get_current_project_path=lambda: tmp_path, current_project='demo')
        library = object()
        loop.skill_library = library
        codex = SimpleNamespace(run=AsyncMock(return_value='Updated and tested.'))
        loop.codex_agent = codex
        args = {'prompt': 'Fix the parser', 'skills': ['test-driven-development']}
        reply = await loop.handle_tool_calls([SimpleNamespace(id='codex-2', name='run_codex_task', args=args)])
        loop.confirm_tool.assert_awaited_once_with('run_codex_task', args)
        codex.run.assert_awaited_once_with('Fix the parser', tmp_path,
            skills=['test-driven-development'], skill_library=library)
        assert 'Updated and tested.' in reply[0]['content']
    asyncio.run(check())
