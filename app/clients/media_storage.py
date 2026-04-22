from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
import shutil

logger = logging.getLogger(__name__)


class MediaStorage:
    def upload_file(self, *, local_path: Path, bucket: str, object_key: str) -> str:
        raise NotImplementedError


class LocalStubMediaStorage(MediaStorage):
    """
    Temporary S3 stub.

    It copies file to a local folder and returns a fake public URL.
    Replace this class with a real boto3/minio implementation later.
    """

    def __init__(self, base_dir: str, public_base_url: str) -> None:
        self._base_dir = Path(base_dir)
        self._public_base_url = public_base_url.rstrip("/")
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def upload_file(self, *, local_path: Path, bucket: str, object_key: str) -> str:
        destination = self._base_dir / bucket / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination)
        logger.info("Stub-uploaded media %s -> %s", local_path, destination)
        return f"{self._public_base_url}/{bucket}/{object_key}"


def build_object_key(*parts: str, suffix: str) -> str:
    safe_parts = [part.replace(" ", "_") for part in parts]
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    return "/".join([*safe_parts, f"{timestamp}.{suffix.lstrip('.')}"])