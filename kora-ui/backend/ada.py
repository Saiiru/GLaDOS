import asyncio
import base64
import io
import os
import sys
import traceback
from dotenv import load_dotenv
import cv2
import pyaudio
import PIL.Image
import mss
import argparse
import math
import struct
import time
from types import SimpleNamespace

from local_provider import LocalLLMClient
from local_session import LocalSession, openai_tools, function_response as make_function_response
from kora_engine_session import KoraEngineSession

if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup
    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

from tools import tools_list

FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

DEFAULT_MODE = "none"

load_dotenv()
client = LocalLLMClient()

# Function definitions
generate_cad = {
    "name": "generate_cad",
    "description": "Generates a 3D CAD model based on a prompt.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "The description of the object to generate."}
        },
        "required": ["prompt"]
    },
    "behavior": "NON_BLOCKING"
}

run_web_agent = {
    "name": "run_web_agent",
    "description": "Opens a web browser and performs a task according to the prompt.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "The detailed instructions for the web browser agent."}
        },
        "required": ["prompt"]
    },
    "behavior": "NON_BLOCKING"
}

create_project_tool = {
    "name": "create_project",
    "description": "Creates a new project folder to organize files.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "The name of the new project."}
        },
        "required": ["name"]
    }
}

switch_project_tool = {
    "name": "switch_project",
    "description": "Switches the current active project context.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "The name of the project to switch to."}
        },
        "required": ["name"]
    }
}

list_projects_tool = {
    "name": "list_projects",
    "description": "Lists all available projects.",
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
}

list_smart_devices_tool = {
    "name": "list_smart_devices",
    "description": "Lists all available smart home devices (lights, plugs, etc.) on the network.",
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
}

control_light_tool = {
    "name": "control_light",
    "description": "Controls a smart light device.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {
                "type": "STRING",
                "description": "The IP address of the device to control. Always prefer the IP address over the alias for reliability."
            },
            "action": {
                "type": "STRING",
                "description": "The action to perform: 'turn_on', 'turn_off', or 'set'."
            },
            "brightness": {
                "type": "INTEGER",
                "description": "Optional brightness level (0-100)."
            },
            "color": {
                "type": "STRING",
                "description": "Optional color name (e.g., 'red', 'cool white') or 'warm'."
            }
        },
        "required": ["target", "action"]
    }
}

discover_printers_tool = {
    "name": "discover_printers",
    "description": "Discovers 3D printers available on the local network.",
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
}

print_stl_tool = {
    "name": "print_stl",
    "description": "Prints an STL file to a 3D printer. Handles slicing the STL to G-code and uploading to the printer.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "stl_path": {"type": "STRING", "description": "Path to STL file, or 'current' for the most recent CAD model."},
            "printer": {"type": "STRING", "description": "Printer name or IP address."},
            "profile": {"type": "STRING", "description": "Optional slicer profile name."}
        },
        "required": ["stl_path", "printer"]
    }
}

get_print_status_tool = {
    "name": "get_print_status",
    "description": "Gets the current status of a 3D printer including progress, time remaining, and temperatures.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "printer": {"type": "STRING", "description": "Printer name or IP address."}
        },
        "required": ["printer"]
    }
}

iterate_cad_tool = {
    "name": "iterate_cad",
    "description": "Modifies or iterates on the current CAD design based on user feedback. Use this when the user asks to adjust, change, modify, or iterate on the existing 3D model (e.g., 'make it taller', 'add a handle', 'reduce the thickness').",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "The changes or modifications to apply to the current design."}
        },
        "required": ["prompt"]
    },
    "behavior": "NON_BLOCKING"
}

tool_declarations = [generate_cad, run_web_agent, create_project_tool, switch_project_tool,
    list_projects_tool, list_smart_devices_tool, control_light_tool, discover_printers_tool,
    print_stl_tool, get_print_status_tool, iterate_cad_tool] + tools_list[0]['function_declarations'][1:]
tools = openai_tools(tool_declarations)
config = {"response_modalities": ["TEXT"], "tools": tools}

# Audio hardware is opened only on an explicit microphone start.
pya = None

from cad_agent import CadAgent
from web_agent import WebAgent
from kasa_agent import KasaAgent
from printer_agent import PrinterAgent
from task_agent import TaskWarriorClient
from local_vision import LocalVisionService
from skill_library import SkillLibrary
from codex_agent import CodexAgent

