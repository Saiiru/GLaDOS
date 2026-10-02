import sys
import asyncio

# Fix for asyncio subprocess support on Windows
# MUST BE SET BEFORE OTHER IMPORTS
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import socketio
import uvicorn
from fastapi import FastAPI
import asyncio
import threading
import sys
import os
import json
import hmac
from datetime import datetime
from pathlib import Path



# Load local runtime configuration before importing modules that read ADA_DATA_DIR.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

# Ensure we can import ada
from local_voice import VoiceBridge
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import ada
from local_voice import VoiceBridge
from authenticator import FaceAuthenticator
from kasa_agent import KasaAgent

SOCKET_TOKEN = os.environ.get("ADA_SOCKET_TOKEN")
UI_PORT = 17987
ALLOWED_SOCKET_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    f"http://127.0.0.1:{UI_PORT}",
]
# Never use wildcard CORS on a loopback control API. The packaged UI is served
# from 127.0.0.1:ADA_UI_PORT; Vite dev uses port 5173.
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=ALLOWED_SOCKET_ORIGINS, max_http_buffer_size=9 * 1024 * 1024)
voice = VoiceBridge(sio.emit)
app = FastAPI()
app_socketio = socketio.ASGIApp(sio, app)

import signal

# --- SHUTDOWN HANDLER ---
def signal_handler(sig, frame):
    print(f"\n[SERVER] Caught signal {sig}. Exiting gracefully...")
    # Clean up audio loop
    if audio_loop:
        try:
            print("[SERVER] Stopping Audio Loop...")
            audio_loop.stop() 
        except:
            pass
    try:
        asyncio.get_running_loop().create_task(shutdown(audio_owner_sid))
    except RuntimeError:
        raise SystemExit(0)

@app.on_event('shutdown')
async def stop_voice_worker():
    await voice.close()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Global state
audio_loop = None
loop_task = None
audio_owner_sid = None
authenticator = None
kasa_agent = KasaAgent()
SETTINGS_FILE = "settings.json"

DEFAULT_SETTINGS = {
    "face_auth_enabled": False, # Default OFF as requested
    "tool_permissions": {
        "generate_cad": True,
        "run_web_agent": True,
        "write_file": True,
        "read_directory": True,
        "read_file": True,
        "create_project": True,
        "switch_project": True,
        "list_projects": True,
        "list_tasks": False,
        "add_task": True,
        "complete_task": True,
        "analyze_camera": True
    },
    "printers": [], # List of {host, port, name, type}
    "kasa_devices": [], # List of {ip, alias, model}
    "camera_flipped": False # Invert cursor horizontal direction
}

SETTINGS = DEFAULT_SETTINGS.copy()

def load_settings():
    global SETTINGS
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults to ensure new keys exist
                # Deep merge for tool_permissions would be better but shallow merge of top keys + tool_permissions check is okay for now
                for k, v in loaded.items():
                    if k == "tool_permissions" and isinstance(v, dict):
                         SETTINGS["tool_permissions"].update(v)
                    else:
                        SETTINGS[k] = v
            print(f"Loaded settings: {SETTINGS}")
        except Exception as e:
            print(f"Error loading settings: {e}")

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(SETTINGS, f, indent=4)
        print("Settings saved.")
    except Exception as e:
        print(f"Error saving settings: {e}")

# Load on startup
load_settings()

authenticator = None
auth_sid = None
kasa_agent = KasaAgent(known_devices=SETTINGS.get("kasa_devices"))
# tool_permissions is now SETTINGS["tool_permissions"]

async def _auth_status_changed(is_authenticated):
    if auth_sid:
        await sio.emit('auth_status', {'authenticated': is_authenticated}, room=auth_sid)

async def _auth_frame_ready(frame_b64):
    if auth_sid:
        await sio.emit('auth_frame', {'image': frame_b64}, room=auth_sid)

def _ensure_authenticator(sid):
    global authenticator, auth_sid
    if authenticator is not None and auth_sid and auth_sid != sid and getattr(authenticator, 'running', False):
        authenticator.stop()
    auth_sid = sid
    if authenticator is None:
        reference = os.path.expanduser(os.environ.get('ADA_FACE_REFERENCE', 'reference.jpg'))
        authenticator = FaceAuthenticator(
            reference_image_path=reference,
            on_status_change=_auth_status_changed,
            on_frame=_auth_frame_ready,
        )
    return authenticator

def _stop_authenticator(sid=None):
    global auth_sid
    if authenticator is not None and (sid is None or auth_sid == sid):
        authenticator.stop()
        auth_sid = None

