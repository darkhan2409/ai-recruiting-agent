"""FolderSource: dev/demo источник, читает файлы из локальной директории.

В режиме `USE_MOCKS=True` (default) ingestion берёт резюме отсюда — это
снимает зависимость от живого IMAP во время разработки и live-demo.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from src.config import settings
from src.ingestion.base import ALLOWED_EXTS, RawAttachment, RawEmail

logger = logging.getLogger(__name__)


class FolderSource:
    """Читает `settings.inbox_dir`, для каждого допустимого файла → RawEmail.

    Файлы НЕ удаляются после опроса — повторное появление в выдаче снимает
    `processed_emails` (idempotency на уровне pipeline). Это упрощает
    отладку: можно дважды вызвать `poll_once`, увидеть второй раз `skipped`.
    """

    def __init__(self, inbox_dir: str | None = None) -> None:
        self.inbox_dir = Path(inbox_dir or settings.inbox_dir)

    async def poll_once(self) -> list[RawEmail]:
        if not self.inbox_dir.exists():
            return []
        emails: list[RawEmail] = []
        for path in sorted(self.inbox_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_EXTS:
                logger.debug("folder_source: skip non-allowed ext %s", path.name)
                continue
            try:
                emails.append(_path_to_email(path))
            except OSError:
                logger.exception("folder_source: failed to read %s", path.name)
        return emails


def _path_to_email(path: Path) -> RawEmail:
    """Превратить файл в RawEmail с одной аттачкой и стабильным message_id."""
    stat = path.stat()
    content = path.read_bytes()
    digest = hashlib.sha1(f"{path}|{stat.st_mtime_ns}".encode()).hexdigest()
    att = RawAttachment(
        filename=path.name,
        content=content,
        content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size_bytes=stat.st_size,
    )
    return RawEmail(
        message_id=f"folder:{digest}",
        sender=None,
        subject=path.name,
        received_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        attachments=[att],
    )
