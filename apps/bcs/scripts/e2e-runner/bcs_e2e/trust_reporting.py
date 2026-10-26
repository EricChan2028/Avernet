"""Bound, offline canonical reporting for the registered owned-local parent.

Completion is published last. Source receipts are preserved, never rewritten to
manufacture a PASS. No network, database query, credential resolution or cleanup.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time

from .errors import has_cancellation
from .profile import fingerprint, load
from .run_state import RunStateError, read
from .trust_context import CONTEXT_CONTRACT, _exclusive, validate_profile
from .trust_registration import CASE_ID, SUITE, expected_requirements, finalization_ids

COMPLETION_CONTRACT = 'owned-local-trust-completion-v1'
REPORT_NAME = 'report-'+CASE_ID+'.json'
BASE_SOURCES = ('profile.json','trust-run-context.json','run.json','trust-registration.json',
                'manifest.json','local-fixtures.json','bcs-config.json','owned-bcs.json')
OPTIONAL_SOURCES = ('local-trust-api.json','local-trust-api-integrity.json','smoke-report.json')


def _require(condition, message):
    if not condition:
        raise RunStateError(message)


def _sha(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        metadata=os.fstat(stream.fileno())
        _require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink==1 and metadata.st_size<=8*1024*1024,
                 'trusted report source is not a bounded regular unshared file')
        data=stream.read(8*1024*1024+1)
    _require(len(data)<=8*1024*1024,'trusted report source exceeds its byte budget')
    return hashlib.sha256(data).hexdigest()


def _source_names(root):
    return set(BASE_SOURCES)|{name for name in OPTIONAL_SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(profile, root):
    from .inventory import frozen_manifest
    validate_profile(profile)
    _require(root.is_absolute() and root.is_dir() and not root.is_symlink(), 'trusted report requires its absolute owned root')
    run=read(root/'run.json');context=read(root/'trust-run-context.json');registration=read(root/'trust-registration.json')
    manifest=frozen_manifest(root,run)
    _require(context.get('contract')==CONTEXT_CONTRACT and run.get('canonical_reporting')=='COMPLETION_REQUIRED',
             'trusted Run has no canonical reporting lifecycle; old bindings are not upgraded')
    _require(run.get('status')=='CONTEXT_BOUND' and run.get('ready_to_execute') is False
             and manifest['suite']==SUITE and len(manifest['cases'])==1
             and manifest['cases'][0]['id']==CASE_ID and manifest['cases'][0]['version']==1
             and manifest['cases'][0]['requirements']==expected_requirements()
             and set(manifest['cases'][0]['assertions'])==set(sum(expected_requirements().values(),[])+finalization_ids())
             and len(manifest['legacy_inventory'])==34, 'trusted report inventory or Run declaration is invalid')
    _require(run.get('run_id')==context.get('run_id')==registration.get('run_id')==profile['environment']['id']
             and run.get('runtime_dir')==context.get('runtime_dir')==registration.get('runtime_dir')==str(root)
             and fingerprint(profile)==run.get('profile_digest')==context.get('profile_digest')
             and fingerprint(load(root/'profile.json'))==fingerprint(profile)
             and fingerprint(context)==run.get('run_context_digest')
             and fingerprint(registration)==context.get('registration_digest')
             and fingerprint(read(root/'local-fixtures.json'))==run.get('fixture_digest')==context.get('fixture_digest')
             and _sha(root/'bcs-config.json')==context.get('runtime_config_digest'), 'trusted report identity or fixture binding differs')
    for key in ('suite','case_id','case_version','manifest_digest','executor_digest','migration_contract_digest'):
        _require(run.get(key)==context.get(key)==registration.get(key), 'trusted report registration binding differs')
    return run,manifest


def _case(profile, root, run, manifest, *, failed, cancelled, expected_journal, child_unknown):
    journal={}
    source_ok=True
    try:
        journal=read(root/'local-trust-api.json')
    except (RunStateError,OSError):
        source_ok=False
    if (root/'local-trust-api-integrity.json').exists():
        source_ok=False
    if not failed:
        source_ok=source_ok and expected_journal is not None and fingerprint(journal)==fingerprint(expected_journal)
    for key in ('run_id','profile_digest','manifest_digest','executor_digest','case_version','case_id','run_context_digest','fixture_digest'):
        source_ok=source_ok and journal.get(key)==run.get(key)
    assertions=journal.get('assertions',[]) if source_ok else []
    valid=(isinstance(assertions,list) and len(assertions)<=1000 and all(isinstance(item,dict)
           and set(item)=={'id','status'} and isinstance(item['id'],str) and item['id'].startswith(CASE_ID+'.')
           and len(item['id'])<=256 and item['status'] in ('PASS','FAIL','BLOCKED','ERROR','SKIPPED','CANCELLED') for item in assertions))
    if not valid:
        assertions=[];source_ok=False
    ids=[item['id'] for item in assertions]
    required=set(manifest['cases'][0]['assertions'])
    assertions_ok=source_ok and len(ids)==len(set(ids)) and required<=set(ids) and all(item['status']=='PASS' for item in assertions)
    shutdown=read(root/'owned-bcs.json')
    stopped=(shutdown.get('run_id')==run['run_id'] and shutdown.get('origin')==profile['entrypoint']['origin']
             and shutdown.get('binary_sha256')==read(root/'trust-registration.json')['bcs_binary_sha256']
             and shutdown.get('status')=='STOPPED' and type(shutdown.get('returncode')) is int and shutdown.get('returncode')==0
             and journal.get('child_shutdown_status')!='UNCONFIRMED' and not child_unknown)
    operations=journal.get('operations',[]);cli=journal.get('cli_operations',[])
    operations_ok=(isinstance(operations,list) and 5<=len(operations)<=11 and isinstance(cli,list) and len(cli)==4
                   and [item.get('operation') for item in cli if isinstance(item,dict)]==['request','requests','accept','list']
                   and all(isinstance(item,dict) and item.get('status')=='ACKNOWLEDGED'
                           and item.get('resource_binding','BOUND')=='BOUND' for item in [op for op in operations if not isinstance(op,dict) or op.get('operation')!='visibility.restore']+cli)
                   and all(item.get('credential_cleanup')=='CLEARED' and item.get('cli_exit_code')==0 for item in cli))
    business_required={key for key in required if '-final-original-visibility' not in key and '-final-visibility-stored' not in key}
    states={item['id']:item['status'] for item in assertions}
    business_ok=source_ok and len(ids)==len(set(ids)) and all(states.get(key)=='PASS' for key in business_required)
    functional='PASS' if source_ok and journal.get('functional')=='PASS' and business_ok and operations_ok else 'NOT_CONFIRMED'
    restored=source_ok and journal.get('visibility_restoration')=='PASS'
    passed=(not failed and source_ok and journal.get('status')=='LOCAL_TRUST_API_CLI_PASS'
            and functional=='PASS' and assertions_ok and restored and stopped)
    status='PASS' if passed else 'CANCELLED' if cancelled else 'FAIL' if any(item['status']=='FAIL' for item in assertions) else 'BLOCKED'
    return {'schema_version':1,'case_id':CASE_ID,'case_version':1,'run_id':run['run_id'],
            **{key:run[key] for key in ('profile_digest','manifest_digest','executor_digest','run_context_digest')},
            'status':status,'functional':functional,'cleanup':'PASS' if restored and stopped else 'BLOCKED',
            'evidence_status':'PASS' if assertions_ok and operations_ok else 'INCOMPLETE',
            'evidence_required':['api','cli','sqlite'],'assertions':assertions,
            'execution_status':'OWNED_RUNTIME_STOPPED' if stopped else 'UNCONFIRMED',
            'resource_disposition':'RETAINED_LOCAL_DATABASE','visibility_restoration':'PASS' if restored else 'UNCONFIRMED',
            'source_integrity':'PASS' if source_ok else 'UNCONFIRMED','automatic_relation_delete':False,
            'invocation_failed':bool(failed),'child_shutdown_status':'UNCONFIRMED' if child_unknown else 'OBSERVED',
            'ready_to_execute':False}


def _summary(profile, run, manifest, case, issues):
    from .reporting import redact
    status=case['status'] if not issues or case.get('status') in ('CANCELLED','FAIL','ERROR') else 'BLOCKED'
    assertions=case.get('assertions',[])
    if not isinstance(assertions,list):assertions=[]
    states={row['id']:row['status'] for row in assertions if isinstance(row,dict)
            and isinstance(row.get('id'),str) and isinstance(row.get('status'),str)}
    requirements=[{'id':name,'status':'PASS' if not issues and all(states.get(key)=='PASS' for key in ids) else 'INCOMPLETE'}
                  for name,ids in manifest['cases'][0]['requirements'].items()]
    return redact({'schema_version':1,'status':'LOCAL_SUITE_PASS' if status=='PASS' else 'BLOCKED',
        'run_id':run['run_id'],'profile_digest':run['profile_digest'],
        'runner':{'version':1,'executor_digest':run['executor_digest'],'baseline_commit':manifest['baseline_commit']},
        'target':{'tier':'local','environment_id':profile['environment']['id'],'entrypoint':profile['entrypoint'],'deployment_version':'owned-binary'},
        'scope':{'suite':SUITE,'manifest_digest':run['manifest_digest'],'pilot_cases':[CASE_ID],
                 'legacy_inventory_count':34,'full_release_status':'NOT_QUALIFIED',
                 'reason':'One locally verified parent is not the full multi-environment release inventory.'},
        'cases':[{'id':CASE_ID,'version':1,'title':manifest['cases'][0]['title'],'required':True,'status':status,
                  'functional':case.get('functional','NOT_RUN'),'cleanup':case.get('cleanup','BLOCKED'),
                  'evidence_status':case.get('evidence_status','NOT_RUN'),'required_evidence':['api','cli','sqlite'],
                  'assertion_count':len(assertions),'diagnostic_status':'NOT_RUN',
                  'execution_status':case.get('execution_status','UNCONFIRMED'),
                  'resource_disposition':'RETAINED_LOCAL_DATABASE','remote_validation':'NOT_RUN',
                  'invocation_status':'FAILED' if case.get('invocation_failed') else 'COMPLETED' if status=='PASS' else 'UNCONFIRMED'}],
        'requirements':requirements,'routes':[],
        'timing':{'steps':0,'interpretation':'functional evidence only; not a performance benchmark'},
        'mock_boundary':{'entrypoint_kind':'singlebox','local_mock':False,
                         'not_proven':['Gateway/SSO','remote/OB behavior','Bot model execution','edge permission graph','full release coverage']},
        'cleanup':{'status':case.get('cleanup','BLOCKED'),'verification_scope':'OWNED_VISIBILITY_AND_RUNTIME_ONLY',
                   'physical_data_erasure':'NOT_ATTEMPTED','associated_history_purge':'NOT_ATTEMPTED',
                   'retention_disposition':'RETAINED_LOCAL_DATABASE','automatic_relation_delete':False},
        'execution_safety':{'status':case.get('execution_status','UNCONFIRMED')},
        'integrity':{'status':'PASS' if not issues else 'BLOCKED','issues':issues},
        'reports':{'case_count':1 if case.get('case_id')==CASE_ID else 0,'declared_case_count':1,'expected_assertion_count':len(manifest['cases'][0]['assertions']),
                   'observed_assertion_count':len(assertions)},'generated_at':int(time.time())})


def _publication_fence(root, pending_digest):
    path=root/'trust-publication-pending.json'
    if pending_digest is None:
        _require(not path.exists() and not path.is_symlink(), 'trusted publication is still pending')
    else:
        _require(_sha(path)==pending_digest, 'trusted pending publication fence changed')


def build_trust_report(profile, root, *, _pending_digest=None):
    root=Path(root)
    _require(root.is_absolute() and not root.is_symlink(), 'trusted reporting root must be absolute and not a symlink')
    root=root.resolve()
    run,manifest=_bound(profile,root)
    issues=[]
    case={'status':'BLOCKED','assertions':[]}
    try:
        _publication_fence(root,_pending_digest)
        _require(not (root/'trust-publication-failure.json').exists() and not (root/'trust-publication-failure.json').is_symlink(),
                 'trusted publication recorded a failure')
        completion_digest=_sha(root/'trust-completion.json')
        completion=read(root/'trust-completion.json')
        _require(completion.get('contract')==COMPLETION_CONTRACT and completion.get('status')=='FINALIZED'
                 and completion.get('run_id')==run['run_id'] and completion.get('run_context_digest')==run['run_context_digest'],
                 'trusted completion receipt is invalid')
        sources=completion.get('sources')
        _require(isinstance(sources,dict) and set(sources)==_source_names(root)|{REPORT_NAME},
                 'trusted completion source set differs')
        _require(all(_sha(root/name)==digest for name,digest in sources.items()), 'trusted completed source changed')
        _require({p.name for p in root.glob('report-*.json')}=={REPORT_NAME}, 'trusted Run has an unknown Case report')
        case=read(root/REPORT_NAME)
        for key in ('run_id','profile_digest','manifest_digest','executor_digest','run_context_digest','case_id','case_version'):
            _require(case.get(key)==run.get(key), 'trusted Case report binding differs')
        from .reporting import _validate_case
        issues.extend(_validate_case(manifest['cases'][0],case,['api','cli','sqlite']))
        if case.get('status')=='PASS':
            _require(case.get('functional')=='PASS' and case.get('cleanup')=='PASS' and case.get('evidence_status')=='PASS'
                     and case.get('execution_status')=='OWNED_RUNTIME_STOPPED' and case.get('source_integrity')=='PASS'
                     and case.get('invocation_failed') is False, 'trusted Case PASS contradicts its lifecycle')
        _require(all(_sha(root/name)==digest for name,digest in sources.items())
                 and set(sources)==_source_names(root)|{REPORT_NAME}
                 and _sha(root/'trust-completion.json')==completion_digest
                 and not (root/'trust-publication-failure.json').exists()
                 and not (root/'trust-publication-failure.json').is_symlink(),
                 'trusted sources changed during offline projection')
        _publication_fence(root,_pending_digest)
    except (RunStateError,OSError,KeyError,TypeError,ValueError):
        issues.append('trusted completion or source evidence is missing, invalid or changed')
        case={'status':'BLOCKED','assertions':[]}
    return _summary(profile,run,manifest,case,issues)


def _publish_trust_report(registration, runtime, *, result=None, failure=None):
    context=registration.context
    if context is None:return None
    _require(not getattr(context,'publication_attempted',False), 'trusted completion publication cannot be retried')
    context.publication_attempted=True
    registration.validate(runtime)
    run,manifest=_bound(context.profile,context.root)
    context.publication_root=context.root
    context.publication_binding={'run_id':run['run_id'],'run_context_digest':run['run_context_digest']}
    _require(fingerprint(read(context.root/'owned-bcs.json'))==fingerprint(runtime._record()),
             'trusted shutdown receipt differs from the held runtime outcome')
    _require(not (context.root/REPORT_NAME).exists() and not (context.root/'trust-completion.json').exists()
             and not (context.root/'reports').exists() and not (context.root/'trust-publication-failure.json').exists(),
             'trusted reporting output already exists')
    pending=context.root/'trust-publication-pending.json'
    pending_value={'schema_version':1,**context.publication_binding,'status':'PUBLISHING'}
    _exclusive(pending,pending_value)  # Persist a negative fence before any report/commit can become visible.
    pending_digest=hashlib.sha256((json.dumps(pending_value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()).hexdigest()
    _publication_fence(context.root,pending_digest)
    names=_source_names(context.root)
    sources={name:_sha(context.root/name) for name in names}
    case=_case(context.profile,context.root,run,manifest,failed=failure is not None,
               cancelled=has_cancellation(failure) if failure is not None else False,
               expected_journal=result['trust_api'] if result is not None else None,
               child_unknown=getattr(failure,'child_shutdown_status',None)=='UNCONFIRMED')
    _require(_source_names(context.root)==names and all(_sha(context.root/name)==digest for name,digest in sources.items()),
             'trusted sources changed during Case projection')
    case_bytes=(json.dumps(case,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    sources[REPORT_NAME]=hashlib.sha256(case_bytes).hexdigest()
    _exclusive(context.root/REPORT_NAME,case)
    _require(_sha(context.root/REPORT_NAME)==sources[REPORT_NAME], 'generated trusted Case file was replaced')
    completion={'schema_version':1,'contract':COMPLETION_CONTRACT,'status':'FINALIZED','run_id':run['run_id'],
                'run_context_digest':run['run_context_digest'],'sources':sources,'ready_to_execute':False}
    from .reporting import write_reports
    from .reporting import _validate_case
    case_issues=_validate_case(manifest['cases'][0],case,['api','cli','sqlite'])
    summary=_summary(context.profile,run,manifest,case,case_issues)
    outputs=write_reports(summary,context.root/'reports',{'all'})
    registration.validate(runtime)
    _require(_source_names(context.root)|{REPORT_NAME}==set(sources)
             and all(_sha(context.root/name)==digest for name,digest in sources.items()),
             'trusted sources changed during report publication')
    _exclusive(context.root/'trust-completion.json',completion)  # Last artifact written; pending removal below commits publication.
    verified=build_trust_report(context.profile,context.root,_pending_digest=pending_digest)
    _require(verified['status']==summary['status'] and verified['integrity']==summary['integrity'],
             'trusted published completion did not verify')
    published={'status':verified['status'],'outputs':outputs['outputs'],'summary':verified}
    _publication_fence(context.root,pending_digest)
    pending.unlink()  # Commit point: no fallible I/O or verification after this operation.
    # No post-unlink fsync: a crash may conservatively restore the pending fence,
    # but must never convert a failed pre-commit publication into success.
    return published


def publish_trust_report(registration, runtime, *, result=None, failure=None):
    attempted_before=getattr(registration.context,'publication_attempted',False)
    try:
        return _publish_trust_report(registration,runtime,result=result,failure=failure)
    except BaseException as error:
        cancelled=has_cancellation(error) or (failure is not None and has_cancellation(failure))
        context=registration.context
        # Preserve any already-written completion/Case artifacts. A separate
        # negative receipt vetoes publication, including a late seal failure.
        if not attempted_before and context is not None and hasattr(context,'publication_root'):
            path=context.publication_root/'trust-publication-failure.json'
            try:
                if not path.exists() and not path.is_symlink():
                    _exclusive(path,{'schema_version':1,**context.publication_binding,
                                     'status':'CANCELLED' if cancelled else 'BLOCKED','ready_to_execute':False})
            except BaseException as notice:
                cancelled=cancelled or has_cancellation(notice)
        public=KeyboardInterrupt('trusted report publication cancelled') if cancelled else RunStateError(
            'trusted canonical report publication failed; retain source and partial report artifacts')
        if (getattr(error,'child_shutdown_status',None)=='UNCONFIRMED'
                or getattr(failure,'child_shutdown_status',None)=='UNCONFIRMED'):
            public.child_shutdown_status='UNCONFIRMED'
        raise public from None


def render_trust_report(summary, root, output_dir, formats):
    """Offline viewing must never write into a live publisher's artifact tree."""
    root=Path(root).resolve()
    if output_dir is None:
        if not (root/'trust-completion.json').is_file() or summary['integrity']['status'] != 'PASS':
            return {'status':summary['status'],'outputs':{},'summary':summary}
        output=root/'reports'
    else:
        output=Path(output_dir)
        _require(not output.is_symlink(), 'trusted report output cannot be a symlink')
        resolved=output.resolve()
        # Explicit diagnostic exports must stay wholly outside the owned Run.
        _require(not resolved.is_relative_to(root) and not root.is_relative_to(resolved),
                 'trusted explicit report export must be outside the owned Run')
    from .reporting import write_reports
    return write_reports(summary,output,formats)