@app.on_event("startup")
async def startup_event():
    import sys
    print(f"[SERVER DEBUG] Startup Event Triggered")
    print(f"[SERVER DEBUG] Python Version: {sys.version}")
    try:
        loop = asyncio.get_running_loop()
        print(f"[SERVER DEBUG] Running Loop: {type(loop)}")
        policy = asyncio.get_event_loop_policy()
        print(f"[SERVER DEBUG] Current Policy: {type(policy)}")
    except Exception as e:
        print(f"[SERVER DEBUG] Error checking loop: {e}")

    print("[SERVER] Ready. Device discovery is user-triggered.")
    ready_nonce = os.environ.get("ADA_READY_NONCE", "")
    if 16 <= len(ready_nonce) <= 128 and ready_nonce.isalnum():
        print(f"ADA_BACKEND_READY:{ready_nonce}", flush=True)

@app.get("/status")
async def status():
    return {"status": "running", "service": "KORA Backend"}

@sio.event
async def connect(sid, environ, auth=None):
    global audio_owner_sid
    supplied_token = auth.get('token') if isinstance(auth, dict) else None
    if not SOCKET_TOKEN or not isinstance(supplied_token, str) or not hmac.compare_digest(supplied_token, SOCKET_TOKEN):
        return False
    if audio_owner_sid is not None:
        return False
    audio_owner_sid = sid
    print(f"Client connected: {sid}")
    await sio.emit('status', {'msg': 'Connected to KORA Backend'}, room=sid)

    if not SETTINGS.get("face_auth_enabled", False):
        _stop_authenticator()
        await sio.emit('auth_status', {'authenticated': True}, room=sid)
        return

    auth_instance = _ensure_authenticator(sid)
    if auth_instance.authenticated:
        await sio.emit('auth_status', {'authenticated': True}, room=sid)
    else:
        await sio.emit('auth_status', {'authenticated': False}, room=sid)
        if not getattr(auth_instance, 'running', False):
            asyncio.create_task(auth_instance.start_authentication_loop())

@sio.event
async def disconnect(sid, reason=None):
    global audio_loop, loop_task, audio_owner_sid
    if audio_owner_sid == sid:
        owned_loop = audio_loop
        owned_task = loop_task
        audio_loop = None
        loop_task = None
        try:
            if owned_loop:
                try:
                    owned_loop.clear_frame()
                except Exception:
                    print("Frame cleanup failed; details withheld")
                try:
                    await owned_loop.vision_service.close()
                except Exception:
                    print("VLM teardown failed; details withheld")
                try:
                    owned_loop.stop()
                except Exception:
                    print("Audio teardown failed; details withheld")
            if owned_task and not owned_task.done():
                owned_task.cancel()
            try:
                await voice.close()
            except Exception:
                print("Voice teardown failed; details withheld")
            voice.disconnect(sid)
            _stop_authenticator(sid)
        finally:
            # Keep ownership reserved until all session resources are torn down.
            audio_owner_sid = None
    else:
        voice.disconnect(sid)
        _stop_authenticator(sid)
    print(f"Client disconnected: {sid}")


