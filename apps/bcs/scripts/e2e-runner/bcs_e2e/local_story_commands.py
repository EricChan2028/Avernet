"""Registration and dispatch for standalone owned-local Story commands."""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from argparse import Namespace
from collections import Counter
from dataclasses import dataclass
import hashlib
from importlib import import_module
import re
import signal
import time

from .errors import has_cancellation
from .inventory import reviewed_release_inventory
from .migration_inventory import migration_plan
from .profile import identifier
from .run_state import RunStateError, write


LOCAL_STORY_COMMANDS = frozenset({
    'local-coverage-suite',
    'local-event-run',
    'local-event-report',
    'local-structured-run',
    'local-structured-report',
    'local-direct-chat-run',
    'local-direct-chat-report',
    'local-slow-judge-run',
    'local-slow-judge-report',
    'local-fixed-loop-run',
    'local-fixed-loop-report',
    'local-edge-friend-mutations-run',
    'local-edge-friend-mutations-report',
    'local-edge-read-run',
    'local-edge-read-report',
    'local-channel-cli-run',
    'local-channel-cli-report',
    'local-channel-http-run',
    'local-channel-http-report',
    'local-delivery-policy-run',
    'local-delivery-policy-report',
    'local-operator-cli-run',
    'local-operator-cli-report',
    'local-collaboration-team-run',
    'local-collaboration-team-report',
    'local-provider-run',
    'local-provider-report',
    'local-session-public-run',
    'local-session-public-report',
    'local-session-sharing-run',
    'local-session-sharing-report',
    'local-session-run',
    'local-session-report',
    'local-network-run',
    'local-network-report',
    'local-workspace-api',
    'local-workspace-run',
    'local-workspace-report',
    'local-file-run',
    'local-file-report',
    'local-caller-execution-run',
    'local-caller-execution-report',
    'local-draft-group-run',
    'local-draft-group-report',
    'local-result-queue-run',
    'local-result-queue-report',
    'local-nested-fixed-loop-run',
    'local-nested-fixed-loop-report',
    'local-service-policy-run',
    'local-service-policy-report',
})


