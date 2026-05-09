"""langdetect-обёртка с детерминизмом.

`DetectorFactory.seed = 0` — обязательно по README langdetect: алгоритм
вероятностный, без seed-а на коротких текстах даёт разные ответы между
запусками. Нам нужна воспроизводимость для evals.
"""

from __future__ import annotations

import logging

from langdetect import DetectorFactory, LangDetectException, detect

from src.schemas import Language

DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

_SUPPORTED: dict[str, Language] = {"ru": Language.RU, "en": Language.EN}


def detect_language(text: str) -> Language | None:
    """Определить язык текста.

    Returns:
        `Language.RU` / `Language.EN` если определилось, иначе None
        (включая короткие/мусорные тексты, для которых langdetect кидает
        LangDetectException).
    """
    try:
        code = detect(text)
    except LangDetectException:
        logger.debug("language: langdetect failed on %d chars", len(text))
        return None
    return _SUPPORTED.get(code)
