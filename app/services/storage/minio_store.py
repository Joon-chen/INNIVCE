import hashlib
from io import BytesIO
from uuid import uuid4

from minio import Minio
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Attachment


class AttachmentStore:
    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket = settings.minio_bucket

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def save_attachment(
        self,
        db: Session,
        *,
        work_event_id,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> Attachment:
        self.ensure_bucket()
        checksum = hashlib.sha256(data).hexdigest()
        key = f"attachments/{work_event_id}/{uuid4()}-{filename}"
        self.client.put_object(
            self.bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        attachment = Attachment(
            work_event_id=work_event_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            storage_bucket=self.bucket,
            storage_key=key,
            checksum=checksum,
        )
        db.add(attachment)
        return attachment

    def put_bytes(
        self,
        *,
        key_prefix: str,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> dict[str, str | int | None]:
        self.ensure_bucket()
        checksum = hashlib.sha256(data).hexdigest()
        safe_prefix = key_prefix.strip("/").replace("..", "_") or "attachments"
        key = f"{safe_prefix}/{uuid4()}-{filename}"
        self.client.put_object(
            self.bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        return {
            "bucket": self.bucket,
            "key": key,
            "checksum": checksum,
            "size_bytes": len(data),
            "content_type": content_type,
        }
