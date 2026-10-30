"""Registration and dispatch for owned-local backlog Story commands.

The reviewed release command registry already lives in
:mod:`bcs_e2e.local_story_commands`.  These slices are independent,
known-unmapped backlog parents, so their command surface is kept in a focused
module rather than growing the already oversized reviewed-story registry.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Callable

from .run_state import RunStateError


BACKLOG_STORY_COMMANDS = frozenset({
    'local-session-owner-run',
    'local-session-owner-report',
    'local-draft-delivery-run',
    'local-draft-delivery-report',
    'local-provider-owner-run',
    'local-provider-owner-report',
})


def _register_run(commands, name: str, help_text: str, *,
                  cli_binary: bool = False) -> None:
    run = commands.add_parser(name, help=help_text)
    for argument in ('binary', 'state-dir'):
        run.add_argument('--' + argument, type=Path, required=True)
    for argument in ('binary-sha256', 'run-id', 'confirm-run-id'):
        run.add_argument('--' + argument, required=True)
    for argument in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        run.add_argument('--' + argument, action='store_true')
    if cli_binary:
        run.add_argument('--cli-binary', type=Path, required=True)
        run.add_argument('--cli-binary-sha256', required=True)


def _register_report(commands, name: str, help_text: str) -> None:
    report = commands.add_parser(name, help=help_text)
    report.add_argument('--state-dir', type=Path, required=True)
    report.add_argument('--output-dir', type=Path)


def register_backlog_story_commands(commands) -> None:
    _register_run(
        commands, 'local-session-owner-run',
        'Run the owned-local Session-owner permissions Story in one owned BCS runtime')
    _register_report(
        commands, 'local-session-owner-report',
        'Verify frozen owned-local Session-owner evidence offline')
    _register_run(
        commands, 'local-draft-delivery-run',
        'Run the frozen Draft delivery Story beside one owned BCS runtime',
        cli_binary=True)
    _register_report(
        commands, 'local-draft-delivery-report',
        'Verify frozen owned-local Draft delivery evidence offline')
    _register_run(
        commands, 'local-provider-owner-run',
        'Run the frozen Provider owner-isolation Story beside one owned BCS runtime')
    _register_report(
        commands, 'local-provider-owner-report',
        'Verify frozen owned-local Provider owner evidence offline')


def _report_command(args: Namespace, *, write_reports: Callable,
                    builder: Callable, label: str) -> tuple[dict, int]:
    summary = builder(args.state_dir)
    if args.output_dir is not None:
        source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
        if destination == source_root or source_root in destination.parents:
            raise RunStateError(
                'Offline ' + label + ' rendering cannot overwrite original evidence')
    outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
    report = {'status': summary['status'], 'summary': summary,
              'outputs': outputs, 'ready_to_execute': False}
    rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else (
        130 if summary['status'] == 'CANCELLED' else 2)
    return report, rc


def dispatch_backlog_story_command(args: Namespace, *,
                                   write_reports: Callable) -> tuple[dict, int]:
    if args.command == 'local-session-owner-run':
        from .session_owner.local_session_owner_run import local_session_owner_run
        report = local_session_owner_run(
            binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id,
            confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes,
            allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SESSION_OWNER_PASS' else (
            130 if report['status'] == 'CANCELLED' else 2)
        return report, rc
    if args.command == 'local-session-owner-report':
        from .session_owner.local_session_owner_reporting import build_session_owner_report
        return _report_command(
            args, write_reports=write_reports, builder=build_session_owner_report,
            label='Session owner')
    if args.command == 'local-draft-delivery-run':
        from .draft_delivery.run import local_draft_delivery_run
        report = local_draft_delivery_run(
            binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id,
            confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes,
            allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DRAFT_DELIVERY_PASS' else (
            130 if report['status'] == 'CANCELLED' else 2)
        return report, rc
    if args.command == 'local-draft-delivery-report':
        from .draft_delivery.reporting import build_draft_delivery_report
        return _report_command(
            args, write_reports=write_reports, builder=build_draft_delivery_report,
            label='Draft delivery')
    if args.command == 'local-provider-owner-run':
        from .local_provider_owner_run import local_provider_owner_run
        report = local_provider_owner_run(
            binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id,
            confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes,
            allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_PROVIDER_OWNER_PASS' else (
            130 if report['status'] == 'CANCELLED' else 2)
        return report, rc
    if args.command == 'local-provider-owner-report':
        from .local_provider_owner_reporting import build_provider_owner_report
        return _report_command(
            args, write_reports=write_reports, builder=build_provider_owner_report,
            label='Provider owner')
    raise RunStateError('unknown backlog Story command')
