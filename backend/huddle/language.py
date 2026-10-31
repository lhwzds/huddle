"""Intent word lists, loaded from config/language.yaml, behind a small interface.

The orchestrator asks questions ("is this a stop?", "does it address everyone?", "does this message
hand the question to Theo?") and never sees a word list itself.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from .config import config_root


def _any(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def name_pattern(names) -> re.Pattern:
    """Matches one member by any of their names, with an optional @.

    Names written in scripts with word boundaries (Latin, Cyrillic...) must stand alone ("Mira" not in
    "Miranda"); names in scripts without spaces between words (Chinese, Japanese) match where they appear.
    """
    alternatives = []
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        escaped = re.escape(name)
        if any(unicodedata.east_asian_width(c) in ("W", "F") for c in name):
            alternatives.append(escaped)
        else:
            alternatives.append(rf"(?<!\w){escaped}(?!\w)")
    return re.compile(r"@?(?:" + "|".join(alternatives) + ")", re.IGNORECASE)


class Language:
    def __init__(self, data: dict):
        self.stop_words = {w.lower() for w in data.get("stop_words", [])}
        self._everyone = _any(data.get("everyone", []))
        self._file = _any(data.get("file_request", []))
        self._discussion = _any(data.get("discussion", []))
        self._remember = _any(data.get("remember", []))
        self._question_to = data.get("question_to")
        self._handoffs = list(data.get("handoff", []))

    def addresses_everyone(self, text: str) -> bool:
        return bool(self._everyone and self._everyone.search(text))

    def is_stop(self, text: str, names: list[re.Pattern]) -> bool:
        """Only a stop word is left once names, 'everyone' words and punctuation are removed."""
        for pattern in names:
            text = pattern.sub(" ", text)
        if self._everyone:
            text = self._everyone.sub(" ", text)
        text = "".join(c for c in text if unicodedata.category(c)[0] not in "PS")
        return " ".join(text.lower().split()) in self.stop_words

    def wants_file(self, text: str) -> bool:
        return bool(self._file and self._file.search(text))

    def wants_discussion(self, text: str) -> bool:
        return bool(self._discussion and self._discussion.search(text))

    def wants_remember(self, text: str) -> bool:
        """True when the person asks for something to be kept beyond this conversation."""
        return bool(self._remember and self._remember.search(text))

    def invites(self, name: re.Pattern, text: str) -> bool:
        """The text asks this member something, or hands part of the question to them."""
        inner = name.pattern
        if any(text[m.start():m.end()].startswith("@") for m in name.finditer(text)):
            return True
        templates = ([self._question_to] if self._question_to else []) + self._handoffs
        return any(re.search(t.replace("{name}", f"(?:{inner})"), text, re.IGNORECASE) for t in templates)


def load_language(cfg: dict, *, config_path=None) -> Language:
    path = Path(cfg.get("language") or "language.yaml")
    if not path.is_absolute():
        path = config_root(config_path) / path
    return Language(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
