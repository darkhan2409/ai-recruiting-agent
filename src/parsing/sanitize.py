"""Регекс-санитайзер на известные prompt-injection паттерны.

Линия обороны №1 от LLM01 (OWASP). Линия №2 — XML-обёртка `<resume_content>`
+ system safety rules в промпте (см. `src/parsing/prompts.py`). Линия №3 —
anti-hallucination check на выход LLM-judge (БЛОК 5).

`detect_injection` работает в три слоя:
  1. Regex по тексту as-is — точные команды LLM на EN и RU.
  2. Regex по normalized тексту (срезаны zero-width / RTL-маркеры, сжаты
     пробелы) — ловит `ig​nore previous` и `i g n o r e   p r e v i o u s`.
  3. Statistical heuristic case-obfuscation — ловит PDF с переплетёнными
     случай-смешанными слоями (`[prSerYvoiSmeTwpEt...`), где регистр-
     инвариантные regex принципиально не помогают.

Список паттернов сознательно короткий: длинные регекс-наборы провоцируют
false-positive на легитимных резюме. RU-паттерны требуют пары «глагол +
объект» (инструкции/команды/правила), чтобы голое «забудь пароль» в опыте
работы не триггерило карантин.
"""

from __future__ import annotations

import re

_INVISIBLE_CHARS = (
    "\u200b\u200c\u200d\u2060\ufeff"  # zero-width space/joiner/non-joiner/word-joiner/BOM
    "\u200e\u200f"  # LTR/RTL marks
    "\u202a\u202b\u202c\u202d\u202e"  # bidi embedding/override
)
_INVISIBLE_RE = re.compile(f"[{_INVISIBLE_CHARS}]")
_WS_RUN_RE = re.compile(r"\s+")

