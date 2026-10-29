"""Opt-in BCS regression runner. No import-time environment mutations."""

# Establish the source boundary before any implementation module is imported.
from .artifact import PROCESS_EXECUTOR_DIGEST
