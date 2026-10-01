"""Offline CAD agent checks; design safety and export are covered in test_safe_cad.py."""


def test_cad_agent_uses_the_local_completion_client():
    from cad_agent import CadAgent
    from local_provider import LocalLLMClient
    agent = CadAgent()
    assert isinstance(agent.client, LocalLLMClient)
    assert callable(agent.generate_prototype)
    assert callable(agent.iterate_prototype)


def test_cad_agent_callbacks_are_optional_and_local():
    from cad_agent import CadAgent
    events = []
    agent = CadAgent(on_thought=events.append, on_status=events.append)
    assert agent.on_thought is not None
    assert agent.on_status is not None
    assert events == []
