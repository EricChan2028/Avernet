"""Creation-report readiness after host ownership, job and dead-bot checks."""
from __future__ import annotations

from collections.abc import Callable

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.core.apply_engine.delivery import CreationSequence
from agentclaw.community.core.apply_engine.outcomes import ApplyReport, ApplyStatus
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER, CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES, TaskRecord, TaskStatus,
)


def creation_report_state(
    *, report: ApplyReport, job: TaskRecord, bot_is_up: bool, device_id: str,
    live_sequence: Callable[[], CreationSequence],
) -> str:
    # start_apply returns before I/O finishes, and the creation job can already
    # be SUCCEEDED. RUNNING is never a teardown verdict.
    if report.status is ApplyStatus.RUNNING:
        return BotState.PROVISIONING.value
    if report.status is ApplyStatus.SUCCEEDED:
        # Match CreateJobHandler: submission freezes the sequence across mode
        # changes/mixed instances; only legacy jobs use live delivery.
        frozen = job.payload.get("creation_sequence")
        sequence = CreationSequence(str(frozen)) if frozen else live_sequence()
        terminal_trigger = (
            CREATE_PRE_CONTAINER_TRIGGER
            if sequence is CreationSequence.RECORD_APPLY_PROVISION
            else CREATE_ON_CONTAINER_TRIGGER
        )
        if report.trigger == terminal_trigger and bot_is_up:
            return BotState.READY.value
        # An earlier phase is not the full identity; record-first engines also
        # need their container ACTIVE. A failed job cannot advance either step.
        if job.status in TERMINAL_STATUSES and job.status is not TaskStatus.SUCCEEDED:
            return BotState.APPLY_FAILED.value if bot_is_up else BotState.CREATE_FAILED.value
        return BotState.PROVISIONING.value
    if not bot_is_up and bool(device_id.strip()):
        # Concurrent creates can transiently fail apply while the durable publish
        # pipeline (row + device) is still booting. The device pipeline owns that
        # outcome and the caller's poll budget bounds the wait; tearing it down
        # here would recreate a healthy, billable bot. An UP row keeps the apply
        # failure verdict under the existing anti-duplicate contract.
        return BotState.PROVISIONING.value
    return BotState.APPLY_FAILED.value
