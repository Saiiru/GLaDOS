"""Regression tests for independent security review findings."""
import asyncio
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


def audio_loop_class():
    tree = ast.parse(Path("backend/ada.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AudioLoop")
    scope = {"asyncio": asyncio, "os": os, "DEFAULT_MODE": "none",
             "make_function_response": lambda id, name, response: {"role": "tool", "tool_call_id": id, "content": __import__("json").dumps(response)}}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "backend/ada.py", "exec"), scope)
    return scope["AudioLoop"]


def test_false_tool_permission_does_not_bypass_confirmation():
    cls = audio_loop_class()
    async def run():
        loop = cls.__new__(cls)
        loop.permissions = {"add_task": False}
        loop.confirm_tool = AsyncMock(return_value=False)
        loop.task_agent = SimpleNamespace(add_task=lambda **kwargs: pytest.fail("must not run"))
        reply = await loop.handle_tool_calls([SimpleNamespace(id="x", name="add_task", args={"description": "unsafe"})])
        loop.confirm_tool.assert_awaited_once_with("add_task", {"description": "unsafe"})
        assert "denied" in reply[0]["content"].lower()
    import pytest
    asyncio.run(run())


def test_read_tools_reject_paths_outside_active_project(tmp_path):
    cls = audio_loop_class()
    async def run():
        root = tmp_path / "project"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("OUTSIDE-SECRET")
        loop = cls.__new__(cls)
        loop.project_manager = SimpleNamespace(get_current_project_path=lambda: root)
        loop.session = SimpleNamespace(send=AsyncMock())
        await loop.handle_read_file(str(secret))
        await loop.handle_read_directory(str(tmp_path))
        outputs = [c.kwargs["input"] for c in loop.session.send.await_args_list]
        assert all("OUTSIDE-SECRET" not in item and "secret.txt" not in item for item in outputs)
        assert all("outside" in item.lower() or "project" in item.lower() for item in outputs)
    asyncio.run(run())