def register_local_story_commands(commands) -> None:
    register_coverage_command(commands)
    event = commands.add_parser('local-event-run', help='Run complete owned-local Event Webhook Story')
    for name in ('binary','state-dir'):
        event.add_argument('--'+name,type=Path,required=True)
    for name in ('binary-sha256','run-id','confirm-run-id'):
        event.add_argument('--'+name,required=True)
    for name in ('allow-local-processes','allow-fixture-write','allow-write'):
        event.add_argument('--'+name,action='store_true')
    event_report=commands.add_parser('local-event-report',help='Verify frozen local Event Webhook evidence offline')
    event_report.add_argument('--state-dir',type=Path,required=True)
    event_report.add_argument('--output-dir',type=Path)
    structured = commands.add_parser('local-structured-run', help='Run original17-step Structured collaboration locally')
    for name in ('binary','state-dir','template-dir','template-manifest'):
        structured.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256','run-id','confirm-run-id','template-manifest-sha256'):
        structured.add_argument('--'+name, required=True)
    for name in ('allow-local-processes','allow-fixture-write','allow-write'):
        structured.add_argument('--'+name, action='store_true')
    structured_report = commands.add_parser('local-structured-report', help='Verify frozen Structured results offline')
    structured_report.add_argument('--state-dir', type=Path, required=True)
    structured_report.add_argument('--output-dir', type=Path)
    direct = commands.add_parser('local-direct-chat-run', help='Run complete original direct-chat Story in an owned local runtime')
    for name in ('binary', 'state-dir'):
        direct.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        direct.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        direct.add_argument('--'+name, action='store_true')
    direct_report = commands.add_parser('local-direct-chat-report', help='Verify frozen direct-chat results offline')
    direct_report.add_argument('--state-dir', type=Path, required=True)
    direct_report.add_argument('--output-dir', type=Path)
    slow_judge = commands.add_parser('local-slow-judge-run', help='Run the complete owned-local slow-Judge Story')
    for name in ('binary', 'state-dir'):
        slow_judge.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        slow_judge.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        slow_judge.add_argument('--' + name, action='store_true')
    slow_judge_report = commands.add_parser('local-slow-judge-report', help='Verify frozen local slow-Judge evidence offline')
    slow_judge_report.add_argument('--state-dir', type=Path, required=True)
    slow_judge_report.add_argument('--output-dir', type=Path)
    fixed_loop = commands.add_parser('local-fixed-loop-run', help='Run the complete owned-local fixed-Loop Story with both scripted Runs')
    for name in ('binary', 'state-dir'):
        fixed_loop.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        fixed_loop.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        fixed_loop.add_argument('--' + name, action='store_true')
    fixed_loop_report = commands.add_parser('local-fixed-loop-report', help='Verify frozen owned-local fixed-Loop evidence offline')
    fixed_loop_report.add_argument('--state-dir', type=Path, required=True)
    fixed_loop_report.add_argument('--output-dir', type=Path)
    edge_friend_mutations = commands.add_parser('local-edge-friend-mutations-run', help='Run all nine owned-local Edge friend-mutation cases in one owned runtime')
    for name in ('binary', 'state-dir'):
        edge_friend_mutations.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        edge_friend_mutations.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        edge_friend_mutations.add_argument('--'+name, action='store_true')
    edge_friend_mutations_report = commands.add_parser('local-edge-friend-mutations-report', help='Verify frozen owned-local Edge friend-mutation evidence offline')
    edge_friend_mutations_report.add_argument('--state-dir', type=Path, required=True)
    edge_friend_mutations_report.add_argument('--output-dir', type=Path)
    edge_read = commands.add_parser('local-edge-read-run', help='Run both dual-environment read-only Edge cases in one owned runtime')
    for name in ('binary', 'state-dir'):
        edge_read.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        edge_read.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        edge_read.add_argument('--'+name, action='store_true')
    edge_read_report = commands.add_parser('local-edge-read-report', help='Verify frozen owned-local Edge read evidence offline')
    edge_read_report.add_argument('--state-dir', type=Path, required=True)
    edge_read_report.add_argument('--output-dir', type=Path)
    file_story = commands.add_parser('local-file-run', help='Run the actual owned-local six-leaf CLI File Story')
    for name in ('binary', 'cli-binary', 'state-dir'):
        file_story.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        file_story.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        file_story.add_argument('--'+name, action='store_true')
    channel_cli = commands.add_parser('local-channel-cli-run', help='Run the complete Channel CLI Story locally')
    for name in ('binary', 'cli-binary', 'state-dir'):
        channel_cli.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        channel_cli.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        channel_cli.add_argument('--'+name, action='store_true')
    channel_cli_report = commands.add_parser('local-channel-cli-report', help='Verify local Channel CLI evidence offline')
    channel_cli_report.add_argument('--state-dir', type=Path, required=True)
    channel_cli_report.add_argument('--output-dir', type=Path)
    channel = commands.add_parser('local-channel-http-run', help='Run the complete disabled-channel HTTP Story locally')
    for name in ('binary', 'state-dir'):
        channel.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        channel.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        channel.add_argument('--'+name, action='store_true')
    channel_report = commands.add_parser('local-channel-http-report', help='Verify local disabled-channel HTTP evidence offline')
    channel_report.add_argument('--state-dir', type=Path, required=True)
    channel_report.add_argument('--output-dir', type=Path)
    delivery_policy = commands.add_parser('local-delivery-policy-run', help='Run the complete human delivery-policy CAS Story locally')
    for name in ('binary', 'state-dir'):
        delivery_policy.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        delivery_policy.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        delivery_policy.add_argument('--'+name, action='store_true')
    delivery_policy_report = commands.add_parser('local-delivery-policy-report', help='Verify local human delivery-policy CAS evidence offline')
    delivery_policy_report.add_argument('--state-dir', type=Path, required=True)
    delivery_policy_report.add_argument('--output-dir', type=Path)
    operator_cli = commands.add_parser('local-operator-cli-run', help='Run the complete operator CLI parent Story locally')
    for name in ('binary', 'cli-binary', 'state-dir'):
        operator_cli.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        operator_cli.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        operator_cli.add_argument('--'+name, action='store_true')
    operator_cli_report = commands.add_parser('local-operator-cli-report', help='Verify local operator CLI evidence offline')
    operator_cli_report.add_argument('--state-dir', type=Path, required=True)
    operator_cli_report.add_argument('--output-dir', type=Path)
    collaboration_team = commands.add_parser('local-collaboration-team-run', help='Run the complete collaboration-team CLI parent Story locally')
    for name in ('binary', 'cli-binary', 'state-dir'):
        collaboration_team.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        collaboration_team.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        collaboration_team.add_argument('--'+name, action='store_true')
    collaboration_team_report = commands.add_parser('local-collaboration-team-report', help='Verify local collaboration-team evidence offline')
    collaboration_team_report.add_argument('--state-dir', type=Path, required=True)
    collaboration_team_report.add_argument('--output-dir', type=Path)
    provider = commands.add_parser('local-provider-run', help='Run the complete Provider/Organization Story in an owned local runtime')
    for name in ('binary', 'state-dir'):
        provider.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        provider.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        provider.add_argument('--'+name, action='store_true')
    provider_report = commands.add_parser('local-provider-report', help='Verify the complete local Provider Story offline')
    provider_report.add_argument('--state-dir', type=Path, required=True)
    provider_report.add_argument('--output-dir', type=Path)
    session_flow = commands.add_parser('local-session-run', help='Run all nine original Session helpers in one owned local runtime')
    for name in ('binary', 'cli-binary', 'state-dir'):
        session_flow.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        session_flow.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        session_flow.add_argument('--'+name, action='store_true')
    flow_report = commands.add_parser('local-session-report', help='Verify complete original local Session flow offline')
    flow_report.add_argument('--state-dir', type=Path, required=True)
    flow_report.add_argument('--output-dir', type=Path)
    sharing = commands.add_parser('local-session-sharing-run', help='Run first six original Session helpers with actual CLI and invitation closure')
    for name in ('binary', 'cli-binary', 'state-dir'):
        sharing.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        sharing.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        sharing.add_argument('--'+name, action='store_true')
    sharing_report = commands.add_parser('local-session-sharing-report', help='Verify original six-helper Session evidence offline')
    sharing_report.add_argument('--state-dir', type=Path, required=True)
    sharing_report.add_argument('--output-dir', type=Path)
    session_public = commands.add_parser('local-session-public-run', help='Run complete public Session/JWT/WS helper; eight parent helpers remain unmigrated')
    for name in ('binary', 'state-dir'):
        session_public.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        session_public.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        session_public.add_argument('--'+name, action='store_true')
    session_report = commands.add_parser('local-session-public-report', help='Verify original local Session public slice offline')
    session_report.add_argument('--state-dir', type=Path, required=True)
    session_report.add_argument('--output-dir', type=Path)
    network = commands.add_parser('local-network-run', help='Execute complete owned-local Agent-network Story; no remote qualification')
    for name in ('binary', 'panel-asset', 'state-dir'):
        network.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'panel-sha256', 'run-id', 'confirm-run-id'):
        network.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        network.add_argument('--'+name, action='store_true')
    network_report = commands.add_parser('local-network-report', help='Verify original Agent-network evidence offline')
    network_report.add_argument('--state-dir', type=Path, required=True)
    network_report.add_argument('--output-dir', type=Path)
    workspace = commands.add_parser('local-workspace-api', help='Run owned-local Workspace API obligations; full Story remains unmigrated')
    for name in ('binary', 'state-dir'):
        workspace.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        workspace.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        workspace.add_argument('--'+name, action='store_true')
    workspace_cli = commands.add_parser('local-workspace-run', help='Run owned Workspace API and actual CLI obligations; incident/messaging/deletion remain unmigrated')
    for name in ('binary', 'cli-binary', 'state-dir'):
        workspace_cli.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        workspace_cli.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        workspace_cli.add_argument('--'+name, action='store_true')
    workspace_cli.add_argument('--with-incident-config', action='store_true',
                               help='Also run owned incident configuration; messages/deletion remain NOT_RUN')
    workspace_cli.add_argument('--with-messages', action='store_true',
                               help='Include incident configuration and actual local Bot transport reception; no deletion')
    workspace_cli.add_argument('--with-incident-delete', action='store_true',
                               help='Include message stage and exact owned-local Incident deletion; no remote cleanup')
    workspace_report = commands.add_parser('local-workspace-report', help='Verify frozen complete Workspace evidence offline')
    workspace_report.add_argument('--state-dir', type=Path, required=True)
    workspace_report.add_argument('--output-dir', type=Path)
    file_report = commands.add_parser('local-file-report', help='Render bound local CLI File evidence offline')
    file_report.add_argument('--state-dir', type=Path, required=True)
    file_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    caller_run = commands.add_parser('local-caller-execution-run', help='Run the complete owned-local caller-execution Story as one black-box child')
    for name in ('binary', 'state-dir'):
        caller_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        caller_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        caller_run.add_argument('--' + name, action='store_true')
    caller_report = commands.add_parser('local-caller-execution-report', help='Verify frozen owned-local caller-execution evidence offline')
    caller_report.add_argument('--state-dir', type=Path, required=True)
    caller_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    draft_run = commands.add_parser('local-draft-group-run', help='Run the complete owned-local Draft group Story as black-box children beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        draft_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        draft_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        draft_run.add_argument('--' + name, action='store_true')
    draft_report = commands.add_parser('local-draft-group-report', help='Verify frozen owned-local Draft group evidence offline')
    draft_report.add_argument('--state-dir', type=Path, required=True)
    draft_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    result_run = commands.add_parser('local-result-queue-run', help='Run the complete owned-local Result queue Story as black-box children beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        result_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        result_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        result_run.add_argument('--' + name, action='store_true')
    result_report = commands.add_parser('local-result-queue-report', help='Verify frozen owned-local Result queue evidence offline')
    result_report.add_argument('--state-dir', type=Path, required=True)
    result_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    nested_run = commands.add_parser('local-nested-fixed-loop-run', help='Run the complete owned-local Nested fixed-loop Story as black-box children beside one owned BCS runtime')
    for name in ('binary', 'cli-binary', 'state-dir'):
        nested_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'driver', 'run-id', 'confirm-run-id'):
        nested_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        nested_run.add_argument('--' + name, action='store_true')
    nested_report = commands.add_parser('local-nested-fixed-loop-report', help='Verify frozen owned-local Nested fixed-loop evidence offline')
    nested_report.add_argument('--state-dir', type=Path, required=True)
    nested_report.add_argument('--output-dir', type=Path)

    # Promoted 30th reviewed release parent slice: the retired service policy
    # Story runs as one black-box child beside one owned BCS runtime.
    policy_run = commands.add_parser('local-service-policy-run', help='Run the complete upstream Retired service policy Story as one black-box child beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        policy_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        policy_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        policy_run.add_argument('--' + name, action='store_true')
    policy_report = commands.add_parser('local-service-policy-report', help='Verify frozen retired service policy evidence offline')
    policy_report.add_argument('--state-dir', type=Path, required=True)
    policy_report.add_argument('--output-dir', type=Path)


def dispatch_local_story_command(args, *, write_reports: Callable) -> tuple[dict, int]:
    if args.command == 'local-coverage-suite':
        return dispatch_coverage_command(args)
    if args.command == 'local-event-run':
        from .local_event_run import local_event_run
        report=local_event_run(binary=args.binary,binary_sha256=args.binary_sha256,
            state_dir=args.state_dir,run_id=args.run_id,confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes,allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc=0 if report['status']=='LOCAL_EVENT_PASS' else 130 if report['status']=='CANCELLED' else 2
    elif args.command == 'local-event-report':
        from .local_event_reporting import build_event_report
        summary=build_event_report(args.state_dir)
        if args.output_dir is not None:
            source_root,destination=args.state_dir.resolve(),args.output_dir.resolve()
            if destination==source_root or source_root in destination.parents:
                raise RunStateError('Offline Event rendering cannot overwrite original evidence')
        outputs=write_reports(summary,args.output_dir,{'all'}) if args.output_dir is not None else {}
        report={'status':summary['status'],'summary':summary,'outputs':outputs,'ready_to_execute':False}
        rc=0 if summary['status']=='LOCAL_SUITE_PASS' else 130 if summary['status']=='CANCELLED' else 2
    elif args.command == 'local-structured-run':
        from .local_structured_run import local_structured_run
        report = local_structured_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write, template_dir=args.template_dir, template_manifest=args.template_manifest,
            template_manifest_sha256=args.template_manifest_sha256)
        rc = 0 if report['status'] == 'LOCAL_STRUCTURED_PASS' else 130 if report['status']=='CANCELLED' else 2
    elif args.command == 'local-structured-report':
        from .local_structured_reporting import build_structured_report
        summary = build_structured_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Structured rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status':summary['status'],'summary':summary,'outputs':outputs,'ready_to_execute':False}
        rc = 0 if summary['status']=='LOCAL_SUITE_PASS' else 130 if summary['status']=='CANCELLED' else 2
    elif args.command == 'local-direct-chat-run':
        from .local_direct_chat_run import local_direct_chat_run
        report = local_direct_chat_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DIRECT_CHAT_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-direct-chat-report':
        from .local_direct_chat_reporting import build_direct_chat_report
        summary = build_direct_chat_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline direct-chat rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-slow-judge-run':
        from .local_slow_judge_run import local_slow_judge_run
        report = local_slow_judge_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SLOW_JUDGE_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-slow-judge-report':
        from .local_slow_judge_reporting import build_slow_judge_report
        summary = build_slow_judge_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline slow-Judge rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-fixed-loop-run':
        from .local_fixed_loop_run import local_fixed_loop_run
        report = local_fixed_loop_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_FIXED_LOOP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-fixed-loop-report':
        from .local_fixed_loop_reporting import build_fixed_loop_report
        summary = build_fixed_loop_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline fixed-Loop rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-friend-mutations-run':
        from .local_edge_friend_mutations_run import local_edge_friend_mutations_run
        report = local_edge_friend_mutations_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_EDGE_FRIEND_MUTATIONS_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-friend-mutations-report':
        from .local_edge_friend_mutations_reporting import build_edge_friend_mutations_report
        summary = build_edge_friend_mutations_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Edge friend-mutation rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-read-run':
        from .local_edge_read_run import local_edge_read_run
        report = local_edge_read_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_EDGE_READ_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-read-report':
        from .local_edge_read_reporting import build_edge_read_report
        summary = build_edge_read_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Edge read rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-cli-run':
        from .local_channel_cli_run import local_channel_cli_run
        report = local_channel_cli_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_CHANNEL_CLI_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-cli-report':
        from .local_channel_cli_reporting import build_channel_cli_report
        summary = build_channel_cli_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Channel CLI rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-http-run':
        from .local_channel_run import local_channel_http_run
        report = local_channel_http_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_CHANNEL_HTTP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-http-report':
        from .local_channel_reporting import build_channel_http_report
        summary = build_channel_http_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Channel rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-delivery-policy-run':
        from .local_delivery_policy_run import local_delivery_policy_run
        report = local_delivery_policy_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DELIVERY_POLICY_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-delivery-policy-report':
        from .local_delivery_policy_reporting import build_delivery_policy_report
        summary = build_delivery_policy_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Delivery policy rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2

    elif args.command == 'local-operator-cli-run':
        from .local_operator_cli_run import local_operator_cli_run
        report = local_operator_cli_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_OPERATOR_CLI_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-operator-cli-report':
        from .local_operator_cli_reporting import build_operator_cli_report
        summary = build_operator_cli_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Operator CLI rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-collaboration-team-run':
        from .local_collaboration_team_run import local_collaboration_team_run
        report = local_collaboration_team_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_COLLABORATION_TEAM_PASS' else 130 if report['status']=='CANCELLED' else 2
    elif args.command == 'local-collaboration-team-report':
        from .local_collaboration_team_reporting import build_collaboration_team_report
        summary = build_collaboration_team_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Collaboration-team rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status']=='CANCELLED' else 2
    elif args.command == 'local-provider-run':
        from .local_provider_run import local_provider_run
        report = local_provider_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_PROVIDER_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-provider-report':
        from .local_provider_reporting import build_provider_report
        summary = build_provider_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Provider rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command in ('local-session-public-run', 'local-session-sharing-run', 'local-session-run'):
        from .local_session_run import local_session_public_run
        report = local_session_public_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write, with_completion=args.command == 'local-session-run',
            **({'cli_binary': args.cli_binary, 'cli_binary_sha256': args.cli_binary_sha256}
               if args.command in ('local-session-sharing-run', 'local-session-run') else {}))
        rc = 0 if report['status'] in ('LOCAL_SESSION_PUBLIC_PASS', 'LOCAL_SESSION_SHARING_PASS', 'LOCAL_SESSION_FLOW_PASS') else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-session-report':
        from .local_session_flow_reporting import build_session_flow_report
        summary = build_session_flow_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Session flow rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-session-sharing-report':
        from .local_session_sharing_reporting import build_sharing_report
        summary = build_sharing_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Session sharing cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SLICE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-session-public-report':
        from .local_session_reporting import build_session_public_report
        summary = build_session_public_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Session rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SLICE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-network-run':
        from .local_network_run import local_network_run
        report = local_network_run(binary=args.binary, binary_sha256=args.binary_sha256,
            panel_asset=args.panel_asset, panel_sha256=args.panel_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_NETWORK_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-network-report':
        from .local_network_reporting import build_network_report
        summary = build_network_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Agent-network rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-workspace-report':
        from .local_workspace_reporting import build_workspace_report
        summary = build_workspace_report(args.state_dir)
        if args.output_dir is not None:
            source_root = args.state_dir.resolve()
            destination = args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Workspace rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-file-report':
        from .local_file_reporting import build_file_report
        summary = build_file_report(args.state_dir.resolve())
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 2
    elif args.command in ('local-workspace-api', 'local-workspace-run'):
        from .local_workspace_run import local_workspace_api_run
        report = local_workspace_api_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write,
            **({'cli_binary': args.cli_binary, 'cli_binary_sha256': args.cli_binary_sha256,
                'with_incident': args.with_incident_config, 'with_messages': args.with_messages, 'with_delete': args.with_incident_delete}
               if args.command == 'local-workspace-run' else {}))
        rc = 0 if report['status'] in ('LOCAL_WORKSPACE_API_PASS', 'LOCAL_WORKSPACE_API_CLI_PASS', 'LOCAL_WORKSPACE_CONFIG_PASS', 'LOCAL_WORKSPACE_MESSAGING_PASS', 'LOCAL_WORKSPACE_INCIDENT_ROUNDTRIP_PASS') else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-file-run':
        from .local_file_story import local_file_run
        report = local_file_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SUITE_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-caller-execution-run':
        from .local_caller_execution_run import local_caller_execution_run
        report = local_caller_execution_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_CALLER_EXECUTION_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-caller-execution-report':
        from .local_caller_execution_reporting import build_caller_execution_report
        summary = build_caller_execution_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Caller execution rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-draft-group-run':
        from .local_draft_group_run import local_draft_group_run
        report = local_draft_group_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DRAFT_GROUP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-draft-group-report':
        from .local_draft_group_reporting import build_draft_group_report
        summary = build_draft_group_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Draft group rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-result-queue-run':
        from .local_result_queue_run import local_result_queue_run
        report = local_result_queue_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_RESULT_QUEUE_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-result-queue-report':
        from .local_result_queue_reporting import build_result_queue_report
        summary = build_result_queue_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Result queue rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-nested-fixed-loop-run':
        from .local_nested_fixed_loop_run import local_nested_fixed_loop_run
        report = local_nested_fixed_loop_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, driver=args.driver,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_NESTED_FIXED_LOOP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-nested-fixed-loop-report':
        from .local_nested_fixed_loop_reporting import build_nested_fixed_loop_report
        summary = build_nested_fixed_loop_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Nested fixed-loop rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-service-policy-run':
        from .local_service_policy_run import local_service_policy_run
        report = local_service_policy_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SERVICE_POLICY_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-service-policy-report':
        from .local_service_policy_reporting import build_service_policy_report
        summary = build_service_policy_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline service policy rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    return report, rc


# Sequential owned-local coverage aggregation. Kept here to preserve the
# artifact source-file budget without weakening the executor digest gate.
@dataclass(frozen=True)
class CaseBinding:
    case_id: str
    command: str
    cli: bool = False
    flags: tuple[str, ...] = ()
    function: str = ''


# Deliberately explicit: inventory additions must be reviewed, never auto-dispatched.
# Edge batch commands are decomposed only at their existing case/adapter seam.
CASES = (
    CaseBinding('LOCAL-TRUST-API-CLI-01', 'local-trust-run', True,
                function='owned_bcs.local_runtime_smoke'),
    CaseBinding('LOCAL-CLI-FILE-01', 'local-file-run', True),
    CaseBinding('LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01', 'local-workspace-run', True, ('with_incident_delete',)),
    CaseBinding('LOCAL-NETWORK-01', 'local-network-run'),
    CaseBinding('LOCAL-SESSION-FLOW-01', 'local-session-run', True),
    CaseBinding('LOCAL-PROVIDER-01', 'local-provider-run'),
    CaseBinding('LOCAL-CHANNEL-HTTP-01', 'local-channel-http-run'),
    CaseBinding('LOCAL-DELIVERY-POLICY-01', 'local-delivery-policy-run'),
    CaseBinding('LOCAL-CHANNEL-CLI-01', 'local-channel-cli-run', True),
    CaseBinding('LOCAL-OPERATOR-CLI-01', 'local-operator-cli-run', True),
    CaseBinding('DUAL-COLLABORATION-TEAM-CLI-01', 'local-collaboration-team-run', True),
    CaseBinding('LOCAL-CUSTOM-COLLABORATION-LIFECYCLE-01', 'run_custom_collaboration_case', True,
                function='local_custom_collaboration_run.local_custom_collaboration_run'),
    CaseBinding('LOCAL-SESSION-SERVICE-PARENT-01', 'execute_local_session_service_parent_case', True,
                function='local_session_service_parent_run.local_session_service_parent_run'),
    CaseBinding('LOCAL-DIRECT-CHAT-01', 'local-direct-chat-run'),
    CaseBinding('LOCAL-STRUCTURED-01', 'local-structured-run'),
    CaseBinding('LOCAL-EVENT-WEBHOOK-01', 'local-event-run'),
    CaseBinding('LOCAL-SLOW-JUDGE-01', 'local-slow-judge-run'),
    CaseBinding('LOCAL-EDGE-ADMISSION-01', 'local-edge-read-run', function='edge_read_case.execute_edge_read_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-REQUESTS-LIST-01', 'local-edge-read-run', function='edge_read_case.execute_edge_read_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-CONNECTIONS-LIST-01', 'local-edge-read-run', function='edge_read_case.execute_edge_read_case'),
    CaseBinding('LOCAL-EDGE-BOT-SEARCH-READ-01', 'local-edge-read-run', function='edge_search_case.execute_edge_search_case'),
    CaseBinding('LOCAL-FIXED-LOOP-01', 'local-fixed-loop-run'),
    CaseBinding('LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-REVOKE-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-ENSURE-BOT-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-FULL-LIFECYCLE-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-SERVICE-POLICY-01', 'local-service-policy-run'),
)
DEFAULT_PANEL = Path(__file__).resolve().parents[3] / 'assets/panel/dist/index.umd.js'
DEFAULT_TEMPLATES = Path(__file__).resolve().parents[3] / 'seeds/collaboration-templates'
SUMMARY_NAME = 'suite-summary.json'
REPORT_NAME = 'suite-report.md'

def list_cases() -> dict:
    """Read-only inventory/mapping check; no binaries or state directory needed."""
    inventory = migration_plan()['local_registered_executors']
    bindings = {case.case_id: case for case in CASES}
    rows = []
    for item in inventory:
        case = bindings.get(item['case_id'])
        expected = (case.command + ''.join(' --' + flag.replace('_', '-') for flag in case.flags)
                    if case else '')
        source = item.get('execution_command', item.get('programmatic_execution_contract', ''))
        matches = bool(case) and (source == expected or (
            case.case_id == 'LOCAL-TRUST-API-CLI-01' and source == 'owned-local-trust-api-cli-v1'))
        rows.append({
            'case_id': item['case_id'], 'legacy_id': item['legacy_id'], 'suite': item['suite'],
            'inventory_binding': source, 'command': expected,
            'function': case.function if case else '',
            'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id',
                                    'confirm_run_id', 'allow_local_processes',
                                    'allow_fixture_write', 'allow_write']
                + (['cli_binary', 'cli_binary_sha256'] if case and case.cli else [])
                + (['panel_asset', 'panel_sha256'] if case and case.command == 'local-network-run' else [])
                + (['template_dir', 'template_manifest', 'template_manifest_sha256'] if case and case.command == 'local-structured-run' else []),
            'status': 'READY' if matches else 'BLOCKED',
        })
    ids = [row['case_id'] for row in rows]
    reviewed_parent_count = len(reviewed_release_inventory())
    aligned = (len(ids) == len(set(ids)) == len(CASES) == reviewed_parent_count
               and set(ids) == set(bindings) and all(row['status'] == 'READY' for row in rows))
    return {'schema_version': 1, 'status': 'READY' if aligned else 'BLOCKED',
            'case_count': len(rows), 'cases': rows, 'remote_validation': 'NOT_RUN',
            'grants_release_credit': False, 'ready_to_execute': False}

