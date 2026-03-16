import os

from vid_analyser.storage.base import StorageProvider, VideoReference
from vid_analyser.storage.local import LocalStorageProvider
from vid_analyser.storage.s3 import S3StorageProvider

STORAGE_PROVIDER_ENV_VAR = "VID_ANALYSER_STORAGE_PROVIDER"
STORAGE_ROOT_ENV_VAR = "VID_ANALYSER_STORAGE_ROOT"
VIDEO_S3_BUCKET_ENV_VAR = "VID_ANALYSER_VIDEO_S3_BUCKET"
DEFAULT_STORAGE_ROOT = "/app/data/storage"


def build_storage_provider() -> StorageProvider:
    provider = os.getenv(STORAGE_PROVIDER_ENV_VAR, "local").strip().lower()
    if provider == "s3":
        bucket = os.getenv(VIDEO_S3_BUCKET_ENV_VAR)
        if not bucket:
            raise RuntimeError(f"{VIDEO_S3_BUCKET_ENV_VAR} is not set for s3 storage")
        return S3StorageProvider(bucket=bucket)
    if provider == "local":
        root = os.getenv(STORAGE_ROOT_ENV_VAR, DEFAULT_STORAGE_ROOT)
        return LocalStorageProvider(root=root)
    raise RuntimeError(f"Unsupported storage provider: {provider}")


__all__ = [
    "STORAGE_PROVIDER_ENV_VAR",
    "STORAGE_ROOT_ENV_VAR",
    "VIDEO_S3_BUCKET_ENV_VAR",
    "StorageProvider",
    "VideoReference",
    "build_storage_provider",
]
