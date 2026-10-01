"""Offline WebAgent checks; navigation is mocked in test_local_web.py."""


def test_web_agent_uses_local_completion_client_and_does_not_launch_browser_on_init():
    from local_provider import LocalLLMClient
    from web_agent import WebAgent
    agent = WebAgent()
    assert isinstance(agent.client, LocalLLMClient)
    assert agent.browser is None
    assert agent.page is None
    assert agent.context is None


def test_web_agent_accepts_confirmation_callback_without_invoking_it():
    from unittest.mock import AsyncMock
    from web_agent import WebAgent
    confirm = AsyncMock(return_value=False)
    agent = WebAgent(confirm_action=confirm)
    confirm.assert_not_awaited()
    assert agent.confirm_action is confirm