@sio.event
async def start_audio(sid, data=None):
    global audio_loop, loop_task, audio_owner_sid
    if sid != audio_owner_sid:
        return
    
    # Optional: Block if not authenticated
    # Only block if auth is ENABLED and not authenticated
    if SETTINGS.get("face_auth_enabled", False):
        if authenticator and not authenticator.authenticated:
            print("Blocked start_audio: Not authenticated.")
            await sio.emit('error', {'msg': 'Authentication Required'})
            return

    print("Starting Audio Loop...")
    voice.enable_speaker(sid)
    
    device_index = None
    device_name = None
    if data:
        if 'device_index' in data:
            device_index = data['device_index']
        if 'device_name' in data:
            device_name = data['device_name']
            
    print(f"Using input device: Name='{device_name}', Index={device_index}")
    
    if audio_loop:
        if loop_task and (loop_task.done() or loop_task.cancelled()):
             print("Audio loop task appeared finished/cancelled. Clearing and restarting...")
             audio_loop = None
             loop_task = None
        else:
             print("Audio loop already running. Re-connecting client to session.")
             await sio.emit('status', {'msg': 'KORA Already Running'})
             return


    # Callback to send audio data to frontend
    def on_audio_data(data_bytes):
        # We need to schedule this on the event loop
        # This is high frequency, so we might want to downsample or batch if it's too much
        asyncio.create_task(sio.emit('audio_data', {'data': list(data_bytes)}, room=sid))

    # Callback to send CAL data to frontend
    def on_cad_data(data):
        info = f"{len(data.get('vertices', []))} vertices" if 'vertices' in data else f"{len(data.get('data', ''))} bytes (STL)"
        print(f"Sending CAD data to frontend: {info}")
        asyncio.create_task(sio.emit('cad_data', data, room=sid))

    # Callback to send Browser data to frontend
    def on_web_data(data):
        print(f"Sending Browser data to frontend: {len(data.get('log', ''))} chars logs")
        asyncio.create_task(sio.emit('browser_frame', data, room=sid))
        
    # Callback to send Transcription data to frontend
    def on_transcription(data):
        # data = {"sender": "User"|"ADA", "text": "..."}
        asyncio.create_task(sio.emit('transcription', data, room=sid))
        # KORA is the canonical assistant sender; ADA remains a legacy compatibility value.
        if data.get('sender') in {'KORA', 'ADA'} and data.get('text'):
            voice.schedule_reply(data['text'])

    # Callback to send Confirmation Request to frontend
    def on_tool_confirmation(data):
        # data = {"id": "uuid", "tool": "tool_name", "args": {...}}
        print(f"Requesting confirmation for tool: {data.get('tool')}")
        asyncio.create_task(sio.emit('tool_confirmation_request', data, room=sid))

    # Callback to send CAD status to frontend
    def on_cad_status(status):
        # status can be: 
        # - a string like "generating" (from ada.py handle_cad_request)
        # - a dict with {status, attempt, max_attempts, error} (from CadAgent)
        if isinstance(status, dict):
            print(f"Sending CAD Status: {status.get('status')} (attempt {status.get('attempt')}/{status.get('max_attempts')})")
            asyncio.create_task(sio.emit('cad_status', status, room=sid))
        else:
            # Legacy: simple string
            print(f"Sending CAD Status: {status}")
            asyncio.create_task(sio.emit('cad_status', {'status': status}, room=sid))

    # Callback to send CAD thoughts to frontend (streaming)
    def on_cad_thought(thought_text):
        asyncio.create_task(sio.emit('cad_thought', {'text': thought_text}, room=sid))

    # Callback to send Project Update to frontend
    def on_project_update(project_name):
        print(f"Sending Project Update: {project_name}")
        asyncio.create_task(sio.emit('project_update', {'project': project_name}, room=sid))

    # Callback to send Device Update to frontend
    def on_device_update(devices):
        # devices is a list of dicts
        print(f"Sending Kasa Device Update: {len(devices)} devices")
        asyncio.create_task(sio.emit('kasa_devices', devices, room=sid))

    # Callback to send Error to frontend
    def on_error(msg):
        print("Forwarding backend error notice to the active session")
        asyncio.create_task(sio.emit('error', {'msg': msg}, room=sid))

    # Initialize ADA
    try:
        print(f"Initializing AudioLoop with device_index={device_index}")
        audio_loop = ada.AudioLoop(
            video_mode="none", 
            on_audio_data=on_audio_data,
            on_cad_data=on_cad_data,
            on_web_data=on_web_data,
            on_transcription=on_transcription,
            on_tool_confirmation=on_tool_confirmation,
            on_cad_status=on_cad_status,
            on_cad_thought=on_cad_thought,
            on_project_update=on_project_update,
            on_device_update=on_device_update,
            on_error=on_error,

            input_device_index=device_index,
            input_device_name=device_name,
            kasa_agent=kasa_agent
        )
        audio_owner_sid = sid
        print("AudioLoop initialized successfully.")

        # Apply current permissions
        audio_loop.update_permissions(SETTINGS["tool_permissions"])
        
        # Check initial mute state
        if data and data.get('muted', False):
            print("Starting with Audio Paused")
            audio_loop.set_paused(True)

        print("Creating asyncio task for AudioLoop.run()")
        loop_task = asyncio.create_task(audio_loop.run())
        
        # Add a done callback to catch silent failures in the loop
        def handle_loop_exit(task):
            try:
                task.result()
            except asyncio.CancelledError:
                print("Audio Loop Cancelled")
            except Exception:
                print("Audio Loop exited with an error; details withheld")
        
        loop_task.add_done_callback(handle_loop_exit)
        
        print("Emitting 'KORA Started'")
        await sio.emit('status', {'msg': 'KORA Started'}, room=sid)

        # Load saved printers
        saved_printers = SETTINGS.get("printers", [])
        if saved_printers and audio_loop.printer_agent:
            print(f"[SERVER] Loading {len(saved_printers)} saved printers...")
            for p in saved_printers:
                audio_loop.printer_agent.add_printer_manually(
                    name=p.get("name", p["host"]),
                    host=p["host"],
                    port=p.get("port", 80),
                    printer_type=p.get("type", "moonraker"),
                    camera_url=p.get("camera_url")
                )
        
        # Start Printer Monitor
        asyncio.create_task(monitor_printers_loop(sid))
        
    except Exception as e:
        print("CRITICAL ERROR STARTING ADA; details withheld from renderer")
        audio_loop = None
        await sio.emit('error', {'msg': 'Failed to start ADA; see local backend logs.'}, room=sid)


