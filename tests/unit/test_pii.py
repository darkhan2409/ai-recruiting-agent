"""Unit-тесты для маскировки PII (DATA_POLICY §«Логи»).

Проверяем основные форматы email/телефонов, композицию `mask_pii` и
идемпотентность. ФИО не маскируем (см. docstring `pii.py`).
"""

from __future__ import annotations

from src.utils.pii import mask_email, mask_phone, mask_pii


def test_mask_email_basic() -> None:
    assert mask_email("ivan.petrov@gmail.com") == "i***@gmail.com"


def test_mask_email_in_sentence() -> None:
    raw = "Кандидат прислал резюме с почты ivan.petrov@gmail.com — обсудить."
    masked = mask_email(raw)
    assert "ivan.petrov" not in masked
    assert "i***@gmail.com" in masked


def test_mask_email_multiple() -> None:
    raw = "Связаться: a.b@example.com, foo@bar.io"
    masked = mask_email(raw)
    assert "a***@example.com" in masked
    assert "f***@bar.io" in masked
    assert "a.b@" not in masked


def test_mask_email_special_chars_in_local() -> None:
    """Локал с . + - _ — маскируется по первой букве."""
    assert mask_email("john.smith+work@corp.io") == "j***@corp.io"
    assert mask_email("a_b-c@x.co") == "a***@x.co"


def test_mask_email_no_match_for_bare_at() -> None:
    """`@gmail.com` без локала — не email-адрес, не трогаем."""
    assert mask_email("@gmail.com") == "@gmail.com"


def test_mask_phone_kz_compact() -> None:
    assert mask_phone("+77071234567") == "+7707***4567"


def test_mask_phone_eight_prefix() -> None:
    assert mask_phone("87071234567") == "8707***4567"


def test_mask_phone_with_separators() -> None:
    assert mask_phone("+7 (707) 123-45-67") == "+7 (707) 12***45-67" or "***" in mask_phone(
        "+7 (707) 123-45-67"
    )


def test_mask_phone_short_not_a_phone() -> None:
    """`123-45` — слишком короткое, не телефон."""
    raw = "Дом 123-45 корпус 6"
    assert mask_phone(raw) == raw


def test_mask_phone_dates_not_phones() -> None:
    """Год `2021-2026` не должен ловиться (нет префикса +7/8)."""
    raw = "Опыт 2021-2026 на проекте"
    assert mask_phone(raw) == raw


def test_mask_pii_composition() -> None:
    raw = "Контакт: ivan@gmail.com, +77071234567 — Python dev"
    masked = mask_pii(raw)
    assert "ivan@gmail.com" not in masked
    assert "+77071234567" not in masked
    assert "i***@gmail.com" in masked
    assert "+7707" in masked
    # Имя не маскируем алгоритмически — это контракт.
    assert "Python dev" in masked


def test_mask_pii_idempotent() -> None:
    raw = "почта ivan@gmail.com телефон +77071234567"
    once = mask_pii(raw)
    twice = mask_pii(once)
    assert once == twice


def test_mask_pii_empty() -> None:
    assert mask_pii("") == ""
    assert mask_pii(None) is None  # type: ignore[arg-type]
