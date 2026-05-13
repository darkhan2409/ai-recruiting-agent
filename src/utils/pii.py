"""PII-маскировка для логов (DATA_POLICY §«Логи»).

Применяется ПЕРЕД `logger.*` всякий раз, когда в сообщении может оказаться
email, телефон или сырой текст резюме. ФИО алгоритмически не маскируем —
надёжного способа без NER нет; контракт проекта: вместо ФИО логировать
`candidate_id`.

Все функции pure, без I/O, идемпотентны: повторный вызов на уже
замаскированной строке оставляет её как есть.
"""

from __future__ import annotations

import re

# Email: первая буква локала + *** + домен.
# Локал: 2+ символов из [A-Za-z0-9._+-], домен: ≥1 dot, TLD ≥2 букв.
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9])[A-Za-z0-9._+-]{1,}@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w])"
)

# Телефон: +7 / 8 + 10 цифр, опциональные разделители (пробел, скобки, дефис).
# Формат КЗ/РФ: всего 11 цифр. Маска: первые 4 цифры + *** + последние 4.
_PHONE_RE = re.compile(
    r"(?<!\d)(\+7|8)[\s\-()]*(\d)[\s\-()]*(\d)[\s\-()]*(\d)"
    r"[\s\-()]*\d[\s\-()]*\d[\s\-()]*\d"
    r"[\s\-()]*(\d)[\s\-()]*(\d)[\s\-()]*(\d)[\s\-()]*(\d)(?!\d)"
)


def mask_email(text: str) -> str:
    """Замаскировать email-адреса: `ivan.petrov@gmail.com` → `i***@gmail.com`.

    Сохраняет первую букву локала + домен — этого достаточно рекрутёру/SRE
    для дебага без раскрытия идентифицирующих данных.
    """
    if not text:
        return text
    return _EMAIL_RE.sub(r"\1***@\2", text)


def mask_phone(text: str) -> str:
    """Замаскировать телефоны КЗ/РФ: `+77071234567` → `+7707***4567`.

    Поддерживает форматы `+7XXXXXXXXXX`, `8XXXXXXXXXX`,
    `+7 (XXX) XXX-XX-XX`, `8 707 123-45-67`. Сохраняет первые 4 цифры
    (страна+код оператора/региона) + последние 4 — этого достаточно для
    диагностики, не достаточно для дозвона.
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        d2, d3, d4 = match.group(2), match.group(3), match.group(4)
        last4 = "".join(match.group(i) for i in (5, 6, 7, 8))
        return f"{prefix}{d2}{d3}{d4}***{last4}"

    return _PHONE_RE.sub(_replace, text)


def mask_pii(text: str) -> str:
    """Композиция: маскирует email и телефоны в строке.

    Идемпотентна: `mask_pii(mask_pii(x)) == mask_pii(x)`.

    Args:
        text: Произвольная строка для лога.

    Returns:
        Строка с заменёнными email/phone. Если входная строка пустая или
        None-like — возвращает её без изменений.
    """
    if not text:
        return text
    return mask_email(mask_phone(text))
