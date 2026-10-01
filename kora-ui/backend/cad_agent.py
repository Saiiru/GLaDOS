"""Local, declarative CAD generation. Model output is data, never code."""
import asyncio
import base64
import json
import math
from pathlib import Path
import uuid

from local_provider import LocalLLMClient


def validate_design(design):
    """Validate the entire bounded plan before importing or invoking geometry."""
    if not isinstance(design, dict) or set(design) != {"parts"}:
        raise ValueError('Expected {"parts": [...]}')
    parts = design["parts"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= 64:
        raise ValueError("Expected 1 to 64 primitives")
    dimensions = {"box": ("size", 3), "sphere": ("radius", 1), "cylinder": ("dimensions", 2)}
    def numbers(values, count, positive):
        if not isinstance(values, list) or len(values) != count:
            raise ValueError("Invalid dimensions")
        for value in values:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Dimensions must be finite numbers")
            if abs(value) > 1000 or (positive and value < 0.1):
                raise ValueError("Dimensions out of bounds (0.1 to 1000 mm)")
    for index, part in enumerate(parts):
        if not isinstance(part, dict) or part.get("shape") not in dimensions:
            raise ValueError("Only box, sphere and cylinder are supported")
        key, count = dimensions[part["shape"]]
        if set(part) - {"shape", key, "position", "operation"} or key not in part:
            raise ValueError("Unknown or missing primitive fields")
        value = part[key]
        numbers([value] if count == 1 else value, count, True)
        numbers(part.get("position", [0, 0, 0]), 3, False)
        if part.get("operation", "add") not in {"add", "subtract"}:
            raise ValueError("Only add and subtract are supported")
        if index == 0 and part.get("operation", "add") != "add":
            raise ValueError("First primitive must be additive")
    return design


def build_design(design, output_path):
    validate_design(design)
    from build123d import Box, Sphere, Cylinder, export_stl
    result = None
    for part in design["parts"]:
        shape = part["shape"]
        if shape == "box":
            solid = Box(*part["size"])
        elif shape == "sphere":
            solid = Sphere(part["radius"])
        else:
            solid = Cylinder(*part["dimensions"])
        solid = solid.translate(tuple(part.get("position", [0, 0, 0])))
        if result is None:
            result = solid
        elif part.get("operation", "add") == "subtract":
            result = result - solid
        else:
            result = result + solid
    export_stl(result, str(output_path))


class CadAgent:
    def __init__(self, on_thought=None, on_status=None):
        self.client = LocalLLMClient()
        self.on_thought = on_thought
        self.on_status = on_status
        self.system_instruction = (
            'Return only a JSON object {"parts": [...]}, no code or markdown. '
            'Each primitive has shape and dimensions: box uses "size": [x,y,z], '
            'sphere uses "radius": r, cylinder uses "dimensions": [radius,height]. '
            'Optional "position": [x,y,z] translates the centered primitive; '
            'optional "operation": "add" or "subtract" combines it with previous parts. '
            'First part must add. Maximum 64 parts. Dimensions 0.1 to 1000 mm; '
            'positions -1000 to 1000 mm. No other fields are allowed.'
        )

    async def generate_prototype(self, prompt, output_dir=None):
        # Defaults stay inside this checkout, rather than a shared temp design.
        work_dir = Path(output_dir) if output_dir else Path(__file__).parent / "cad_outputs"
        messages = [{"role": "system", "content": self.system_instruction},
                    {"role": "user", "content": prompt}]
        for attempt in range(1, 4):
            if self.on_status:
                self.on_status({"status": "generating" if attempt == 1 else "retrying",
                                "attempt": attempt, "max_attempts": 3, "error": None})
            try:
                response = await self.client.create_chat_completion(messages, max_tokens=2048)
                content = response["choices"][0]["message"]["content"]
                design = validate_design(json.loads(content))
                work_dir.mkdir(parents=True, exist_ok=True)
                output = work_dir / f"output_{uuid.uuid4().hex}.stl"
                await asyncio.to_thread(build_design, design, output)
                data = base64.b64encode(output.read_bytes()).decode("ascii")
                (work_dir / "current_design.json").write_text(json.dumps(design), encoding="utf-8")
                return {"format": "stl", "data": data, "file_path": str(output)}
            except Exception as exc:
                messages.append({"role": "user", "content": f"Invalid design: {exc}. Return a corrected JSON design."})
                if self.on_status:
                    self.on_status({"status": "failed" if attempt == 3 else "retrying",
                                    "attempt": attempt, "max_attempts": 3, "error": str(exc)})
        return None

    async def iterate_prototype(self, prompt, output_dir=None):
        work_dir = Path(output_dir) if output_dir else Path(__file__).parent / "cad_outputs"
        design_path = work_dir / "current_design.json"
        if not design_path.exists():
            return await self.generate_prototype(prompt, str(work_dir))
        design = validate_design(json.loads(design_path.read_text(encoding="utf-8")))
        return await self.generate_prototype(
            f"Current design: {json.dumps(design)}\nRequested changes: {prompt}", str(work_dir))
