import asyncio
import json

from local_session import LocalSession, function_response


class FakeClient:
    def __init__(self, final_text):
        self.responses = [
            {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{
                'id': 'search-1', 'function': {'name': 'search_skills',
                'arguments': json.dumps({'query': 'audio routing'})}}]}}]},
            {'choices': [{'message': {'role': 'assistant', 'content': final_text}}]},
        ]
    async def create_chat_completion(self, messages, tools=None):
        return self.responses.pop(0)


def test_session_grounds_explicit_first_step_request_in_skill_result():
    async def handler(calls):
        return [function_response(call.id, call.name, {'skills': [{
            'name': 'audio-routing', 'first_step': 'Inspect sources with `pactl list short sources`.'
        }], 'count': 1}) for call in calls]
    events = []
    session = LocalSession(FakeClient('A skill was found for audio routing.'), [], handler, events.append)
    async def run():
        await session.send('Qual o primeiro diagnóstico seguro?', end_of_turn=True)
        await session.process_next()
    asyncio.run(run())
    answer = next(event['text'] for event in reversed(events) if event['sender'] == 'KORA')
    assert answer == 'Primeiro passo da skill `audio-routing`: Inspect sources with `pactl list short sources`.'


def test_local_session_bounds_skill_content_for_small_context_models():
    from types import SimpleNamespace
    from local_session import _bound_skill_tool_result
    payload = {"skill_guidance_untrusted": "x" * 5000}
    result = function_response("read-1", "read_skill", payload)

    _bound_skill_tool_result(SimpleNamespace(name="read_skill"), result)

    loaded = json.loads(result["content"])
    assert len(loaded["skill_guidance_untrusted"]) == 2400


def test_session_does_not_force_skill_step_into_a_nonprocedural_reply():
    async def handler(calls):
        return [function_response(call.id, call.name, {'skills': [{
            'name': 'audio-routing', 'first_step': 'Inspect sources.'
        }], 'count': 1}) for call in calls]
    events = []
    session = LocalSession(FakeClient('Encontrei a skill de roteamento de áudio.'), [], handler, events.append)
    async def run():
        await session.send('Qual skill fala de áudio?', end_of_turn=True)
        await session.process_next()
    asyncio.run(run())
    answer = next(event['text'] for event in reversed(events) if event['sender'] == 'KORA')
    assert answer == 'Encontrei a skill de roteamento de áudio.'
