"""Read-only, name-scoped access to installed Hermes/Kora skill guidance."""
from __future__ import annotations

import os
from pathlib import Path
import re
import unicodedata

DEFAULT_ROOT = Path.home() / ".local/share/kora/hermes/skills"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillLibrary:
    def __init__(self, root: str | Path | None = None, max_chars: int = 12000):
        self.root = Path(root or os.environ.get("ADA_SKILLS_ROOT") or DEFAULT_ROOT).expanduser().resolve()
        self.max_chars = max(1000, min(int(max_chars), 20000))

    def _entries(self):
        if not self.root.is_dir():
            return []
        entries = []
        for path in self.root.rglob("SKILL.md"):
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(self.root) or not resolved.is_file():
                    continue
            except OSError:
                continue
            entries.append(resolved)
        return sorted(set(entries), key=lambda p: str(p).casefold())

    @staticmethod
    def _tokens(text: str):
        text = "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                       if not unicodedata.combining(char))
        aliases = {
            "som": "audio", "dispositivo": "device", "dispositivos": "device",
            "conflito": "conflict", "conflitos": "conflict",
            "conflitante": "conflict", "conflitantes": "conflict",
            "roteamento": "rout", "rotear": "rout", "rota": "rout",
            "microfone": "microphone", "microfones": "microphone",
            "voz": "voice", "codigo": "code", "testes": "test",
            "tarefas": "task", "tarefa": "task", "arquivo": "file", "arquivos": "file",
            "agenda": "calendar", "calendario": "calendar", "correio": "email",
            "mail": "email", "assistente": "assistant", "casa": "home",
            "domotica": "home", "automacao": "automation",
        }
        stopwords = {"com", "sem", "para", "por", "uma", "uns", "das", "dos",
                     "que", "the", "and", "for", "with", "from", "into", "use",
                     "write", "create", "add", "implement", "implementation", "build",
                     "helper", "function", "project", "active", "task", "requested", "please"}
        tokens = []
        for token in re.findall(r"[a-z0-9]+", text):
            if token in stopwords:
                continue
            if token in aliases:
                token = aliases[token]
            elif token.endswith("ies") and len(token) > 4:
                token = token[:-3] + "y"
            elif token.endswith("ing") and len(token) > 5:
                token = token[:-3]
                if len(token) > 2 and token[-1] == token[-2]:
                    token = token[:-1]
            elif token.endswith(("ches", "shes", "sses", "xes", "zes")) and len(token) > 5:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            tokens.append(token)
        return tokens

    @staticmethod
    def _metadata(text: str):
        block = re.match(r"\A---\s*\n(.*?)\n---\s*", text, re.S)
        front = block.group(1) if block else ""
        name = re.search(r"(?m)^name:\s*[\"']?([^\"'\n]+)", front)
        description = re.search(r"(?m)^description:\s*[\"']?([^\"'\n]+)", front)
        return ((name.group(1).strip() if name else ""),
                (description.group(1).strip() if description else ""))

    def list_skills(self, limit: int = 200):
        """Return bounded, read-only metadata for every valid installed guide."""
        entries = []
        for path in self._entries():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            name, description = self._metadata(text)
            if name and _NAME.fullmatch(name):
                entries.append({"name": name, "description": description})
        return entries[:max(1, min(int(limit), 250))]

    def search(self, query: str, limit: int = 8):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Provide a skill topic to search.")
        query_tokens = self._tokens(query)
        if "email" in query_tokens:
            query_tokens = ["inbox" if term in {"caixa", "entrada"} else term for term in query_tokens]
        if "test" in query_tokens and "unit" in query_tokens:
            query_tokens = [term for term in query_tokens if term != "unit"]
        terms = {term for term in query_tokens if len(term) > 2}
        found = []
        for path in self._entries():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            name, description = self._metadata(text)
            if not name or not _NAME.fullmatch(name):
                continue
            title_tokens = set(self._tokens(name.replace("-", " ").replace("_", " ")))
            description_tokens = set(self._tokens(description))
            body_tokens = set(self._tokens(text[:5000]))
            coverage = sum(1 for term in terms
                           if term in title_tokens or term in description_tokens or term in body_tokens)
            if coverage != len(terms):
                continue
            score = sum(5 for term in terms if term in title_tokens)
            score += sum(3 for term in terms if term in description_tokens)
            score += sum(1 for term in terms if term in body_tokens)
            if score:
                found.append((score, name, description))
        found.sort(key=lambda item: (-item[0], item[1]))
        return [{"name": name, "description": description}
                for _, name, description in found[:max(1, min(int(limit), 12))]]

    @staticmethod
    def _first_step(text: str):
        lines = text.splitlines()
        for index, line in enumerate(lines):
            current = line.lstrip()
            if not (current.startswith("1. ") or current.startswith("1) ")):
                continue
            parts = [current[3:]]
            for following in lines[index + 1:]:
                item = following.strip()
                if not item or item.startswith("#"):
                    break
                if len(item) > 2 and item[0].isdigit() and item[1:3] in (". ", ") "):
                    break
                parts.append(item)
            return " ".join(" ".join(parts).split())[:1500]
        return ""

    def search_with_guidance(self, query: str, limit: int = 8, preview_chars: int = 5000):
        matches = self.search(query, limit=limit)
        if matches:
            size = max(1000, min(int(preview_chars), 7000))
            guidance = self.read(matches[0]["name"])
            matches[0]["guidance"] = guidance[:size]
            step = self._first_step(guidance)
            if step:
                matches[0]["first_step"] = step
        return matches

    def read(self, name: str):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Skill name is invalid.")
        matches = []
        aliases = []
        for path in self._entries():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            skill_name, _ = self._metadata(text)
            if skill_name == name:
                matches.append((path, text))
            elif name.endswith("-guide") and skill_name == name[:-6]:
                aliases.append((path, text))
        if not matches:
            matches = aliases
        if not matches:
            raise ValueError("Skill was not found in the active skills directory.")
        if len(matches) > 1:
            raise ValueError("Skill name is ambiguous; search results need disambiguation.")
        return matches[0][1][:self.max_chars]