# Case-obfuscation heuristic: sliding window по тексту, считаем долю
# upper↔lower переходов между letters, которые СМЕЖНЫ в raw chunk (без
# разрыва пробелом/пунктуацией/цифрой). В обфусцированных слоях типа
# `prSerYvoiSmeTpAyt` letters склеены → rate 0.4-0.6. В легитимном тексте
# `XGBoost, LightGBM` запятые разрывают цепочку, в каждом PascalCase токене
# одна транзиция на 5-7 пар → rate <0.20. Порог 0.40 даёт 2x запас.
# Предыдущая версия страйпала разделители и ловила false-positive на
# плотных списках PascalCase-фреймворков в ML-резюме.
_CASE_WINDOW_SIZE = 100
_CASE_WINDOW_STEP = 50
_CASE_WINDOW_MIN_ALPHA = 40
_CASE_TRANSITION_THRESHOLD = 0.40
# Сканируем только начало резюме — большинство injection-атак сидит в первых
# страницах. Полный текст не нужен, экономим на CPU.
_CASE_SCAN_LIMIT = 1500

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
    (
        "ru_ignore_previous",
        re.compile(
            r"игнорируй\s+(все\s+|всё\s+)?(предыдущ\w+|вышеуказанн\w+)\s+"
            r"(инструкци\w+|команд\w+|правил\w+|указани\w+)",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_disregard_instructions",
        re.compile(
            r"не\s+обращай\s+внимани\w+\s+на\s+(предыдущ\w+\s+|вышеуказанн\w+\s+|все\s+)?"
            r"(инструкци\w+|команд\w+|правил\w+)",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_forget_instructions",
        re.compile(
            r"забудь\s+(все\s+|всё\s+|свои\s+|прежние\s+|предыдущ\w+\s+)?"
            r"(инструкци\w+|команд\w+|правил\w+|указани\w+)",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_you_are_now",
        re.compile(
            r"ты\s+(теперь|отныне)\s+(?:\w+\s+){0,3}"
            r"(ассистент\w*|агент\w*|модель\w*|ИИ)",
            re.IGNORECASE,
        ),
    ),
    ("ru_system_bracket", re.compile(r"\[систем\w*\s*:", re.IGNORECASE)),
    ("ru_system_prompt", re.compile(r"систем\w+\s+промпт\s*:", re.IGNORECASE)),
    ("ru_markdown_system", re.compile(r"^###\s*систем", re.IGNORECASE | re.MULTILINE)),
    (
        "ru_new_instructions",
        re.compile(
            r"нов\w+\s+(инструкци\w+|команд\w+|правил\w+|указани\w+)\s*:",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_reveal_system",
        re.compile(
            r"(выведи|покажи|раскрой|сообщи|напечатай)\s+(?:\w+\s+){0,3}"
            r"(систем\w+\s+(промпт|инструкци\w+|правил\w+)|"
            r"(свои|твои)\s+(инструкци\w+|правил\w+|команд\w+|указани\w+)|"
            r"(свой|твой)\s+промпт)",
            re.IGNORECASE,
        ),
    ),
]


def _scan_patterns(text: str) -> tuple[str, str] | None:
    """Прогон `_PATTERNS` по строке; вернуть первое совпадение."""
    for name, pattern in _PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        snippet = text[start:end].replace("\n", " ")
        return name, snippet
    return None


def _normalize(text: str) -> str:
    """Снять невидимые юникод-маркеры (zero-width / RTL) и сжать пробелы.

    Регистр не трогаем — `re.IGNORECASE` в `_PATTERNS` уже это покрывает.
    """
    return _WS_RUN_RE.sub(" ", _INVISIBLE_RE.sub("", text))


def _detect_case_obfuscation(text: str) -> tuple[str, str] | None:
    """Sliding-window heuristic для PDF с переплетёнными case-слоями.

    Считаем upper↔lower переходы только между letters, СМЕЖНЫМИ в raw
    тексте (без разрыва не-буквенными символами). Это отличает реальную
    обфускацию `prSerYvoiSmeTpAyt` от плотного списка PascalCase-токенов
    в легитимном CV (`XGBoost, LightGBM, CatBoost`).
    Возвращаем первое окно с rate ≥ `_CASE_TRANSITION_THRESHOLD`.
    """
    scan_end = min(len(text), _CASE_SCAN_LIMIT)
    for start in range(0, scan_end, _CASE_WINDOW_STEP):
        chunk = text[start : start + _CASE_WINDOW_SIZE]
        prev_letter: str | None = None
        alpha_count = 0
        adjacent_pairs = 0
        transitions = 0
        for c in chunk:
            if c.isalpha():
                alpha_count += 1
                if prev_letter is not None:
                    adjacent_pairs += 1
                    if prev_letter.isupper() != c.isupper():
                        transitions += 1
                prev_letter = c
            else:
                # Разделитель (пробел/пунктуация/цифра) рвёт цепочку.
                prev_letter = None
        if alpha_count < _CASE_WINDOW_MIN_ALPHA or adjacent_pairs == 0:
            continue
        rate = transitions / adjacent_pairs
        if rate < _CASE_TRANSITION_THRESHOLD:
            continue
        snippet = chunk[:120].replace("\n", " ")
        return (
            "case_obfuscation",
            f"window[{start}:{start + _CASE_WINDOW_SIZE}] rate={rate:.2f} | {snippet}",
        )
    return None


def detect_injection(text: str) -> tuple[str, str] | None:
    """Многослойный детект: raw → normalized → case obfuscation.

    Args:
        text: Извлечённый из резюме текст.

    Returns:
        `(pattern_name, snippet)` для первого срабатывания. Имя паттерна
        получает суффикс `_normalized` если совпадение нашлось только
        после `_normalize`. None — если ничего не сработало.
    """
    hit = _scan_patterns(text)
    if hit is not None:
        return hit
    normalized = _normalize(text)
    if normalized != text:
        hit = _scan_patterns(normalized)
        if hit is not None:
            name, snippet = hit
            return f"{name}_normalized", snippet
    return _detect_case_obfuscation(text)
