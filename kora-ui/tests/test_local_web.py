import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_browser_action_requires_explicit_confirmation():
    from web_agent import WebAgent
    async def check():
        agent = WebAgent()
        agent.page = SimpleNamespace(goto=AsyncMock())
        call = SimpleNamespace(id="nav", name="navigate", args={"url": "https://example.com",
            "safety_decision": {"decision": "require_confirmation"}})
        result = await agent.execute_function_calls([call])
        agent.page.goto.assert_not_awaited()
        assert "denied" in result[0][2]["error"].lower()
        agent.confirm_action = AsyncMock(return_value=False)
        await agent.execute_function_calls([call])
        agent.page.goto.assert_not_awaited()
        agent.confirm_action = AsyncMock(return_value=True)
        await agent.execute_function_calls([call])
        agent.page.goto.assert_awaited_once_with("https://example.com")
    asyncio.run(check())


def test_no_automatic_browser_acknowledgement():
    from pathlib import Path
    assert 'Auto-acknowledging' not in Path('backend/web_agent.py').read_text()


def test_local_browser_loop_uses_text_observations_and_closes(monkeypatch):
    import sys
    from web_agent import WebAgent
    page = SimpleNamespace(url='about:blank', goto=AsyncMock(),
        evaluate=AsyncMock(return_value={'text': 'Example', 'elements': []}),
        screenshot=AsyncMock(return_value=b'png'))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        async def __aexit__(self, *args): pass
    monkeypatch.setitem(sys.modules, 'playwright', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'playwright.async_api', SimpleNamespace(async_playwright=Playwright))
    agent = WebAgent(confirm_action=AsyncMock(return_value=False))
    call = {'id': 'nav', 'type': 'function', 'function': {'name': 'navigate', 'arguments': '{"url":"https://example.com"}'}}
    agent.client.create_chat_completion = AsyncMock(side_effect=[
        {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [call]}}]},
        {'choices': [{'message': {'role': 'assistant', 'content': 'Navigation denied.'}}]},
    ])
    assert asyncio.run(agent.run_task('Visit example')) == 'Navigation denied.'
    page.goto.assert_not_awaited()
    browser.close.assert_awaited_once()
    kwargs = agent.client.create_chat_completion.call_args.kwargs
    assert any(t['function']['name'] == 'navigate' for t in kwargs['tools'])
    history = agent.client.create_chat_completion.call_args.args[0]
    assert any(m.get('tool_call_id') == 'nav' for m in history)
    assert all(isinstance(m.get('content'), str) for m in history)


def test_web_agent_adds_local_visual_summary_to_page_observation(monkeypatch):
    import sys
    from web_agent import WebAgent
    image=b'\xff\xd8\xff'+b'fake-jpeg'
    page=SimpleNamespace(url='http://local.test',
        evaluate=AsyncMock(return_value={'text':'Button label','elements':[]}),
        screenshot=AsyncMock(return_value=image))
    context=SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser=SimpleNamespace(new_context=AsyncMock(return_value=context),close=AsyncMock())
    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        async def __aexit__(self,*args):pass
    monkeypatch.setitem(sys.modules,'playwright',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'playwright.async_api',SimpleNamespace(async_playwright=Playwright))
    import copy
    vision=SimpleNamespace(analyze=AsyncMock(return_value='Um quadrado vermelho está ao centro.'))
    agent=WebAgent(confirm_action=AsyncMock(return_value=True),vision_service=vision)
    captured=[]
    async def completion(messages,tools=None):
        captured.append(copy.deepcopy(messages))
        return {'choices':[{'message':{'role':'assistant','content':'Vejo um quadrado vermelho.'}}]}
    agent.client.create_chat_completion=completion
    result=asyncio.run(agent.run_task('Descreva a página.'))
    assert result=='Vejo um quadrado vermelho.'
    vision.analyze.assert_awaited_once()
    frame,question=vision.analyze.await_args.args
    assert frame['mime_type']=='image/jpeg'
    assert frame['data']
    observation=captured[0][-1]['content']
    assert 'Um quadrado vermelho está ao centro.' in observation
    assert 'visual summaries' in captured[0][0]['content']


def test_web_agent_navigates_a_local_page_only_after_confirmation():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest.mock import AsyncMock
    from web_agent import WebAgent

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body=b'<title>ADA local</title><h1>ADA local browser smoke</h1>'
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/'
    agent=WebAgent(confirm_action=AsyncMock(return_value=True))
    call={'id':'local-nav','type':'function','function':{'name':'navigate','arguments':json.dumps({'url':url})}}
    requests=[]
    async def completion(messages,tools=None):
        requests.append(messages)
        if len(requests)==1:
            return {'choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]}}]}
        assert 'ADA local browser smoke' in messages[-1]['content']
        return {'choices':[{'message':{'role':'assistant','content':'A página local abriu corretamente.'}}]}
    agent.client.create_chat_completion=completion
    try:
        result=asyncio.run(agent.run_task('Abra a página local de teste.'))
        assert result=='A página local abriu corretamente.'
        agent.confirm_action.assert_awaited_once()
        assert len(requests)==2
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


