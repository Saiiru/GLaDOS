"""Startup policy checks without launching the application or devices."""
import ast
from pathlib import Path


def test_frontend_has_no_startup_discovery_or_microphone():
    source = Path('src/App.jsx').read_text()
    assert 'hasAutoConnectedRef' not in source
    assert "const [isConnected, setIsConnected] = useState(false)" in source
    assert 'if (socketConnected && isConnected && !isMuted)' in source
    assert "socket.emit('voice_utterance'" in source
    assert 'const toggleVideo' in source
    assert "socket.emit('discover_kasa'" in source


def test_server_startup_does_not_contact_devices():
    tree = ast.parse(Path('backend/server.py').read_text())
    startup = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'startup_event')
    assert not any(isinstance(n, ast.Attribute) and n.attr in {'initialize', 'discover_devices', 'discover_printers'}
                   for n in ast.walk(startup))


def test_cloud_sdk_removed_from_configuration():
    assert 'google-genai' not in Path('requirements.txt').read_text()
    assert 'GEMINI_API_KEY' not in Path('.env.example').read_text()
    for path in ['backend/ada.py', 'backend/cad_agent.py', 'backend/web_agent.py']:
        assert 'genai' not in Path(path).read_text()


def test_text_input_does_not_send_unsupported_frames_or_duplicate_logs():
    tree = ast.parse(Path('backend/server.py').read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'user_input')
    assert not any(isinstance(n, ast.Attribute) and n.attr in {'_latest_image_payload', 'log_chat'} for n in ast.walk(handler))


def test_browser_button_uses_preserved_handler():
    tree = ast.parse(Path('backend/server.py').read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'prompt_web_agent')
    assert any(isinstance(n, ast.Attribute) and n.attr == 'handle_web_agent_request' for n in ast.walk(handler))


def test_power_button_does_not_implicitly_enable_microphone():
    source = Path('src/App.jsx').read_text()
    toggle = source.split('const togglePower = () => {', 1)[1].split('const toggleMute', 1)[0]
    assert 'setIsMuted(false)' not in toggle
    assert 'muted: true' in toggle


def test_runtime_dependencies_avoid_duplicate_opencv_wheels():
    packages = {line.strip().split('=')[0].split('>')[0].lower()
                for line in Path('requirements.txt').read_text().splitlines()
                if line.strip() and not line.strip().startswith('#')}
    assert 'mediapipe' in packages
    assert 'opencv-python' not in packages