class AudioLoop:
    def __init__(self, video_mode=DEFAULT_MODE, on_audio_data=None, on_video_frame=None, on_cad_data=None, on_web_data=None, on_transcription=None, on_tool_confirmation=None, on_cad_status=None, on_cad_thought=None, on_project_update=None, on_device_update=None, on_error=None, input_device_index=None, input_device_name=None, output_device_index=None, kasa_agent=None):
        self.video_mode = video_mode
        self.on_audio_data = on_audio_data
        self.on_video_frame = on_video_frame
        self.on_cad_data = on_cad_data
        self.on_web_data = on_web_data
        self.on_transcription = on_transcription
        self.on_tool_confirmation = on_tool_confirmation 
        self.on_cad_status = on_cad_status
        self.on_cad_thought = on_cad_thought
        self.on_project_update = on_project_update
        self.on_device_update = on_device_update
        self.on_error = on_error
        self.input_device_index = input_device_index
        self.input_device_name = input_device_name
        self.output_device_index = output_device_index

        self.audio_in_queue = None
        self.out_queue = None
        self.paused = False

        self.chat_buffer = {"sender": None, "text": ""} # For aggregating chunks
        
        # Track last transcription text to calculate deltas (Speech providers may send cumulative text)
        self._last_input_transcription = ""
        self._last_output_transcription = ""

        self.audio_in_queue = None
        self.out_queue = None
        self.paused = False

        self.session = None
        self._acp_message_parts = []
        
        # Create CadAgent with thought callback
        def handle_cad_thought(thought_text):
            if self.on_cad_thought:
                self.on_cad_thought(thought_text)
        
        def handle_cad_status(status_info):
            if self.on_cad_status:
                self.on_cad_status(status_info)
        
        self.cad_agent = CadAgent(on_thought=handle_cad_thought, on_status=handle_cad_status)
        self.vision_service = LocalVisionService()
        self.web_agent = WebAgent(confirm_action=self.confirm_web_action, vision_service=self.vision_service)
        self.kasa_agent = kasa_agent if kasa_agent else KasaAgent()
        self.printer_agent = PrinterAgent()
        self.task_agent = TaskWarriorClient()
        self.skill_library = SkillLibrary()
        self.codex_agent = CodexAgent()

        self.send_text_task = None
        self._background_tasks = set()
        self.stop_event = asyncio.Event()
        
        self.stop_event = asyncio.Event()
        
        self.permissions = {} # Default Empty (Will treat unset as True)
        self._pending_confirmations = {}
        self._acp_action_broker = None

        # Video buffering state
        self._latest_image_payload = None
        self._latest_frame_at = None
        # VAD State
        self._is_speaking = False
        self._silence_start_time = None
        
        # Initialize ProjectManager
        from project_manager import ProjectManager
        # Assuming we are running from backend/ or root? 
        # Using abspath of current file to find root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # If ada.py is in backend/, project root is one up
        project_root = os.path.dirname(current_dir)
        configured_project_root = os.environ.get('ADA_PROJECT_ROOT')
        if configured_project_root:
            project_root = os.path.expanduser(configured_project_root)
        self.project_manager = ProjectManager(project_root)
        
        # Sync Initial Project State
        if self.on_project_update:
            # We need to defer this slightly or just call it. 
            # Since this is init, loop might not be running, but on_project_update in server.py uses asyncio.create_task which needs a loop.
            # We will handle this by calling it in run() or just print for now.
            pass

    def flush_chat(self):
        """Forces the current chat buffer to be written to log."""
        if self.chat_buffer["sender"] and self.chat_buffer["text"].strip():
            self.project_manager.log_chat(self.chat_buffer["sender"], self.chat_buffer["text"])
            self.chat_buffer = {"sender": None, "text": ""}
        # Reset transcription tracking for new turn
        self._last_input_transcription = ""
        self._last_output_transcription = ""

    def update_permissions(self, new_perms):
        print(f"[ADA DEBUG] [CONFIG] Updating tool permissions: {new_perms}")
        self.permissions.update(new_perms)

    def set_paused(self, paused):
        self.paused = paused

    def stop(self):
        self.stop_event.set()
        
    def resolve_tool_confirmation(self, request_id, confirmed):
        print(f"[ADA DEBUG] [RESOLVE] resolve_tool_confirmation called. ID: {request_id}, Confirmed: {confirmed}")
        if request_id in self._pending_confirmations:
            future = self._pending_confirmations[request_id]
            if not future.done():
                print(f"[ADA DEBUG] [RESOLVE] Future found and pending. Setting result to: {confirmed}")
                future.set_result(confirmed)
            else:
                 print(f"[ADA DEBUG] [WARN] Request {request_id} future already done. Result: {future.result()}")
        else:
            print(f"[ADA DEBUG] [WARN] Confirmation Request {request_id} not found in pending dict. Keys: {list(self._pending_confirmations.keys())}")

    def clear_audio_queue(self):
        """Clears the queue of pending audio chunks to stop playback immediately."""
        try:
            count = 0
            while not self.audio_in_queue.empty():
                self.audio_in_queue.get_nowait()
                count += 1
            if count > 0:
                print(f"[ADA DEBUG] [AUDIO] Cleared {count} chunks from playback queue due to interruption.")
        except Exception as e:
            print(f"[ADA DEBUG] [ERR] Failed to clear audio queue: {e}")

    async def send_frame(self, frame_data):
        # Update the latest frame payload
        if isinstance(frame_data, bytes):
            b64_data = base64.b64encode(frame_data).decode('utf-8')
        else:
            b64_data = frame_data 

        # Keep one latest frame in memory only; analysis is invoked by an explicit tool.
        self._latest_image_payload = {"mime_type": "image/jpeg", "data": b64_data}
        self._latest_frame_at = time.monotonic()

    async def analyze_camera(self, question):
        # Startup can take minutes; prepare the local model first, then validate
        # that the currently-consented frame is still fresh and present.
        await self.vision_service.server.ensure_ready()
        if (self._latest_image_payload is None or self._latest_frame_at is None or
                time.monotonic() - self._latest_frame_at > 5):
            raise ValueError("No fresh camera frame. Turn on the camera and ask again.")
        frame = self._latest_image_payload
        return await self.vision_service.analyze(frame, question)

    def clear_frame(self):
        self._latest_image_payload = None
        self._latest_frame_at = None

    async def send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send(input=msg, end_of_turn=False)

    async def listen_audio(self):
        global pya
        if pya is None:
            pya = pyaudio.PyAudio()
        mic_info = pya.get_default_input_device_info()

        # Resolve Input Device by Name if provided
        resolved_input_device_index = None
        
        if self.input_device_name:
            print(f"[ADA] Attempting to find input device matching: '{self.input_device_name}'")
            count = pya.get_device_count()
            best_match = None
            
            for i in range(count):
                try:
                    info = pya.get_device_info_by_index(i)
                    if info['maxInputChannels'] > 0:
                        name = info.get('name', '')
                        # Simple case-insensitive check
                        if self.input_device_name.lower() in name.lower() or name.lower() in self.input_device_name.lower():
                             print(f"   Candidate {i}: {name}")
                             # Prioritize exact match or very close match if possible, but first match is okay for now
                             resolved_input_device_index = i
                             best_match = name
                             break
                except Exception:
                    continue
            
            if resolved_input_device_index is not None:
                print(f"[ADA] Resolved input device '{self.input_device_name}' to index {resolved_input_device_index} ({best_match})")
            else:
                print(f"[ADA] Could not find device matching '{self.input_device_name}'. Checking index...")

        # Fallback to index if Name lookup failed or wasn't provided
        if resolved_input_device_index is None and self.input_device_index is not None:
             try:
                 resolved_input_device_index = int(self.input_device_index)
                 print(f"[ADA] Requesting Input Device Index: {resolved_input_device_index}")
             except ValueError:
                 print(f"[ADA] Invalid device index '{self.input_device_index}', reverting to default.")
                 resolved_input_device_index = None

        if resolved_input_device_index is None:
             print("[ADA] Using Default Input Device")

        try:
            self.audio_stream = await asyncio.to_thread(
                pya.open,
                format=FORMAT,
                channels=CHANNELS,
                rate=SEND_SAMPLE_RATE,
                input=True,
                input_device_index=resolved_input_device_index if resolved_input_device_index is not None else mic_info["index"],
                frames_per_buffer=CHUNK_SIZE,
            )
        except OSError as e:
            print(f"[ADA] [ERR] Failed to open audio input stream: {e}")
            print("[ADA] [WARN] Audio features will be disabled. Please check microphone permissions.")
            return

        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}
        
        # VAD Constants
        VAD_THRESHOLD = 800 # Adj based on mic sensitivity (800 is conservative for 16-bit)
        SILENCE_DURATION = 0.5 # Seconds of silence to consider "done speaking"
        
        while True:
            if self.paused:
                await asyncio.sleep(0.1)
                continue

            try:
                data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
                
                # 1. Send Audio
                if self.out_queue:
                    await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
                
                # 2. VAD Logic for Video
                # rms = audioop.rms(data, 2)
                # Replacement for audioop.rms(data, 2)
                count = len(data) // 2
                if count > 0:
                    shorts = struct.unpack(f"<{count}h", data)
                    sum_squares = sum(s**2 for s in shorts)
                    rms = int(math.sqrt(sum_squares / count))
                else:
                    rms = 0
                
                if rms > VAD_THRESHOLD:
                    # Speech Detected
                    self._silence_start_time = None
                    
                    if not self._is_speaking:
                        # NEW Speech Utterance Started
                        self._is_speaking = True
                        print(f"[ADA DEBUG] [VAD] Speech Detected (RMS: {rms}). Sending Video Frame.")
                        
                        # Send ONE frame
                        if self._latest_image_payload and self.out_queue:
                            await self.out_queue.put(self._latest_image_payload)
                        else:
                            print(f"[ADA DEBUG] [VAD] No video frame available to send.")
                            
                else:
                    # Silence
                    if self._is_speaking:
                        if self._silence_start_time is None:
                            self._silence_start_time = time.time()
                        
                        elif time.time() - self._silence_start_time > SILENCE_DURATION:
                            # Silence confirmed, reset state
                            print(f"[ADA DEBUG] [VAD] Silence detected. Resetting speech state.")
                            self._is_speaking = False
                            self._silence_start_time = None

            except Exception as e:
                print(f"Error reading audio: {e}")
                await asyncio.sleep(0.1)

    async def handle_cad_request(self, prompt):
        print("[ADA DEBUG] [CAD] Background generation started.")
        if self.on_cad_status:
            self.on_cad_status("generating")
            
        # Auto-create project if stuck in temp
        if self.project_manager.current_project == "temp":
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            new_project_name = f"Project_{timestamp}"
            print(f"[ADA DEBUG] [CAD] Auto-creating project: {new_project_name}")
            
            success, msg = self.project_manager.create_project(new_project_name)
            if success:
                self.project_manager.switch_project(new_project_name)
                # Notify User (Optional, or rely on update)
                try:
                    await self.session.send(input=f"System Notification: Automatic Project Creation. Switched to new project '{new_project_name}'.", end_of_turn=False)
                    if self.on_project_update:
                         self.on_project_update(new_project_name)
                except Exception as e:
                    print(f"[ADA DEBUG] [ERR] Failed to notify auto-project: {e}")

        # Get project cad folder path
        cad_output_dir = str(self.project_manager.get_current_project_path() / "cad")
        
        # Call the secondary agent with project path
        cad_data = await self.cad_agent.generate_prototype(prompt, output_dir=cad_output_dir)
        
        if cad_data:
            print(f"[ADA DEBUG] [OK] CadAgent returned data successfully.")
            print(f"[ADA DEBUG] [INFO] Data Check: {len(cad_data.get('vertices', []))} vertices, {len(cad_data.get('edges', []))} edges.")
            
            if self.on_cad_data:
                print(f"[ADA DEBUG] [SEND] Dispatching data to frontend callback...")
                self.on_cad_data(cad_data)
                print(f"[ADA DEBUG] [SENT] Dispatch complete.")
            
            # Save to Project
            if 'file_path' in cad_data:
                self.project_manager.save_cad_artifact(cad_data['file_path'], prompt)
            else:
                 # Fallback (legacy support)
                 self.project_manager.save_cad_artifact("output.stl", prompt)

            # Notify the model that the task is done - this triggers speech about completion
            completion_msg = "System Notification: CAD generation is complete! The 3D model is now displayed for the user. Let them know it's ready."
            try:
                await self.session.send(input=completion_msg, end_of_turn=True)
                print(f"[ADA DEBUG] [NOTE] Sent completion notification to model.")
            except Exception as e:
                 print(f"[ADA DEBUG] [ERR] Failed to send completion notification: {e}")

        else:
            print(f"[ADA DEBUG] [ERR] CadAgent returned None.")
            # Optionally notify failure
            try:
                await self.session.send(input="System Notification: CAD generation failed.", end_of_turn=True)
            except Exception:
                pass



    async def handle_write_file(self, path, content):
        print("[ADA DEBUG] [FS] Writing project file.")
        
        # Auto-create project if stuck in temp
        if self.project_manager.current_project == "temp":
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            new_project_name = f"Project_{timestamp}"
            print(f"[ADA DEBUG] [FS] Auto-creating project: {new_project_name}")
            
            success, msg = self.project_manager.create_project(new_project_name)
            if success:
                self.project_manager.switch_project(new_project_name)
                # Notify User
                try:
                    await self.session.send(input=f"System Notification: Automatic Project Creation. Switched to new project '{new_project_name}'.", end_of_turn=False)
                    if self.on_project_update:
                         self.on_project_update(new_project_name)
                except Exception as e:
                    print(f"[ADA DEBUG] [ERR] Failed to notify auto-project: {e}")
        
        project_root = os.path.realpath(self.project_manager.get_current_project_path())
        try:
            if not isinstance(path, str) or not path.strip():
                raise ValueError("A non-empty project-relative path is required.")
            relative_path = os.path.basename(path) if os.path.isabs(path) else path
            final_path = os.path.realpath(os.path.join(project_root, relative_path))
            if os.path.commonpath([project_root, final_path]) != project_root or final_path == project_root:
                raise ValueError("File path must stay inside the active project.")
        except (ValueError, TypeError, OSError) as exc:
            result = f"Failed to write file '{path}': {exc}"
        else:
            print("[ADA DEBUG] [FS] Project-relative path validated.")
            try:
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                with open(final_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                result = f"File '{os.path.basename(final_path)}' written successfully to project '{self.project_manager.current_project}'."
            except Exception as e:
                result = f"Failed to write file '{path}': {str(e)}"

        print("[ADA DEBUG] [FS] Project write operation finished.")
        try:
            await self.session.send(input=f"System Notification: {result}", end_of_turn=True)
        except Exception as e:
            print(f"[ADA DEBUG] [ERR] Failed to send fs result: {e}")

    def _project_scoped_path(self, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("A non-empty project-relative path is required.")
        project_root = os.path.realpath(self.project_manager.get_current_project_path())
        candidate = os.path.realpath(path if os.path.isabs(path) else os.path.join(project_root, path))
        if os.path.commonpath([project_root, candidate]) != project_root:
            raise ValueError("File path must stay inside the active project.")
        return candidate

    async def handle_read_directory(self, path):
        log_note = "Directory read failed."
        try:
            scoped_path = self._project_scoped_path(path)
            if not os.path.isdir(scoped_path):
                result = "Requested project directory does not exist."
            else:
                items = os.listdir(scoped_path)
                result = f"Contents of project directory: {', '.join(items)}"
                log_note = f"Directory read completed ({len(items)} entries); names omitted from logs."
        except Exception as e:
            result = f"Directory read rejected: {e}"
        print(f"[ADA DEBUG] [FS] {log_note}")
        try:
            if self.session is not None:
                await self.session.send(input=f"System Notification: {result}", end_of_turn=True)
        except Exception as e:
            print(f"[ADA DEBUG] [ERR] Failed to send directory result: {e}")

    async def handle_read_file(self, path):
        log_note = "File read failed."
        try:
            scoped_path = self._project_scoped_path(path)
            if not os.path.isfile(scoped_path):
                result = "Requested project file does not exist."
            else:
                with open(scoped_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                result = f"Content of project file:\\n{content}"
                log_note = f"File read completed ({len(content)} characters); path and contents omitted from logs."
        except Exception as e:
            result = f"File read rejected: {e}"
        print(f"[ADA DEBUG] [FS] {log_note}")
        try:
            if self.session is not None:
                await self.session.send(input=f"System Notification: {result}", end_of_turn=True)
        except Exception as e:
            print(f"[ADA DEBUG] [ERR] Failed to send file result: {e}")

    async def handle_web_agent_request(self, prompt):
        print("[ADA DEBUG] [WEB] User-approved browser task started.")
        
        async def update_frontend(image_b64, log_text):
            if self.on_web_data:
                 self.on_web_data({"image": image_b64, "log": log_text})
                 
        # Run the web agent and wait for it to return
        result = await self.web_agent.run_task(prompt, update_callback=update_frontend)
        print("[ADA DEBUG] [WEB] Browser task completed.")
        
        # Send the final result back to the main model
        try:
             await self.session.send(input=f"System Notification: Web Agent has finished.\nResult: {result}", end_of_turn=True)
        except Exception as e:
             print(f"[ADA DEBUG] [ERR] Failed to send web agent result to model: {e}")

    def start_background(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def handle_acp_permission(self, params):
        """Ask ADA's visible confirmation UI before a Hermes ACP tool runs."""
        if not isinstance(params, dict):
            return "deny"
        tool_call = params.get("toolCall")
        if not isinstance(tool_call, dict):
            return "deny"
        options = params.get("options")
        option_ids = {
            item.get("optionId") for item in options if isinstance(item, dict)
        } if isinstance(options, list) else set()
        # Only approve an ephemeral option explicitly offered by ACP. Never
        # accept session-wide or persistent permissions.
        if "allow_once" not in option_ids:
            return "deny"
        title = tool_call.get("title")
        raw_input = tool_call.get("rawInput")
        if not isinstance(title, str) or not title.strip() or not isinstance(raw_input, dict):
            return "deny"
        approval_args = {
            "provider": "Hermes ACP (provedor configurado)",
            "kind": tool_call.get("kind", "other"),
            "input": raw_input,
        }
        approved = await self.confirm_tool(f"Hermes ACP: {title.strip()}", approval_args)
        return "allow_once" if approved else "deny"

    async def confirm_tool(self, name, args):
        if not self.on_tool_confirmation:
            return False
        if not hasattr(self, "_confirmation_lock"):
            self._confirmation_lock = asyncio.Lock()
        async with self._confirmation_lock:
            import uuid
            request_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            self._pending_confirmations[request_id] = future
            try:
                self.on_tool_confirmation({"id": request_id, "tool": name, "args": args})
                return await future is True
            finally:
                self._pending_confirmations.pop(request_id, None)

    async def confirm_web_action(self, name, args):
        # Browser sub-actions always ask, even when the parent tool is allowed.
        return await self.confirm_tool("web_" + name, args)

    async def handle_tool_calls(self, calls):
        function_responses = []
        for fc in calls:
            if fc.name not in ["generate_cad", "run_web_agent", "write_file", "read_directory",
                               "read_file", "create_project", "switch_project", "list_projects",
                               "list_smart_devices", "control_light", "discover_printers",
                               "print_stl", "get_print_status", "iterate_cad", "add_task",
                               "list_tasks", "complete_task", "analyze_camera", "search_skills",
                               "read_skill", "run_codex_task"]:
                function_responses.append(make_function_response(fc.id, fc.name, {"error": "Unknown tool"}))
                continue
            # Make every automatically selected skill part of the exact scope
            # shown to the user before they approve a Codex task.
            prompt = fc.args.get("prompt", "")
            if fc.name == "run_codex_task":
                selected = fc.args.get("skills", [])
                if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
                    function_responses.append(make_function_response(
                        fc.id, fc.name, {"error": "Codex skill selection must be a list of names."}))
                    continue
                try:
                    library = getattr(self, "skill_library", None)
                    search_skills = getattr(library, "search", None)
                    ranked = [item["name"] for item in search_skills(prompt, limit=2)] if callable(search_skills) else []
                except (ValueError, OSError, KeyError, TypeError):
                    ranked = []
                fc.args["skills"] = list(dict.fromkeys(ranked + selected))[:4]
            # Settings may add confirmation requirements, but may never bypass
            # confirmation for operations that can change state or expose data.
            skill_read_tools = {"search_skills", "read_skill"}
            if fc.name in skill_read_tools:
                # Local skill metadata/content is read-only; prompt only if an
                # explicit per-tool policy opts into extra confirmation.
                should_confirm = self.permissions.get(fc.name, False)
            else:
                requires_confirmation = fc.name != "list_tasks"
                should_confirm = requires_confirmation or self.permissions.get(fc.name, True)
            if should_confirm and not await self.confirm_tool(fc.name, fc.args):
                function_responses.append(make_function_response(fc.id, fc.name, {"result": "User denied the request to use this tool."}))
                continue
            try:
                if fc.name == "search_skills":
                    matches = self.skill_library.search_with_guidance(fc.args.get("query", ""))
                    function_responses.append(make_function_response(
                        fc.id, fc.name, {"skills": matches, "count": len(matches)}))

                elif fc.name == "read_skill":
                    content = self.skill_library.read(fc.args.get("name", ""))
                    function_responses.append(make_function_response(
                        fc.id, fc.name, {"skill_guidance_untrusted": content}))

                elif fc.name == "run_codex_task":
                    project_path = self.project_manager.get_current_project_path()
                    selected_skills = fc.args.get("skills", [])
                    if not isinstance(selected_skills, list):
                        raise ValueError("Codex skill selection must be a list.")
                    result = await self.codex_agent.run(
                        fc.args.get("prompt", ""), project_path,
                        skills=selected_skills, skill_library=self.skill_library)
                    function_responses.append(make_function_response(
                        fc.id, fc.name, {"result": result, "project": self.project_manager.current_project}))

                elif fc.name == "generate_cad":
                    print(f"\n[ADA DEBUG] --------------------------------------------------")
                    print(f"[ADA DEBUG] [TOOL] Tool Call Detected: 'generate_cad'")
                    print("[ADA DEBUG] [IN] Local assistant request received.")

                    self.start_background(self.handle_cad_request(prompt))
                    function_responses.append(make_function_response(fc.id, fc.name, {"result": "CAD generation started"}))

                elif fc.name == "run_web_agent":
                    print("[ADA DEBUG] [TOOL] Confirmed browser task dispatched.")
                    self.start_background(self.handle_web_agent_request(prompt))

                    result_text = "Web Navigation started. Do not reply to this message."
                    function_response = make_function_response(
                        id=fc.id,
                        name=fc.name,
                        response={
                            "result": result_text,
                        }
                    )
                    print("[ADA DEBUG] [RESPONSE] Function response prepared.")
                    function_responses.append(function_response)



                elif fc.name == "write_file":
                    path = fc.args["path"]
                    content = fc.args["content"]
                    print("[ADA DEBUG] [TOOL] Tool Call: 'write_file'")
                    self.start_background(self.handle_write_file(path, content))
                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": "Writing file..."}
                    )
                    function_responses.append(function_response)

                elif fc.name == "read_directory":
                    path = fc.args["path"]
                    print("[ADA DEBUG] [TOOL] Tool Call: 'read_directory'")
                    self.start_background(self.handle_read_directory(path))
                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": "Reading directory..."}
                    )
                    function_responses.append(function_response)

                elif fc.name == "read_file":
                    path = fc.args["path"]
                    print("[ADA DEBUG] [TOOL] Tool Call: 'read_file'")
                    self.start_background(self.handle_read_file(path))
                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": "Reading file..."}
                    )
                    function_responses.append(function_response)

                elif fc.name == "create_project":
                    name = fc.args["name"]
                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'create_project' name='{name}'")
                    success, msg = self.project_manager.create_project(name)
                    if success:
                        # Auto-switch to the newly created project
                        self.project_manager.switch_project(name)
                        msg += f" Switched to '{name}'."
                        if self.on_project_update:
                            self.on_project_update(name)
                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": msg}
                    )
                    function_responses.append(function_response)

                elif fc.name == "switch_project":
                    name = fc.args["name"]
                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'switch_project' name='{name}'")
                    success, msg = self.project_manager.switch_project(name)
                    if success:
                        if self.on_project_update:
                            self.on_project_update(name)
                        # Gather project context and send to AI (silently, no response expected)
                        context = self.project_manager.get_project_context()
                        print(f"[ADA DEBUG] [PROJECT] Sending project context to AI ({len(context)} chars)")
                        try:
                            await self.session.send(input=f"System Notification: {msg}\n\n{context}", end_of_turn=False)
                        except Exception as e:
                            print(f"[ADA DEBUG] [ERR] Failed to send project context: {e}")
                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": msg}
                    )
                    function_responses.append(function_response)

                elif fc.name == "list_projects":
                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'list_projects'")
                    projects = self.project_manager.list_projects()
                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": f"Available projects: {', '.join(projects)}"}
                    )
                    function_responses.append(function_response)

                elif fc.name == "list_tasks":
                    tasks = self.task_agent.list_pending()
                    function_responses.append(make_function_response(
                        id=fc.id, name=fc.name,
                        response={"source": "Kora Taskwarrior", "count": len(tasks), "tasks": tasks}
                    ))

                elif fc.name == "add_task":
                    task = self.task_agent.add_task(
                        fc.args.get("description"), fc.args.get("due_date"), fc.args.get("priority")
                    )
                    acknowledgement = f"Anotado: {task['description']}. "
                    acknowledgement += (f"Prazo: {task['due_date']}." if task.get('due_date') else "Sem prazo definido.")
                    if task.get('priority'):
                        acknowledgement += f" Prioridade {task['priority']}."
                    function_responses.append(make_function_response(
                        id=fc.id, name=fc.name, response={"result": acknowledgement, "task": task}
                    ))

                elif fc.name == "complete_task":
                    task = self.task_agent.complete_task(
                        fc.args.get("task_id"), fc.args.get("expected_description")
                    )
                    function_responses.append(make_function_response(
                        id=fc.id, name=fc.name,
                        response={"result": f"Concluída: {task['description']}.", "task": task}
                    ))

                elif fc.name == "analyze_camera":
                    description = await self.analyze_camera(fc.args.get("question", ""))
                    function_responses.append(make_function_response(
                        id=fc.id, name=fc.name, response={"description": description}
                    ))

                elif fc.name == "list_smart_devices":
                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'list_smart_devices'")
                    # Use cached devices directly for speed
                    # devices_dict is {ip: SmartDevice}

                    dev_summaries = []
                    frontend_list = []

                    for ip, d in self.kasa_agent.devices.items():
                        dev_type = "unknown"
                        if d.is_bulb: dev_type = "bulb"
                        elif d.is_plug: dev_type = "plug"
                        elif d.is_strip: dev_type = "strip"
                        elif d.is_dimmer: dev_type = "dimmer"

                        # Format for Model
                        info = f"{d.alias} (IP: {ip}, Type: {dev_type})"
                        if d.is_on:
                            info += " [ON]"
                        else:
                            info += " [OFF]"
                        dev_summaries.append(info)

                        # Format for Frontend
                        frontend_list.append({
                            "ip": ip,
                            "alias": d.alias,
                            "model": d.model,
                            "type": dev_type,
                            "is_on": d.is_on,
                            "brightness": d.brightness if d.is_bulb or d.is_dimmer else None,
                            "hsv": d.hsv if d.is_bulb and d.is_color else None,
                            "has_color": d.is_color if d.is_bulb else False,
                            "has_brightness": d.is_dimmable if d.is_bulb or d.is_dimmer else False
                        })

                    result_str = "No devices found in cache."
                    if dev_summaries:
                        result_str = "Found Devices (Cached):\n" + "\n".join(dev_summaries)

                    # Trigger frontend update
                    if self.on_device_update:
                        self.on_device_update(frontend_list)

                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": result_str}
                    )
                    function_responses.append(function_response)

                elif fc.name == "control_light":
                    target = fc.args["target"]
                    action = fc.args["action"]
                    brightness = fc.args.get("brightness")
                    color = fc.args.get("color")

                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'control_light' Target='{target}' Action='{action}'")

                    result_msg = f"Action '{action}' on '{target}' failed."
                    success = False

                    if action == "turn_on":
                        success = await self.kasa_agent.turn_on(target)
                        if success:
                            result_msg = f"Turned ON '{target}'."
                    elif action == "turn_off":
                        success = await self.kasa_agent.turn_off(target)
                        if success:
                            result_msg = f"Turned OFF '{target}'."
                    elif action == "set":
                        success = True
                        result_msg = f"Updated '{target}':"

                    # Apply extra attributes if 'set' or if we just turned it on and want to set them too
                    if success or action == "set":
                        if brightness is not None:
                            sb = await self.kasa_agent.set_brightness(target, brightness)
                            if sb:
                                result_msg += f" Set brightness to {brightness}."
                        if color is not None:
                            sc = await self.kasa_agent.set_color(target, color)
                            if sc:
                                result_msg += f" Set color to {color}."

                    # Notify Frontend of State Change
                    if success:
                        # We don't need full discovery, just refresh known state or push update
                        # But for simplicity, let's get the standard list representation
                        # KasaAgent updates its internal state on control, so we can rebuild the list

                        # Quick rebuild of list from internal dict
                        updated_list = []
                        for ip, dev in self.kasa_agent.devices.items():
                            # We need to ensure we have the correct dict structure expected by frontend
                            # We duplicate logic from KasaAgent.discover_devices a bit, but that's okay for now or we can add a helper
                            # Ideally KasaAgent has a 'get_devices_list()' method.
                            # Use the cached objects in self.kasa_agent.devices

                            dev_type = "unknown"
                            if dev.is_bulb: dev_type = "bulb"
                            elif dev.is_plug: dev_type = "plug"
                            elif dev.is_strip: dev_type = "strip"
                            elif dev.is_dimmer: dev_type = "dimmer"

                            d_info = {
                                "ip": ip,
                                "alias": dev.alias,
                                "model": dev.model,
                                "type": dev_type,
                                "is_on": dev.is_on,
                                "brightness": dev.brightness if dev.is_bulb or dev.is_dimmer else None,
                                "hsv": dev.hsv if dev.is_bulb and dev.is_color else None,
                                "has_color": dev.is_color if dev.is_bulb else False,
                                "has_brightness": dev.is_dimmable if dev.is_bulb or dev.is_dimmer else False
                            }
                            updated_list.append(d_info)

                        if self.on_device_update:
                            self.on_device_update(updated_list)
                    else:
                        # Report Error
                        if self.on_error:
                            self.on_error(result_msg)

                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": result_msg}
                    )
                    function_responses.append(function_response)

                elif fc.name == "discover_printers":
                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'discover_printers'")
                    printers = await self.printer_agent.discover_printers()
                    # Format for model
                    if printers:
                        printer_list = []
                        for p in printers:
                            printer_list.append(f"{p['name']} ({p['host']}:{p['port']}, type: {p['printer_type']})")
                        result_str = "Found Printers:\n" + "\n".join(printer_list)
                    else:
                        result_str = "No printers found on network. Ensure printers are on and running OctoPrint/Moonraker."

                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": result_str}
                    )
                    function_responses.append(function_response)

                elif fc.name == "print_stl":
                    stl_path = fc.args["stl_path"]
                    printer = fc.args["printer"]
                    profile = fc.args.get("profile")

                    print("[ADA DEBUG] [TOOL] Confirmed print request dispatched.")

                    # Resolve 'current' to project STL
                    if stl_path.lower() == "current":
                        stl_path = "output.stl" # Let printer agent resolve it in root_path

                    # Get current project path
                    project_path = str(self.project_manager.get_current_project_path())

                    result = await self.printer_agent.print_stl(
                        stl_path,
                        printer,
                        profile,
                        root_path=project_path
                    )
                    result_str = result.get("message", "Unknown result")

                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": result_str}
                    )
                    function_responses.append(function_response)

                elif fc.name == "get_print_status":
                    printer = fc.args["printer"]
                    print(f"[ADA DEBUG] [TOOL] Tool Call: 'get_print_status' Printer='{printer}'")

                    status = await self.printer_agent.get_print_status(printer)
                    if status:
                        result_str = f"Printer: {status.printer}\n"
                        result_str += f"State: {status.state}\n"
                        result_str += f"Progress: {status.progress_percent:.1f}%\n"
                        if status.time_remaining:
                            result_str += f"Time Remaining: {status.time_remaining}\n"
                        if status.time_elapsed:
                            result_str += f"Time Elapsed: {status.time_elapsed}\n"
                        if status.filename:
                            result_str += f"File: {status.filename}\n"
                        if status.temperatures:
                            temps = status.temperatures
                            if "hotend" in temps:
                                result_str += f"Hotend: {temps['hotend']['current']:.0f}°C / {temps['hotend']['target']:.0f}°C\n"
                            if "bed" in temps:
                                result_str += f"Bed: {temps['bed']['current']:.0f}°C / {temps['bed']['target']:.0f}°C"
                    else:
                        result_str = f"Could not get status for printer '{printer}'. Ensure it is discovered first."

                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": result_str}
                    )
                    function_responses.append(function_response)

                elif fc.name == "iterate_cad":
                    prompt = fc.args["prompt"]
                    print("[ADA DEBUG] [TOOL] Confirmed CAD iteration dispatched.")

                    # Emit status
                    if self.on_cad_status:
                        self.on_cad_status("generating")

                    # Get project cad folder path
                    cad_output_dir = str(self.project_manager.get_current_project_path() / "cad")

                    # Call CadAgent to iterate on the design
                    cad_data = await self.cad_agent.iterate_prototype(prompt, output_dir=cad_output_dir)

                    if cad_data:
                        print(f"[ADA DEBUG] [OK] CadAgent iteration returned data successfully.")

                        # Dispatch to frontend
                        if self.on_cad_data:
                            print(f"[ADA DEBUG] [SEND] Dispatching iterated CAD data to frontend...")
                            self.on_cad_data(cad_data)
                            print(f"[ADA DEBUG] [SENT] Dispatch complete.")

                        # Save to Project
                        self.project_manager.save_cad_artifact(cad_data["file_path"], f"Iteration: {prompt}")

                        result_str = f"Successfully iterated design: {prompt}. The updated 3D model is now displayed."
                    else:
                        print(f"[ADA DEBUG] [ERR] CadAgent iteration returned None.")
                        result_str = f"Failed to iterate design with prompt: {prompt}"

                    function_response = make_function_response(
                        id=fc.id, name=fc.name, response={"result": result_str}
                    )
                    function_responses.append(function_response)
            except Exception as exc:
                function_responses.append(make_function_response(fc.id, fc.name, {"error": str(exc)}))
        return function_responses

    async def _handle_acp_action(self, name, arguments):
        import json
        import uuid

        call = SimpleNamespace(id=f"ada-mcp-{uuid.uuid4().hex}", name=name, args=arguments)
        responses = await self.handle_tool_calls([call])
        if not responses:
            return {"error": "ADA returned no action result."}
        try:
            result = json.loads(responses[0].get("content", "{}"))
        except (TypeError, ValueError):
            return {"error": "ADA returned an invalid action result."}
        return result if isinstance(result, dict) else {"result": result}

    def _create_acp_session(self, transcript):
        from acp_action_broker import ActionBroker
        from acp_mcp_config import effectful_action_server_config, readonly_skill_server_config
        from acp_persona import ADA_ACP_PERSONA
        from acp_session import ACPSession

        self._acp_message_parts = []
        action_names = {
            "generate_cad", "run_web_agent", "write_file", "read_directory", "read_file",
            "create_project", "switch_project", "list_projects", "list_smart_devices",
            "control_light", "discover_printers", "print_stl", "get_print_status",
            "iterate_cad", "add_task", "list_tasks", "complete_task", "analyze_camera",
            "run_codex_task",
        }
        action_schemas = getattr(self, "acp_tool_schemas", tools)
        available_names = {
            item.get("function", {}).get("name") for item in action_schemas
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
        } & action_names
        self._acp_action_broker = None
        action_servers = []
        if available_names:
            self._acp_action_broker = ActionBroker(
                self._handle_acp_action, available_names, tool_schemas=action_schemas
            )
            action_servers.append(effectful_action_server_config(
                socket_path=self._acp_action_broker.socket_path,
                manifest_path=self._acp_action_broker.manifest_path,
            ))

        def collect_message_part(text):
            self._acp_message_parts.append(text)

        async def authorize_prompt(user_text, outbound_text):
            if transcript and not user_text.startswith("System Notification:"):
                transcript({"sender": "User", "text": user_text})
            approved = await self.confirm_tool("Hermes ACP: Enviar mensagem", {
                "provider": "Hermes ACP (provedor configurado)",
                "user_message": user_text,
                "outbound_prompt": outbound_text,
            })
            if not approved and transcript:
                transcript({"sender": "ADA", "text": "Entendido. Não enviei a mensagem ao provedor."})
            return approved

        return ACPSession(
            cwd=str(self.project_manager.get_current_project_path()),
            on_message=collect_message_part,
            on_permission=self.handle_acp_permission,
            on_prompt=authorize_prompt,
            persona_prompt=ADA_ACP_PERSONA,
            mcp_servers=[readonly_skill_server_config(), *action_servers],
        )

    async def receive_audio(self):
        """Consume queued turns and publish complete ACP replies to the UI/TTS."""
        while not self.stop_event.is_set():
            try:
                await self.session.process_next()
                parts = getattr(self, "_acp_message_parts", None)
                if parts:
                    complete = "".join(parts)
                    parts.clear()
                    if complete and self.on_transcription:
                        self.on_transcription({"sender": "ADA", "text": complete})
            except Exception as exc:
                if self.on_error:
                    self.on_error(str(exc))

    async def play_audio(self):
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            output_device_index=self.output_device_index,
        )
        while True:
            bytestream = await self.audio_in_queue.get()
            if self.on_audio_data:
                self.on_audio_data(bytestream)
            await asyncio.to_thread(stream.write, bytestream)

    async def get_frames(self):
        cap = await asyncio.to_thread(cv2.VideoCapture, 0, cv2.CAP_AVFOUNDATION)
        while True:
            if self.paused:
                await asyncio.sleep(0.1)
                continue
            frame = await asyncio.to_thread(self._get_frame, cap)
            if frame is None:
                break
            await asyncio.sleep(1.0)
            if self.out_queue:
                await self.out_queue.put(frame)
        cap.release()

    def _get_frame(self, cap):
        ret, frame = cap.read()
        if not ret:
            return None
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(frame_rgb)
        img.thumbnail([1024, 1024])
        image_io = io.BytesIO()
        img.save(image_io, format="jpeg")
        image_io.seek(0)
        image_bytes = image_io.read()
        return {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}

    async def _get_screen(self):
        pass 
    async def get_screen(self):
         pass

    async def run(self, start_message=None):
        def transcript(event):
            if self.on_transcription:
                self.on_transcription(event)

        engine = os.environ.get(
            "KORA_CONVERSATION_ENGINE",
            os.environ.get("ADA_CONVERSATION_ENGINE", "local"),
        ).strip().casefold()
        if engine == "root":
            config_path = os.environ.get(
                "KORA_CONFIG",
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "configs", "glados_kora.yaml")),
            )
            self.session = KoraEngineSession(config_path, transcript)
            await self.session.start()
        elif engine == "local":
            self.session = LocalSession(client, tools, self.handle_tool_calls, transcript)
        elif engine == "hermes-acp":
            self.session = self._create_acp_session(transcript)
            try:
                await self.session.start()
            except Exception:
                self.session = None
                raise
        else:
            raise ValueError("KORA_CONVERSATION_ENGINE must be 'root', 'local' or 'hermes-acp'.")

        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=10)
        tasks = [asyncio.create_task(self.receive_audio())]
        try:
            if start_message:
                await self.session.send(input=start_message, end_of_turn=True)
            if self.on_project_update:
                self.on_project_update(self.project_manager.current_project)
            await self.stop_event.wait()
        finally:
            tasks.extend(self._background_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            session, self.session = self.session, None
            close_session = getattr(session, "close", None)
            if callable(close_session):
                try:
                    await close_session()
                except Exception:
                    if self.on_error:
                        self.on_error("Hermes ACP session cleanup failed.")
            if self._acp_action_broker is not None:
                try:
                    await self._acp_action_broker.close()
                except Exception:
                    if self.on_error:
                        self.on_error("ADA MCP action broker cleanup failed.")
                self._acp_action_broker = None
            self._acp_message_parts.clear()
            await self.vision_service.close()
            if getattr(self, "audio_stream", None):
                self.audio_stream.close()


def get_input_devices():
    p = pyaudio.PyAudio()
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    devices = []
    for i in range(0, numdevices):
        if (p.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
            devices.append((i, p.get_device_info_by_host_api_device_index(0, i).get('name')))
    p.terminate()
    return devices

def get_output_devices():
    p = pyaudio.PyAudio()
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    devices = []
    for i in range(0, numdevices):
        if (p.get_device_info_by_host_api_device_index(0, i).get('maxOutputChannels')) > 0:
            devices.append((i, p.get_device_info_by_host_api_device_index(0, i).get('name')))
    p.terminate()
    return devices

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        help="pixels to stream from",
        choices=["camera", "screen", "none"],
    )
    args = parser.parse_args()
    main = AudioLoop(video_mode=args.mode)
    asyncio.run(main.run())