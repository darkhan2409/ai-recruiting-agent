"""Регекс-санитайзер на известные prompt-injection паттерны.

Линия защиты №1 от LLM01 (OWASP). Линия №2 — XML-обёртка `<resume_content>`
+ system safety rules в промпте (см. `src/parsing/prompts.py`). Линия №3 —
anti-hallucination check на выход LLM-judge (БЛОК 5).

Список паттернов сознательно короткий: длинные регекс-наборы провоцируют
false-positive на легитимных резюме (например, кто-то пишет «forget me not»
в описании хобби). Здесь — только явные команды LLM.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous", re.compile(r"ignore (all )?previous", re.IGNORECASE)),
    (
        "disregard_instructions",
        re.compile(r"disregard (the |all )?(previous |above )?instructions?", re.IGNORECASE),
    ),
    ("system_bracket", re.compile(r"\[system\s*:", re.IGNORECASE)),
    ("you_are_now", re.compile(r"you are now\s+(a |an )?", re.IGNORECASE)),
    ("system_prompt", re.compile(r"system prompt\s*:", re.IGNORECASE)),
    (
        "forget_instructions",
        re.compile(r"forget (your |all )?(previous )?instructions?", re.IGNORECASE),
    ),
    ("markdown_system", re.compile(r"^###\s*system", re.IGNORECASE | re.MULTILINE)),
    ("im_start_token", re.compile(r"<\|im_start\|>")),
    ("end_of_prompt", re.compile(r"<\|endofprompt\|>")),
]


def detect_injection(text: str) -> tuple[str, str] | None:
    """Найти первое совпадение известных injection-паттернов.

    Args:
        text: Извлечённый из резюме текст.

    Returns:
        `(pattern_name, snippet)` где snippet — окно ±40 символов вокруг
        совпадения для логирования в `quarantine.details`. None если
        ни один паттерн не сработал.
    """
    for name, pattern in _PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        snippet = text[start:end].replace("\n", " ")
        return name, snippet
    return None
