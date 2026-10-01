"""Offline contracts for declarative CAD; no model or device access."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_cad_rejects_code_before_loading_geometry(tmp_path):
    from cad_agent import validate_design
    for design in ["import os; os.system('bad')", {"python": "print(1)"},
                   {"parts": [{"shape": "__import__", "args": []}]},
                   {"parts": [{"shape": "box", "size": [1, 2, float('nan')]}]},
                   {"parts": [{"shape": "box", "size": [True, 2, 3]}]},
                   {"parts": [{"shape": "box", "size": [1, 2, 3], "path": "/tmp/evil"}]}]:
        with pytest.raises(ValueError):
            validate_design(design)


def test_generate_and_iterate_allowlisted_geometry(tmp_path, monkeypatch):
    asyncio.run(asyncio.wait_for(_generate_and_iterate(tmp_path, monkeypatch), 3))


async def _generate_and_iterate(tmp_path, monkeypatch):
    from cad_agent import CadAgent
    async def inline(func, *args):
        return func(*args)
    monkeypatch.setattr(asyncio, "to_thread", inline)
    calls = []
    class Solid:
        def translate(self, xyz):
            calls.append(("translate", xyz))
            return self
        def __add__(self, other): return self
        def __sub__(self, other): return self
    def box(*size):
        calls.append(("box", size))
        return Solid()
    def export(part, path):
        from pathlib import Path
        Path(path).write_bytes(b"solid test\nendsolid test")
    monkeypatch.setitem(sys.modules, "build123d", SimpleNamespace(Box=box, Sphere=box, Cylinder=box, export_stl=export))
    agent = CadAgent()
    design = {"parts": [{"shape": "box", "size": [10, 20, 30]}]}
    agent.client.create_chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": json.dumps(design)}}]})
    result = await agent.generate_prototype("box", str(tmp_path))
    assert result["format"] == "stl"
    assert calls[0] == ("box", (10, 20, 30))
    assert (tmp_path / "current_design.json").exists()
    assert not list(tmp_path.glob("*.py"))
    assert await agent.iterate_prototype("make it taller", str(tmp_path))
    messages = agent.client.create_chat_completion.call_args.args[0]
    assert "Current design" in messages[-1]["content"]
    assert "30" in messages[-1]["content"]


def test_model_output_is_never_executed():
    import ast
    from pathlib import Path
    tree = ast.parse(Path("backend/cad_agent.py").read_text())
    calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert not any(c in {"exec", "eval", "subprocess.run"} for c in calls)
    assert "subprocess.run" not in Path("backend/cad_agent.py").read_text()


def test_malicious_completion_never_reaches_builder(tmp_path, monkeypatch):
    import cad_agent
    from unittest.mock import Mock
    agent = cad_agent.CadAgent()
    agent.client.create_chat_completion = AsyncMock(return_value={
        'choices': [{'message': {'content': '{"parts":[],"code":"__import__(\"os\").system(\"bad\")"}'}}]})
    builder = Mock(side_effect=AssertionError('must not run'))
    monkeypatch.setattr(cad_agent, 'build_design', builder)
    assert asyncio.run(agent.generate_prototype('model', str(tmp_path))) is None
    builder.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('part', [
    {'shape': 'box', 'size': [1, 2, 0]},
    {'shape': 'sphere', 'radius': float('inf')},
    {'shape': 'cylinder', 'dimensions': [1, 1001]},
    {'shape': 'sphere', 'radius': 2, 'position': [0, 0, 1001]},
    {'shape': 'sphere', 'radius': 2, 'operation': 'subtract'},
    {'shape': 'sphere', 'radius': 2, 'operation': 'execute'},
])
def test_plan_bounds(part):
    from cad_agent import validate_design
    with pytest.raises(ValueError):
        validate_design({'parts': [part]})
