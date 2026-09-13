"""S3-compatible object store (MinIO, Cloudflare R2, AWS S3). Requires the ``s3`` extra."""

from __future__ import annotations

from .base import ObjectStore


class S3ObjectStore(ObjectStore):
    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str | None,
        access_key: str | None,
        secret_key: str | None,
        region: str = "auto",
    ) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install with `uv sync --extra s3` to use the S3 object store") from exc
        self.bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        try:
            self._s3.head_bucket(Bucket=bucket)
        except Exception:
            self._s3.create_bucket(Bucket=bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)
