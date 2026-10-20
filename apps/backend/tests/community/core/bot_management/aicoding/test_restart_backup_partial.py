"""Partial completion is a verified terminal receipt, not an unknown failure."""
import json
import logging
from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.engines.aicoding import restart_backup as backup
from agentclaw.community.core.bot_management.engines.aicoding.restart_dependencies import RestartBackupDependencies
from agentclaw.community.core.bot_management.engines.aicoding.strategy import AicodingProvisioningStrategy
from agentclaw.community.core.bot_management.engines.provisioning import BotProvisioningContext
from tests.community.core.bot_management.aicoding.test_restart_backup import OPERATION, prepare, response


def partial(**changes):
    event = dict(status='partial_success', operation_id=OPERATION,
                 generation_id='g-partial', error_code='source_files_skipped',
                 failed_phase='backup_partial') | changes
    return response('partial_success', backup=event)


def test_partial_completion_stops_polling_and_verifies_with_warning(caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    execute = Mock(side_effect=[response('running'), partial(), partial()])
    verify = prepare(execute)
    assert execute.call_count == 2
    verify()
    assert execute.call_count == 3
    assert 'phase=prepared status=partial_success' in caplog.text
    assert 'phase=verify status=allowed' in caplog.text
    assert 'reason=partial_receipt_valid' in caplog.text
    assert 'generation_id=g-partial' in caplog.text
    assert 'action=continue_restart' in caplog.text and 'data_loss_risk=true' in caplog.text
    assert 'unknown_status' not in caplog.text
    assert 'phase=prepare status=failed' not in caplog.text
    assert any(record.levelno == logging.WARNING and 'partial_receipt_valid' in record.message
               for record in caplog.records)


@pytest.mark.parametrize('changes', [
    {'generation_id': None}, {'generation_id': ''}, {'generation_id': 42},
    {'operation_id': 'b' * 32}, {'status': 'success'}, {'status': 'failed'},
    {'error_code': 'archive_failed'}, {'error_code': None},
    {'failed_phase': 'publish'},
])
def test_partial_requires_its_own_valid_publication_receipt(changes):
    with pytest.raises(backup.RestartBackupError) as caught:
        prepare(Mock(return_value=partial(**changes)))
    assert caught.value.reason == 'invalid_receipt'


def test_partial_without_receipt_is_invalid():
    with pytest.raises(backup.RestartBackupError) as caught:
        prepare(Mock(return_value=response('partial_success')))
    assert caught.value.reason == 'invalid_receipt'


@pytest.mark.parametrize('change', ['generation', 'boot', 'status', 'receipt'])
def test_partial_verifier_rejects_stale_or_changed_receipt(change):
    value = json.loads(partial().stdout)
    if change == 'generation':
        value['backup']['generation_id'] = 'g-other'
    elif change == 'boot':
        value['boot_id'] = 'new-boot'
    elif change == 'status':
        value.update(status='committed', backup=dict(status='success', generation_id='g-partial',
                                                    operation_id=OPERATION))
    else:
        value['backup']['error_code'] = 'archive_failed'
    changed = response(value.pop('status'), **value)
    verify = prepare(Mock(side_effect=[partial(), changed]))
    with pytest.raises(backup.RestartBackupError):
        verify()


def test_partial_warning_does_not_log_source_paths_or_messages(caplog):
    result = partial(error_message='private-source-content', issues=['/private/source/path'])
    prepare(Mock(return_value=result))()
    assert 'private-source-content' not in caplog.text
    assert '/private/source/path' not in caplog.text


@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
def test_baas_strategy_rechecks_partial_receipt(engine, caplog):
    caplog.set_level(logging.INFO, logger=backup.logger.name)
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [dict(provider_device_id='physical', status='ACTIVE')]
    runtime.post_bots_api.return_value = partial()
    ctx = BotProvisioningContext(bot_id='b', owner_id='o', bot_type='personal', active_engine=engine)
    verify = AicodingProvisioningStrategy(engine)._prepare_restart(
        ctx, device_id='target', operation_id=OPERATION,
        restart_dependencies=RestartBackupDependencies.for_instance(runtime))
    verify()
    assert runtime.post_bots_api.call_count == 2
    assert 'reason=partial_receipt_valid' in caplog.text
    assert 'phase=prepare status=degraded' not in caplog.text



@pytest.mark.parametrize('engine', ['aicoding', 'claude_code'])
@pytest.mark.parametrize('provider', ['arca', 'baas'])
def test_partial_receipt_continues_original_restart_after_verification(engine, provider, caplog):
    from unittest.mock import patch
    from agentclaw.community.core.bot_management.engines.aicoding.restart_arca import ArcaRestartTransport
    from tests.community.core.bot_management.services.test_bot_service_restart_idempotency import (
        FakeRestartLockRepo, _make_service, _make_bot, _stateful_bot_repository,
    )
    from tests.community.core.devices.services.test_device_service import _make_record

    caplog.set_level(logging.INFO, logger=backup.logger.name)
    locks = FakeRestartLockRepo()
    repository, _ = _stateful_bot_repository(_make_bot(active_engine=engine, binding_id=42))
    device = Mock()
    device.get_device.return_value = _make_record(
        id=42, device_id='old-container', device_provider=provider, status='ACTIVE',
        device_props={'sandbox_id': 'old-sandbox'},
    )
    runtime = Mock()
    runtime.list_devices_by_bot_uuid.return_value = [dict(provider_device_id='physical', status='ACTIVE')]

    def execute(*args, **kwargs):
        # The real lifecycle creates an operation ID; echo the ID it sent.
        import re
        cmd = kwargs.get('shell_cmd') or kwargs['payload']['cmd']
        operation = re.search(r'[a-f0-9]{32}', cmd).group()
        value = json.loads(partial().stdout)
        value['operation_id'] = value['backup']['operation_id'] = operation
        return response(value.pop('status'), **value)

    arca_exec = Mock(side_effect=execute)
    runtime.post_bots_api.side_effect = execute
    svc = _make_service(locks, bot_repository=repository, device_provider=device,
                        baas_service_provider=lambda: runtime)
    svc._restart_backup_dependencies = RestartBackupDependencies(
        baas_runtime=lambda: runtime, arca_transport=lambda: ArcaRestartTransport(arca_exec))
    with patch.object(svc, 'stop_bot') as stop, patch.object(svc, 'start_bot') as start, \
         patch.object(svc, '_restart_bot_baas') as update:
        assert svc.restart_bot(bot_id='bot001', user_id='user001') is not None
    if provider == 'arca':
        stop.assert_called_once()
        start.assert_called_once()
        update.assert_not_called()
        assert arca_exec.call_count == 2
    else:
        update.assert_called_once()
        stop.assert_not_called()
        start.assert_not_called()
        assert runtime.post_bots_api.call_count == 2
    assert 'phase=verify status=allowed' in caplog.text
    assert 'reason=partial_receipt_valid' in caplog.text
    assert 'unknown_status' not in caplog.text
