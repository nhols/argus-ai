from enum import StrEnum


class ExecutionStatus(StrEnum):
    RECEIVED = "received"
    ANALYSED = "analysed"
    NOTIFIED = "notified"
    FAILED = "failed"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"
    NOT_CONFIGURED = "not_configured"


class VideoUploadStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    STORED = "stored"
    FAILED = "failed"