def _sha256(path: Path) -> str:
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()

def _binary_evidence(path: Path | None, expected: str | None) -> dict:
    result = {'path': str(path.resolve()) if path else None,
              'expected_sha256': expected, 'sha256': None, 'status': 'BLOCKED'}
    if path is None or expected is None:
        result['reason'] = 'binary_and_sha256_required'
        return result
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError()
        result['sha256'] = _sha256(path)
        if result['sha256'] == expected:
            result['status'] = 'READY'
        else:
            result['reason'] = 'binary_sha256_mismatch'
    except OSError:
        result['reason'] = 'binary_not_readable_regular_file'
    return result

def _edge_run(case: CaseBinding, kwargs: dict) -> dict:
    """Supply one fresh runtime to existing Edge logic, not the batch wrappers."""
    from .case_executor import execute_case
    from .edge_friend_mutation_case import edge_friend_mutation_case_plan
    from .edge_read_case import execute_edge_read_case
    from .edge_search_case import execute_edge_search_case
    from .inventory import executor_digest
    from .local_edge_friend_mutations_case import (
        OwnedEdgeFriendFixtureAdmission, OwnedEdgeFriendMutationsAdapter,
    )
    from .local_fixtures import OwnedLocalFixtures
    from .local_role_identity import OwnedLocalRoleIdentity
    from .owned_bcs import OwnedBcsProcess
    from .owned_edge_read_adapter import (
        OwnedBotSearchFixtureAdmission, OwnedEdgeReadAdapter, OwnedEdgeSearchAdapter,
    )

    mutation = case.command == 'local-edge-friend-mutations-run'
    roles = ('CEO', 'PM', 'ENG', 'QA') if mutation else ('CEO',)
    source = executor_digest()
    with OwnedBcsProcess(binary=kwargs['binary'], binary_sha256=kwargs['binary_sha256'],
                         root=kwargs['state_dir'], run_id=kwargs['run_id'],
                         allow_ambient_local_identity=False) as runtime:
        fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
        with OwnedLocalRoleIdentity(fixtures, roles=roles) as identities:
            fixtures.apply(allow_write=True, role_identity=identities)
            for role in roles:
                identities.verify(role)
            if mutation:
                admission = OwnedEdgeFriendFixtureAdmission(runtime, bot_id=identities.actor_id('CEO'))
                adapter = OwnedEdgeFriendMutationsAdapter(
                    runtime, identities, admission, case_id=case.case_id, allow_write=True)
                execution = execute_case(edge_friend_mutation_case_plan(case.case_id), adapter)
            elif case.function == 'edge_search_case.execute_edge_search_case':
                admission = OwnedBotSearchFixtureAdmission(runtime)
                admission.admit(allow_write=True)
                execution = execute_edge_search_case(OwnedEdgeSearchAdapter(runtime, identities, admission))
            else:
                execution = execute_edge_read_case(case.case_id, OwnedEdgeReadAdapter(runtime, identities, case.case_id))
            snapshot = execution.snapshot()
            write(runtime.root / 'coverage-case-execution.json', {
                'schema_version': 1, 'run_id': kwargs['run_id'], 'executor_digest': source,
                'binary_sha256': kwargs['binary_sha256'], **snapshot,
            })
    shutdown = runtime._record()
    passed = (snapshot['status'] == 'PASS' and snapshot['error_type'] is None
              and snapshot['completed_count'] == snapshot['total_count']
              and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
              and shutdown.get('forced_stop') is False
              and identities.summary().get('credential_memory') == 'CLEARED'
              and executor_digest() == source)
    return {'status': 'PASS' if passed else 'BLOCKED', 'case': snapshot,
            'shutdown': shutdown, 'credentials': identities.summary()}