async def monitor_printers_loop(owner_sid):
    """Poll printers for the active session only; never outlive its owner."""
    print("[SERVER] Starting Printer Monitor Loop")
    while audio_loop and audio_loop.printer_agent and audio_owner_sid == owner_sid:
        try:
            agent = audio_loop.printer_agent
            if not agent.printers:
                await asyncio.sleep(5)
                continue
                
            tasks = []
            for host, printer in agent.printers.items():
                if printer.printer_type.value != "unknown":
                    tasks.append(agent.get_print_status(host))
            
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if audio_owner_sid != owner_sid:
                        break
                    to_dict = getattr(res, 'to_dict', None)
                    if callable(to_dict):
                        await sio.emit('print_status_update', to_dict(), room=owner_sid)
                        
        except asyncio.CancelledError:
            print("[SERVER] Printer Monitor Cancelled")
            break
        except Exception:
            print("Printer monitor failed; details withheld")
            
        await asyncio.sleep(2) # Update every 2 seconds for responsiveness

@sio.event
async def stop_audio(sid):
    global audio_loop, loop_task, audio_owner_sid
    if sid != audio_owner_sid:
        return
    await voice.close()
    if audio_loop:
        await audio_loop.vision_service.close()
        audio_loop.stop() 
        print("Stopping Audio Loop")
        audio_loop = None
        if loop_task and not loop_task.done():
            loop_task.cancel()
        loop_task = None
        await sio.emit('status', {'msg': 'KORA Stopped'}, room=sid)

@sio.event
async def pause_audio(sid):
    if sid != audio_owner_sid:
        return
    voice.disable_input(sid)
    global audio_loop
    if audio_loop:
        audio_loop.set_paused(True)
        print("Pausing Audio")
        await sio.emit('status', {'msg': 'Audio Paused'}, room=sid)

@sio.event
async def resume_audio(sid):
    if sid != audio_owner_sid:
        return
    global audio_loop
    if audio_loop:
        voice.enable_input(sid)
        audio_loop.set_paused(False)
        print("Resuming Audio")
        await sio.emit('status', {'msg': 'Audio Resumed'}, room=sid)

