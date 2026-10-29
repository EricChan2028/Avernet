"""Public schemas for service-Bot evaluation environments."""

from typing import Literal

from pydantic import BaseModel


class EvalEnvPublishResult(BaseModel):
    """Submitted eval deployment; success does not imply runtime readiness."""

    success: bool
    publish_id: int
    stage: Literal["eval"]
    bot_uuid: str
    baas_publish_id: int
    baas_bot_status: str | None = None


class EvalEnvTeardownResult(BaseModel):
    """Acknowledgement that environment teardown was enqueued."""

    success: bool
    bot_uuid: str
    default_tag: str
    message: str