def _execute(case: CaseBinding, kwargs: dict, panel: dict) -> tuple[dict, int]:
    if case.command in ('local-edge-read-run', 'local-edge-friend-mutations-run'):
        report = _edge_run(case, kwargs)
        return report, 0 if report['status'] == 'PASS' else 2
    if case.command == 'local-trust-run':
        from .local_trust_cli import OwnedLocalTrustCli
        from .owned_bcs import local_runtime_smoke
        report = local_runtime_smoke(
            with_fixtures=True, fixture_set='trusted-team', verify_bot_identities=True,
            check_trust_baseline=True, run_trust_api=True,
            trust_cli=OwnedLocalTrustCli(kwargs['cli_binary'], kwargs['cli_binary_sha256']),
            binary=kwargs['binary'], binary_sha256=kwargs['binary_sha256'],
            root=kwargs['state_dir'], run_id=kwargs['run_id'])
        return report, 0 if report['status'] == 'LOCAL_TRUST_API_CLI_PASS' else 2
    if case.function:
        module, name = case.function.split('.')
        report = getattr(import_module('.' + module, __package__), name)(**kwargs)
        expected = {'run_custom_collaboration_case': 'LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS',
                    'execute_local_session_service_parent_case': 'LOCAL_SESSION_SERVICE_PARENT_PASS'}
        return report, 0 if report['status'] == expected[case.command] else 2
    from .reporting import write_reports
    args = Namespace(command=case.command, **kwargs)
    if case.command == 'local-workspace-run':
        for flag in ('with_incident_config', 'with_messages', 'with_incident_delete'):
            setattr(args, flag, flag in case.flags)
    if case.command == 'local-network-run':
        args.panel_asset, args.panel_sha256 = Path(panel['path']), panel['sha256']
    return dispatch_local_story_command(args, write_reports=write_reports)