@sio.event
async def set_speaker(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return
    enabled = data['enabled']
    if enabled:
        voice.enable_speaker(sid)
    else:
        voice.disable_speaker(sid)
    await sio.emit('speaker_status', {'enabled': enabled}, room=sid)


@sio.event
async def confirm_tool(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # data: { "id": "...", "confirmed": True/False }
    request_id = data.get('id')
    confirmed = data.get('confirmed', False)
    if not isinstance(confirmed, bool):
        return
    
    print("[SERVER DEBUG] Received a confirmation response from the session owner.")
    
    if audio_loop:
        audio_loop.resolve_tool_confirmation(request_id, confirmed)

@sio.event
async def shutdown(sid, data=None):
    """Gracefully shutdown the server when the owning frontend closes."""
    global audio_loop, loop_task, authenticator, audio_owner_sid
    if sid != audio_owner_sid:
        return
    
    print("[SERVER] ========================================")
    print("[SERVER] SHUTDOWN SIGNAL RECEIVED FROM FRONTEND")
    print("[SERVER] ========================================")
    
    await voice.close()

    # Stop audio loop
    if audio_loop:
        print("[SERVER] Stopping Audio Loop...")
        audio_loop.clear_frame()
        await audio_loop.vision_service.close()
        audio_loop.stop()
        audio_loop = None
    
    # Cancel the loop task if running
    if loop_task and not loop_task.done():
        print("[SERVER] Cancelling loop task...")
        loop_task.cancel()
        loop_task = None
    
    # Stop authenticator if running
    if authenticator:
        print("[SERVER] Stopping Authenticator...")
        authenticator.stop()
    
    print("[SERVER] Graceful shutdown complete. Terminating process...")
    
    # Force exit immediately - os._exit bypasses cleanup but ensures termination
    audio_owner_sid = None
    os._exit(0)

@sio.event
async def voice_utterance(sid, data):
    if sid == audio_owner_sid and audio_loop and audio_loop.session:
        await voice.utterance(sid, data, audio_loop.session)

@sio.event
async def user_input(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    text = data.get('text')
    
    if not audio_loop:
        print("[SERVER DEBUG] [Error] Audio loop is None. Cannot send text.")
        return

    if not audio_loop.session:
        print("[SERVER DEBUG] [Error] Session is None. Cannot send text.")
        return

    if text:
        
        await audio_loop.session.send(input=text, end_of_turn=True)
        print(f"[SERVER DEBUG] Message sent to model successfully.")

import json
from datetime import datetime
from pathlib import Path

# ... (imports)

@sio.event
async def video_frame(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    image_data = data.get('image')
    if image_data and audio_loop:
        await audio_loop.send_frame(image_data)

@sio.event
async def clear_video_frame(sid):
    if sid == audio_owner_sid and audio_loop:
        audio_loop.clear_frame()

@sio.event
async def save_memory(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    try:
        messages = data.get('messages', [])
        if not messages:
            print("No messages to save.")
            return

        # Ensure directory exists
        memory_dir = Path("long_term_memory")
        memory_dir.mkdir(exist_ok=True)

        # Generate filename
        # Use provided filename if available, else timestamp
        provided_name = data.get('filename')
        
        if provided_name:
            # Simple sanitization
            if not provided_name.endswith('.txt'):
                provided_name += '.txt'
            # Prevent directory traversal
            filename = memory_dir / Path(provided_name).name 
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = memory_dir / f"memory_{timestamp}.txt"

        # Write to file
        with open(filename, 'w', encoding='utf-8') as f:
            for msg in messages:
                sender = msg.get('sender', 'Unknown')
                text = msg.get('text', '')
        print(f"Conversation saved to {filename}")
        await sio.emit('status', {'msg': 'Memory Saved Successfully'})

    except Exception as e:
        print(f"Error saving memory: {e}")
        await sio.emit('error', {'msg': f"Failed to save memory: {str(e)}"})

@sio.event
async def upload_memory(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    print("Received memory upload request")
    try:
        memory_text = data.get('memory', '')
        if not memory_text:
            print("No memory data provided.")
            return

        if not audio_loop:
             print("[SERVER DEBUG] [Error] Audio loop is None. Cannot load memory.")
             await sio.emit('error', {'msg': "System not ready (Audio Loop inactive)"})
             return
        
        if not audio_loop.session:
             print("[SERVER DEBUG] [Error] Session is None. Cannot load memory.")
             await sio.emit('error', {'msg': "System not ready (No active session)"})
             return

        # Send to model
        print("Sending memory context to model...")
        context_msg = f"System Notification: The user has uploaded a long-term memory file. Please load the following context into your understanding. The format is a text log of previous conversations:\n\n{memory_text}"
        
        await audio_loop.session.send(input=context_msg, end_of_turn=True)
        print("Memory context sent successfully.")
        await sio.emit('status', {'msg': 'Memory Loaded into Context'})

    except Exception as e:
        print(f"Error uploading memory: {e}")
        await sio.emit('error', {'msg': f"Failed to upload memory: {str(e)}"})

@sio.event
async def discover_kasa(sid):
    if sid != audio_owner_sid:
        return
    print("Received discover_kasa request")
    try:
        devices = await kasa_agent.discover_devices()
        await sio.emit('kasa_devices', devices, room=sid)
        await sio.emit('status', {'msg': f"Found {len(devices)} Kasa devices"}, room=sid)
        
        # Save to settings
        # devices is a list of full device info dicts. minimizing for storage.
        saved_devices = []
        for d in devices:
            saved_devices.append({
                "ip": d["ip"],
                "alias": d["alias"],
                "model": d["model"]
            })
        
        # Merge with existing to preserve any manual overrides? 
        # For now, just overwrite with latest scan result + previously known if we want to be fancy,
        # but user asked for "Any new devices that are scanned are added there".
        # A simple full persistence of current state is safest.
        SETTINGS["kasa_devices"] = saved_devices
        save_settings()
        print(f"[SERVER] Saved {len(saved_devices)} Kasa devices to settings.")
        
    except Exception as e:
        print("Kasa discovery failed; details withheld")
        await sio.emit('error', {'msg': 'Kasa discovery failed; see local backend logs.'}, room=sid)

@sio.event
async def iterate_cad(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # data: { prompt: "make it bigger" }
    prompt = data.get('prompt')
    print("Received CAD iteration request.")
    
    if not audio_loop or not audio_loop.cad_agent:
        await sio.emit('error', {'msg': "CAD Agent not available"})
        return

    try:
        # Notify user work has started
        await sio.emit('status', {'msg': 'Iterating design...'})
        await sio.emit('cad_status', {'status': 'generating'})
        
        # Call the agent with project path
        cad_output_dir = str(audio_loop.project_manager.get_current_project_path() / "cad")
        result = await audio_loop.cad_agent.iterate_prototype(prompt, output_dir=cad_output_dir)
        
        if result:
            info = f"{len(result.get('data', ''))} bytes (STL)"
            print(f"Sending updated CAD data: {info}")
            await sio.emit('cad_data', result, room=sid)
            # Save to Project
            if 'file_path' in result:
                saved_path = audio_loop.project_manager.save_cad_artifact(result['file_path'], prompt)
                if saved_path:
                    print("[SERVER] CAD iteration artifact saved.")

            await sio.emit('status', {'msg': 'Design updated'})
        else:
            await sio.emit('error', {'msg': 'Failed to update design'})
            
    except Exception as e:
        print(f"Error iterating CAD: {e}")
        await sio.emit('error', {'msg': f"Iteration Error: {str(e)}"})

@sio.event
async def generate_cad(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # data: { prompt: "make a cube" }
    prompt = data.get('prompt')
    print("Received CAD generation request.")
    
    if not audio_loop or not audio_loop.cad_agent:
        await sio.emit('error', {'msg': "CAD Agent not available"})
        return

    try:
        await sio.emit('status', {'msg': 'Generating new design...'})
        await sio.emit('cad_status', {'status': 'generating'})
        
        # Use generate_prototype based on prompt with project path
        cad_output_dir = str(audio_loop.project_manager.get_current_project_path() / "cad")
        result = await audio_loop.cad_agent.generate_prototype(prompt, output_dir=cad_output_dir)
        
        if result:
            info = f"{len(result.get('data', ''))} bytes (STL)"
            print(f"Sending newly generated CAD data: {info}")
            await sio.emit('cad_data', result, room=sid)


            # Save to Project
            if 'file_path' in result:
                saved_path = audio_loop.project_manager.save_cad_artifact(result['file_path'], prompt)
                if saved_path:
                    print("[SERVER] CAD artifact saved.")

            await sio.emit('status', {'msg': 'Design generated'})
        else:
            await sio.emit('error', {'msg': 'Failed to generate design'})
            
    except Exception as e:
        print(f"Error generating CAD: {e}")
        await sio.emit('error', {'msg': f"Generation Error: {str(e)}"})

@sio.event
async def prompt_web_agent(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # data: { prompt: "find xyz" }
    prompt = data.get('prompt')
    print("Received confirmed WebAgent request.")
    
    if not audio_loop or not audio_loop.web_agent:
        await sio.emit('error', {'msg': "Web Agent not available"})
        return

    try:
        await sio.emit('status', {'msg': 'Web Agent running...'})
        
        await audio_loop.handle_web_agent_request(prompt)

        await sio.emit('status', {'msg': 'Web Agent finished'})
        
    except Exception as e:
        print(f"Error running Web Agent: {e}")
        await sio.emit('error', {'msg': f"Web Agent Error: {str(e)}"})

@sio.event
async def discover_printers(sid):
    if sid != audio_owner_sid:
        return
    print("Received discover_printers request")
    
    # If audio_loop isn't ready yet, return saved printers from settings
    if not audio_loop or not audio_loop.printer_agent:
        saved_printers = SETTINGS.get("printers", [])
        if saved_printers:
            # Convert saved printers to the expected format
            printer_list = []
            for p in saved_printers:
                printer_list.append({
                    "name": p.get("name", p["host"]),
                    "host": p["host"],
                    "port": p.get("port", 80),
                    "printer_type": p.get("type", "unknown"),
                    "camera_url": p.get("camera_url")
                })
            print(f"[SERVER] Returning {len(printer_list)} saved printers (audio_loop not ready)")
            await sio.emit('printer_list', printer_list)
            return
        else:
            await sio.emit('printer_list', [])
            await sio.emit('status', {'msg': "Connect to KORA to enable printer discovery"})
            return
        
    try:
        printers = await audio_loop.printer_agent.discover_printers()
        await sio.emit('printer_list', printers)
        await sio.emit('status', {'msg': f"Found {len(printers)} printers"})
    except Exception as e:
        print(f"Error discovering printers: {e}")
        await sio.emit('error', {'msg': f"Printer Discovery Failed: {str(e)}"})

@sio.event
async def add_printer(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # data: { host: "192.168.1.50", name: "My Printer", type: "moonraker" }
    raw_host = data.get('host')
    name = data.get('name') or raw_host
    ptype = data.get('type', "moonraker")
    
    # Parse port if present
    if ":" in raw_host:
        host, port_str = raw_host.split(":")
        port = int(port_str)
    else:
        host = raw_host
        port = 80
    
    print(f"Received add_printer request: {host}:{port} ({ptype})")
    
    if not audio_loop or not audio_loop.printer_agent:
        await sio.emit('error', {'msg': "Printer Agent not available"})
        return
        
    try:
        # Add manually
        camera_url = data.get('camera_url')
        printer = audio_loop.printer_agent.add_printer_manually(name, host, port=port, printer_type=ptype, camera_url=camera_url)
        
        # Save to settings
        new_printer_config = {
            "name": name,
            "host": host,
            "port": port,
            "type": ptype,
            "camera_url": camera_url
        }
        
        # Check if already exists to avoid duplicates
        exists = False
        for p in SETTINGS.get("printers", []):
            if p["host"] == host and p["port"] == port:
                exists = True
                break
        
        if not exists:
            if "printers" not in SETTINGS:
                SETTINGS["printers"] = []
            SETTINGS["printers"].append(new_printer_config)
            save_settings()
            print(f"[SERVER] Saved printer {name} to settings.")
        
        # Probe to confirm/correct type
        print(f"Probing {host} to confirm type...")
        # Try port 7125 (Moonraker) and 4408 (Fluidd/K1) 
        ports_to_try = [80, 7125, 4408]
        
        actual_type = "unknown"
        for port in ports_to_try:
             found_type = await audio_loop.printer_agent._probe_printer_type(host, port)
             if found_type.value != "unknown":
                 actual_type = found_type
                 # Update port if different
                 if port != 80:
                     printer.port = port
                 break
        
        if actual_type != "unknown" and actual_type != printer.printer_type:
             printer.printer_type = actual_type
             print(f"Corrected type to {actual_type.value} on port {printer.port}")
             
        # Refresh list for everyone
        printers = [p.to_dict() for p in audio_loop.printer_agent.printers.values()]
        await sio.emit('printer_list', printers)
        await sio.emit('status', {'msg': f"Added printer: {name}"})
        
    except Exception as e:
        print(f"Error adding printer: {e}")
        await sio.emit('error', {'msg': f"Failed to add printer: {str(e)}"})

@sio.event
async def print_stl(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    print("Received a print request from the active frontend.")
    # data: { stl_path: "path/to.stl" | "current", printer: "name_or_ip", profile: "optional" }
    
    if not audio_loop or not audio_loop.printer_agent:
        await sio.emit('error', {'msg': "Printer Agent not available"})
        return
        
    try:
        stl_path = data.get('stl_path', 'current')
        printer_name = data.get('printer')
        profile = data.get('profile')
        
        if not printer_name:
             await sio.emit('error', {'msg': "No printer specified"})
             return
             
        await sio.emit('status', {'msg': f"Preparing print for {printer_name}..."})
        
        # Get current project path for resolution
        current_project_path = None
        if audio_loop and audio_loop.project_manager:
            current_project_path = str(audio_loop.project_manager.get_current_project_path())
            print("[SERVER DEBUG] Resolving current project path.")

        # Resolve STL path before slicing so we can preview it
        resolved_stl = audio_loop.printer_agent._resolve_file_path(stl_path, current_project_path)
        
        if resolved_stl and os.path.exists(resolved_stl):
            # Open the STL in the CAD module for preview
            try:
                import base64
                with open(resolved_stl, 'rb') as f:
                    stl_data = f.read()
                stl_b64 = base64.b64encode(stl_data).decode('utf-8')
                stl_filename = os.path.basename(resolved_stl)
                
                print(f"[SERVER] Opening STL in CAD module: {stl_filename}")
                await sio.emit('cad_data', {
                    'format': 'stl',
                    'data': stl_b64,
                    'filename': stl_filename
                })
            except Exception as e:
                print(f"[SERVER] Warning: Could not preview STL: {e}")
        
        # Progress Callback
        async def on_slicing_progress(percent, message):
            await sio.emit('slicing_progress', {
                'printer': printer_name,
                'percent': percent,
                'message': message
            })
            if percent < 100:
                 await sio.emit('status', {'msg': f"Slicing: {percent}%"})

        result = await audio_loop.printer_agent.print_stl(
            stl_path, 
            printer_name, 
            profile,
            progress_callback=on_slicing_progress,
            root_path=current_project_path
        )
        
        await sio.emit('print_result', result, room=sid)
        await sio.emit('status', {'msg': f"Print Job: {result.get('status', 'unknown')}"})
        
    except Exception as e:
        print(f"Error printing STL: {e}")
        await sio.emit('error', {'msg': f"Print Failed: {str(e)}"})

@sio.event
async def get_slicer_profiles(sid):
    """Get available OrcaSlicer profiles for manual selection."""
    if sid != audio_owner_sid:
        return
    print("Received get_slicer_profiles request")
    if not audio_loop or not audio_loop.printer_agent:
        await sio.emit('error', {'msg': "Printer Agent not available"})
        return
    
    try:
        profiles = audio_loop.printer_agent.get_available_profiles()
        await sio.emit('slicer_profiles', profiles)
    except Exception as e:
        print(f"Error getting slicer profiles: {e}")
        await sio.emit('error', {'msg': f"Failed to get profiles: {str(e)}"})

@sio.event
async def control_kasa(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # data: { ip, action: "on"|"off"|"brightness"|"color"|..., value: ... }
    ip = data.get('ip')
    action = data.get('action')
    print(f"Kasa Control: {ip} -> {action}")
    
    try:
        success = False
        if action == "on":
            success = await kasa_agent.turn_on(ip)
        elif action == "off":
            success = await kasa_agent.turn_off(ip)
        elif action == "brightness":
            val = data.get('value')
            success = await kasa_agent.set_brightness(ip, val)
        elif action == "color":
            # value is {h, s, v} - convert to tuple for set_color
            h = data.get('value', {}).get('h', 0)
            s = data.get('value', {}).get('s', 100)
            v = data.get('value', {}).get('v', 100)
            success = await kasa_agent.set_color(ip, (h, s, v))
        
        if success:
            await sio.emit('kasa_update', {
                'ip': ip,
                'is_on': True if action == "on" else (False if action == "off" else None),
                'brightness': data.get('value') if action == "brightness" else None,
            })
 
        else:
             await sio.emit('error', {'msg': f"Failed to control device {ip}"})

    except Exception as e:
         print(f"Error controlling kasa: {e}")
         await sio.emit('error', {'msg': f"Kasa Control Error: {str(e)}"})

@sio.event
async def get_settings(sid):
    if sid == audio_owner_sid:
        await sio.emit('settings', SETTINGS, room=sid)

@sio.event
async def update_settings(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    # Generic update
    print(f"Updating settings: {data}")
    
    # Handle specific keys if needed
    if "tool_permissions" in data:
        SETTINGS["tool_permissions"].update(data["tool_permissions"])
        if audio_loop:
            audio_loop.update_permissions(SETTINGS["tool_permissions"])
            
    if "face_auth_enabled" in data:
        enabled = bool(data["face_auth_enabled"])
        SETTINGS["face_auth_enabled"] = enabled
        if enabled:
            auth_instance = _ensure_authenticator(sid)
            if auth_instance.authenticated:
                await sio.emit('auth_status', {'authenticated': True}, room=sid)
            else:
                await sio.emit('auth_status', {'authenticated': False}, room=sid)
                if not getattr(auth_instance, 'running', False):
                    asyncio.create_task(auth_instance.start_authentication_loop())
        else:
            _stop_authenticator(sid)
            await sio.emit('auth_status', {'authenticated': True}, room=sid)

    if "camera_flipped" in data:
        SETTINGS["camera_flipped"] = data["camera_flipped"]
        print(f"[SERVER] Camera flip set to: {data['camera_flipped']}")

    save_settings()
    # Broadcast new full settings
    await sio.emit('settings', SETTINGS)


# Deprecated/Mapped for compatibility if frontend still uses specific events
@sio.event
async def get_tool_permissions(sid):
    if sid == audio_owner_sid:
        await sio.emit('tool_permissions', SETTINGS["tool_permissions"], room=sid)

@sio.event
async def update_tool_permissions(sid, data):
    if sid != audio_owner_sid or not isinstance(data, dict):
        return
    print(f"Updating permissions (legacy event): {data}")
    SETTINGS["tool_permissions"].update(data)
    save_settings()
    
    if audio_loop:
        audio_loop.update_permissions(SETTINGS["tool_permissions"])
    # Broadcast update to all
    await sio.emit('tool_permissions', SETTINGS["tool_permissions"])

if __name__ == "__main__":
    uvicorn.run(
        "server:app_socketio", 
        host="127.0.0.1", 
        port=8000, 
        reload=False, # Reload enabled causes spawn of worker which might miss the event loop policy patch
        loop="asyncio",
        reload_excludes=["temp_cad_gen.py", "output.stl", "*.stl"]
    )
