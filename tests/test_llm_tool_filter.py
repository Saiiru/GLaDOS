from typing import Any

from glados.core.llm_processor import LanguageModelProcessor


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


def _names(tools: list[dict[str, Any]]) -> set[str]:
    return {tool["function"]["name"] for tool in tools}


def test_system_stats_only_offer_system_info_tools() -> None:
    tools = [
        _tool("mcp.system_info.system_overview"),
        _tool("mcp.memory.memory_stats"),
        _tool("mcp.memory.search_memory"),
        _tool("slow clap"),
    ]

    filtered = LanguageModelProcessor._filter_tools_for_message(tools, "Give me the system stats")

    assert "mcp.system_info.system_overview" in _names(filtered)
    assert not any(name.startswith("mcp.memory.") for name in _names(filtered))


def test_computer_stats_phrase_routes_to_system_info() -> None:
    tools = [_tool("mcp.system_info.system_overview"), _tool("mcp.memory.memory_stats")]

    filtered = LanguageModelProcessor._filter_tools_for_message(tools, "Show me my computer stats")

    assert "mcp.system_info.system_overview" in _names(filtered)
    assert "mcp.memory.memory_stats" not in _names(filtered)


def test_memory_question_only_offers_memory_tools() -> None:
    tools = [_tool("mcp.system_info.system_overview"), _tool("mcp.memory.search_memory")]

    filtered = LanguageModelProcessor._filter_tools_for_message(tools, "What do you remember about me?")

    assert "mcp.memory.search_memory" in _names(filtered)
    assert "mcp.system_info.system_overview" not in _names(filtered)


def test_screen_question_only_offers_desktop_tools() -> None:
    tools = [
        _tool("mcp.desktop.screen_ocr"),
        _tool("mcp.desktop.desktop_action"),
        _tool("mcp.system_info.system_overview"),
        _tool("mcp.memory.search_memory"),
    ]

    filtered = LanguageModelProcessor._filter_tools_for_message(tools, "What's on my screen?")

    assert {"mcp.desktop.screen_ocr"}.issubset(_names(filtered))
    assert not any(name.startswith("mcp.system_info.") for name in _names(filtered))
    assert not any(name.startswith("mcp.memory.") for name in _names(filtered))


def test_open_browser_request_offers_desktop_action() -> None:
    tools = [_tool("mcp.desktop.desktop_action"), _tool("mcp.memory.search_memory")]

    filtered = LanguageModelProcessor._filter_tools_for_message(tools, "Open the browser")

    assert "mcp.desktop.desktop_action" in _names(filtered)
    assert "mcp.memory.search_memory" not in _names(filtered)