def _cancelled(report: dict) -> bool:
    # Only outer/canonical cancellation is suite cancellation.  A successful
    # structured Case may intentionally contain CANCELLED state-machine effects.
    case = report.get('case')
    canonical = report.get('canonical_summary', report.get('canonical_report'))
    return (report.get('status') == 'CANCELLED'
            or report.get('error_type') == 'KeyboardInterrupt'
            or (isinstance(case, dict)
                and (case.get('status') == 'CANCELLED'
                     or case.get('error_type') == 'KeyboardInterrupt'))
            or (isinstance(canonical, dict) and canonical.get('status') == 'CANCELLED'))

def _outcome(report: dict, rc: int) -> dict:
    if rc == 130 or _cancelled(report):
        return {'status': 'CANCELLED', 'failure_reason': 'executor_cancelled'}
    if rc == 0 and (report.get('status') == 'PASS' or re.fullmatch(r'LOCAL_[A-Z_]+_PASS', str(report.get('status')))):
        canonical = report.get('canonical_summary', report.get('canonical_report', {}))
        if not canonical or canonical.get('status') == 'LOCAL_SUITE_PASS':
            return {'status': 'PASS', 'failure_reason': None}
    # Never echo exception messages, response bodies, tokens, or raw CLI output.
    status = report.get('status')
    error = report.get('error_type') or report.get('case', {}).get('error_type')
    reason = 'executor_returned_non_success'
    if isinstance(status, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', status):
        reason += ':' + status
    if isinstance(error, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', error):
        reason += ':' + error
    return {'status': 'BLOCKED' if status == 'BLOCKED' else 'FAILED', 'failure_reason': reason}

def _publish(root: Path, summary: dict, *, finished: bool) -> None:
    counts = Counter(row['status'] for row in summary['cases'])
    summary['counts'] = {status: counts[status] for status in ('PASS', 'FAILED', 'BLOCKED', 'CANCELLED', 'PENDING', 'RUNNING')}
    if finished:
        cancelled = counts['CANCELLED'] or summary.get('cancellation_requested', False)
        summary['status'] = ('CANCELLED' if cancelled else
                             'LOCAL_COVERAGE_SUITE_PASS' if counts['PASS'] == len(reviewed_release_inventory()) else
                             'BLOCKED' if counts['BLOCKED'] else 'LOCAL_COVERAGE_SUITE_FAIL')
        summary['exit_code'] = 130 if cancelled else 0 if counts['PASS'] == len(reviewed_release_inventory()) else 2
    write(root / SUMMARY_NAME, summary)
    lines = ['# Owned-local coverage suite', '', f"Run: `{summary['run_id']}`",
             f"Status: **{summary['status']}**", '',
             'Remote validation: NOT_RUN. Release credit: false. Failed state is retained.', '',
             f"BCS SHA256: `{summary['binaries']['bcs']['sha256']}`",
             f"CLI SHA256: `{summary['binaries']['cli']['sha256']}`", '',
             '| Case | Run | Status | Reason | State directory |', '|---|---|---|---|---|']
    for row in summary['cases']:
        values = (row['case_id'], row['run_id'], row['status'], row['failure_reason'] or '', row['state_dir'])
        lines.append('| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in values) + ' |')
    temporary = root / '.suite-report.tmp'
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write('\n'.join(lines) + '\n')
    temporary.replace(root / REPORT_NAME)

def local_coverage_suite(*, binary: Path, binary_sha256: str, state_dir: Path,
                         run_id: str, confirm_run_id: str,
                         allow_local_processes: bool, allow_fixture_write: bool, allow_write: bool,
                         cli_binary: Path | None = None, cli_binary_sha256: str | None = None,
                         panel_asset: Path | None = None, panel_sha256: str | None = None,
                         template_dir: Path = DEFAULT_TEMPLATES, template_manifest: Path | None = None,
                         template_manifest_sha256: str | None = None) -> dict:
    """Run all parents despite ordinary failures; cancellation stops dispatch."""
    identifier(run_id, 'coverage suite Run ID')
    if len(run_id) > 120:
        raise RunStateError('suite Run ID must leave room for the per-case suffix')
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('suite requires process/fixture/write permissions and exact Run confirmation')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('suite requires a fresh state directory; existing evidence is retained')
    listing = list_cases()
    if listing['status'] != 'READY':
        return {**listing, 'status': 'BLOCKED', 'run_id': run_id, 'exit_code': 2,
                'failure_reason': 'inventory_mapping_drift', 'state_dir': str(state_dir.absolute())}
    root = state_dir.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / 'cases').mkdir(mode=0o700)
    binary, cli_binary = binary.absolute(), cli_binary.absolute() if cli_binary else None
    panel_path = (panel_asset or DEFAULT_PANEL).absolute()
    summary = {
        'schema_version': 1, 'suite': 'owned-local-coverage-suite-v1', 'status': 'RUNNING',
        'run_id': run_id, 'state_dir': str(root), 'case_count': len(reviewed_release_inventory()),
        'binaries': {'bcs': _binary_evidence(binary, binary_sha256),
                     'cli': _binary_evidence(cli_binary, cli_binary_sha256)},
        'panel_asset': {'path': str(panel_path), 'sha256': None, 'status': 'PENDING'},
        'templates': {'directory': str(template_dir.absolute()),
                      'manifest': str(template_manifest.absolute()) if template_manifest else None,
                      'sha256': template_manifest_sha256},
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'full_release_status': 'NOT_QUALIFIED', 'entry_switch': 'NOT_PERFORMED',
        'automatic_cleanup': False, 'ready_to_execute': False,
        'outputs': {'summary': str(root / SUMMARY_NAME), 'report': str(root / REPORT_NAME)},
        'cases': [{**row, 'status': 'PENDING', 'run_id': f'{run_id}-{index:02d}',
                   'state_dir': str(root / 'cases' / row['case_id']),
                   'attempted': False, 'failure_reason': None}
                  for index, row in enumerate(listing['cases'], 1)],
    }
    bindings = {case.case_id: case for case in CASES}
    cancellation = False
    accepting_cancellation = True
    active = False
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def cancel(_signum, _frame):
        nonlocal cancellation
        if not accepting_cancellation:
            return
        summary['cancellation_requested'] = True
        first = not cancellation
        cancellation = True
        if first and active:
            raise KeyboardInterrupt()

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        _publish(root, summary, finished=False)
        for row in summary['cases']:
            case = bindings[row['case_id']]
            if cancellation:
                row.update(status='CANCELLED', failure_reason='not_started_after_suite_cancellation')
                continue
            row['status'] = 'RUNNING'
            _publish(root, summary, finished=False)
            started = time.monotonic()
            try:
                active = True
                if cancellation:
                    raise KeyboardInterrupt()
                child = Path(row['state_dir'])
                if child.exists() or child.is_symlink():
                    row.update(status='BLOCKED', failure_reason='case_state_dir_already_exists')
                    continue
                invalid = next((name for name in ('bcs', 'cli') if (name == 'bcs' or case.cli)
                                and summary['binaries'][name]['status'] != 'READY'), None)
                if invalid:
                    row.update(status='BLOCKED', failure_reason=invalid + ':' + summary['binaries'][invalid]['reason'])
                    continue
                if case.command == 'local-network-run':
                    from .local_panel_asset import LocalPanelAsset
                    # Explicit path or actual default bundle; never generate a placeholder.
                    try:
                        digest = panel_sha256 if panel_sha256 is not None else _sha256(panel_path)
                        LocalPanelAsset(panel_path, digest).validate()
                        summary['panel_asset'].update(sha256=digest, status='READY')
                    except Exception as error:
                        summary['panel_asset'].update(status='BLOCKED')
                        row.update(status='BLOCKED', failure_reason='panel_asset_unavailable_or_invalid:' + type(error).__name__)
                        continue
                kwargs = dict(binary=binary, binary_sha256=binary_sha256, state_dir=child,
                              run_id=row['run_id'], confirm_run_id=row['run_id'],
                              allow_local_processes=True, allow_fixture_write=True, allow_write=True)
                if case.cli:
                    kwargs.update(cli_binary=cli_binary, cli_binary_sha256=cli_binary_sha256)
                if case.command == 'local-structured-run':
                    from .local_template_asset import LocalTemplateAsset
                    if template_manifest is None or template_manifest_sha256 is None:
                        row.update(status='BLOCKED', failure_reason='approved_template_manifest_and_sha256_required')
                        continue
                    try:
                        LocalTemplateAsset(template_dir, template_manifest, template_manifest_sha256).validate()
                    except Exception as error:
                        row.update(status='BLOCKED', failure_reason='template_asset_invalid:' + type(error).__name__)
                        continue
                    kwargs.update(template_dir=template_dir, template_manifest=template_manifest,
                                  template_manifest_sha256=template_manifest_sha256)
                row['attempted'] = True
                report, rc = _execute(case, kwargs, summary['panel_asset'])
                row.update(_outcome(report, rc))
                cancellation = cancellation or row['status'] == 'CANCELLED'
            except (Exception, KeyboardInterrupt) as error:
                cancellation = cancellation or has_cancellation(error)
                row.update(status='CANCELLED' if cancellation else 'BLOCKED' if isinstance(error, (ImportError, FileNotFoundError, RunStateError)) else 'FAILED',
                           failure_reason='executor_exception:' + type(error).__name__)
            finally:
                active = False
                if cancellation:
                    row.update(status='CANCELLED', failure_reason=row['failure_reason'] or 'suite_cancelled')
                row['duration_seconds'] = round(time.monotonic() - started, 3)
                _publish(root, summary, finished=False)
        summary['cancellation_requested'] = cancellation
        accepting_cancellation = False  # Seal the durable terminal result.
        _publish(root, summary, finished=True)
        return summary
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

def register_coverage_command(commands) -> None:
    parser = commands.add_parser('local-coverage-suite', help='Run all reviewed-release owned-local parents, sequentially and in isolated state directories')
    parser.add_argument('--template-dir', type=Path, default=DEFAULT_TEMPLATES)
    parser.add_argument('--list-cases', action='store_true', help='List explicit bindings without starting processes or creating state')
    for name in ('binary', 'cli-binary', 'state-dir', 'panel-asset', 'template-manifest'):
        parser.add_argument('--' + name, type=Path)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id', 'panel-sha256', 'template-manifest-sha256'):
        parser.add_argument('--' + name)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        parser.add_argument('--' + name, action='store_true')

def dispatch_coverage_command(args) -> tuple[dict, int]:
    if args.list_cases:
        report = list_cases()
        return report, 0 if report['status'] == 'READY' else 2
    for name in ('binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id'):
        if getattr(args, name) is None:
            raise RunStateError('local-coverage-suite requires --' + name.replace('_', '-'))
    report = local_coverage_suite(**{name: value for name, value in vars(args).items()
                                   if name not in ('command', 'list_cases')})
    return report, report['exit_code']
