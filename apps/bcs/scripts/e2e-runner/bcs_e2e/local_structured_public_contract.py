"""Internal-v1 template read slice from the reviewed Structured Story."""
from __future__ import annotations

from dataclasses import dataclass

from .case_adapters import AdapterCall
from .case_executor import CaseExecution, StepOutcome
from .local_structured_contract import PARENT, structured_source_binding
from .run_state import RunStateError

CASE_ID = 'PUBLIC-STRUCTURED-TEMPLATES-01'
SUITE = 'public-structured-templates-v1'
REQUIREMENT_IDS = ('STRUCTURED.templates-list', 'STRUCTURED.template-detail')
ADAPTER_METHOD = 'read_public_templates'
PUBLIC_LIST_PATH = '/api/v1/collaboration/templates?lang=zh-CN'
PUBLIC_DETAIL_PATH = '/api/v1/collaboration/templates/single-bot-guided-answer?lang=zh-CN&format=json'
TEMPLATE_ID = 'single-bot-guided-answer'
TEMPLATE_LANG = 'zh-CN'
_REQUEST_METHODS = ('GET', 'GET')
_REQUEST_OPERATIONS = ('structured.templates.list', 'structured.template.detail')
_REQUEST_STATUS = (200, 200)


@dataclass(frozen=True)
class StructuredTemplatesRequirement:
    requirement_id: str
    source_method: str
    source_path: str
    public_path: str
    expectation: str

    def validate(self) -> None:
        expected = {
            REQUIREMENT_IDS[0]: ('GET', '/collaboration/templates?lang=zh-CN',
                                 PUBLIC_LIST_PATH),
            REQUIREMENT_IDS[1]: ('GET', ('/collaboration/templates/' + TEMPLATE_ID
                                        + '?lang=zh-CN&format=json'),
                                 PUBLIC_DETAIL_PATH),
        }
        method, source_path, public_path = expected[self.requirement_id]
        if (self.source_method != method or self.source_path != source_path
                or self.public_path != public_path or not self.expectation):
            raise RunStateError('public structured-template requirement is invalid')


@dataclass(frozen=True)
class StructuredTemplatesCasePlan:
    case_id: str
    parent_id: str
    requirements: tuple[StructuredTemplatesRequirement, ...]
    adapter_method: str
    local_executor_status: str
    remote_validation: str
    grants_release_credit: bool

    def validate(self) -> None:
        if (self.case_id != CASE_ID or self.parent_id != PARENT
                or self.adapter_method != ADAPTER_METHOD
                or self.local_executor_status != 'LOCAL_FULL_EXECUTOR'
                or self.remote_validation != 'NOT_RUN'
                or self.grants_release_credit is not False
                or tuple(row.requirement_id for row in self.requirements) != REQUIREMENT_IDS):
            raise RunStateError('public structured-template case is incomplete')
        for requirement in self.requirements:
            requirement.validate()


def structured_templates_case_plan() -> StructuredTemplatesCasePlan:
    binding = structured_source_binding()
    if binding['parent'] != PARENT:
        raise RunStateError('structured-template source parent changed')
    source_rows = [row for row in binding['requirements'] if row['id'] in REQUIREMENT_IDS]
    if [row['id'] for row in source_rows] != list(REQUIREMENT_IDS):
        raise RunStateError('structured-template source obligations changed')
    plan = StructuredTemplatesCasePlan(
        case_id=CASE_ID,
        parent_id=PARENT,
        requirements=(
            StructuredTemplatesRequirement(
                requirement_id=source_rows[0]['id'], source_method=source_rows[0]['method'],
                source_path=source_rows[0]['path'], public_path=PUBLIC_LIST_PATH,
                expectation=source_rows[0]['expectation'],
            ),
            StructuredTemplatesRequirement(
                requirement_id=source_rows[1]['id'], source_method=source_rows[1]['method'],
                source_path=source_rows[1]['path'], public_path=PUBLIC_DETAIL_PATH,
                expectation=source_rows[1]['expectation'],
            ),
        ),
        adapter_method=ADAPTER_METHOD,
        local_executor_status='LOCAL_FULL_EXECUTOR',
        remote_validation='NOT_RUN',
        grants_release_credit=False,
    )
    plan.validate()
    return plan


def structured_templates_adaptability() -> dict:
    plan = structured_templates_case_plan()
    return {
        'case_id': plan.case_id,
        'legacy_id': plan.parent_id,
        'requirement_ids': list(REQUIREMENT_IDS),
        'selected_obligations': ['list', 'detail'],
        'public_paths': [PUBLIC_LIST_PATH, PUBLIC_DETAIL_PATH],
        'environments': ['owned-local-full-story', 'singlebox-public-slice',
                        'preprod-public-slice'],
        'local_executor_status': plan.local_executor_status,
        'remote_validation': plan.remote_validation,
        'grants_release_credit': plan.grants_release_credit,
        'blocker': ('This is a read-only template slice. The Structured group, definition, '
                    'run, and cleanup obligations remain separate and must not receive '
                    'credit from this slice.'),
    }


def validate_structured_templates_adapter(adapter: object) -> None:
    plan = structured_templates_case_plan()
    method = getattr(adapter, plan.adapter_method, None)
    if not callable(method):
        raise RunStateError('structured-template adapter is missing ' + ADAPTER_METHOD)


def _validate_result(plan: StructuredTemplatesCasePlan, result: AdapterCall) -> None:
    evidence = result.evidence
    requests = evidence.get('requests')
    if (not isinstance(evidence, dict)
            or evidence.get('fixture_effect') != 'NONE'
            or not isinstance(requests, list) or len(requests) != 2
            or [row.get('operation') for row in requests] != list(_REQUEST_OPERATIONS)
            or [row.get('method') for row in requests] != list(_REQUEST_METHODS)
            or [row.get('http_status') for row in requests] != list(_REQUEST_STATUS)
            or not all(row.get('credentials_sent') is True
                       and row.get('authorization_sent') is False for row in requests)):
        raise RunStateError('structured-template evidence is outside the reviewed read slice')
    projection = evidence.get('projection')
    if (not isinstance(projection, dict)
            or projection.get('selected_template_id') != TEMPLATE_ID
            or projection.get('detail_template_id') != TEMPLATE_ID
            or projection.get('detail_lang') != TEMPLATE_LANG
            or type(projection.get('list_item_count')) is not int
            or projection.get('list_item_count', 0) < 1
            or type(projection.get('detail_yaml_chars')) is not int
            or projection.get('detail_yaml_chars', 0) < 1
            or projection != result.data):
        raise RunStateError('structured-template result does not prove the reviewed catalog reads')
    expected_paths = [plan.requirements[0].public_path, plan.requirements[1].public_path]
    if [row.get('path') for row in requests] != expected_paths:
        raise RunStateError('structured-template request path differs from its frozen contract')


def execute_structured_templates_case(adapter: object) -> CaseExecution:
    plan = structured_templates_case_plan()
    validate_structured_templates_adapter(adapter)
    method = getattr(adapter, plan.adapter_method)
    try:
        result = method()
        if not isinstance(result, AdapterCall):
            raise RunStateError('structured-template method did not return AdapterCall')
        result.validate()
        _validate_result(plan, result)
        projection = result.evidence['projection']
        steps = tuple(
            StepOutcome(
                requirement_id=requirement_id,
                method=ADAPTER_METHOD,
                status='PASS',
                data={'template_id': projection['detail_template_id']}
                      if requirement_id == REQUIREMENT_IDS[1]
                      else {'template_id': projection['selected_template_id'],
                            'item_count': projection['list_item_count']},
                evidence={'request': dict(request)},
                error_type=None,
            )
            for requirement_id, request in zip(REQUIREMENT_IDS, result.evidence['requests'])
        )
        completed, error_type = 2, None
    except Exception as error:
        completed, error_type = 0, type(error).__name__
        steps = tuple(
            StepOutcome(
                requirement_id=requirement_id,
                method=ADAPTER_METHOD,
                status='NOT_RUN',
                data=None,
                evidence={},
                error_type=error_type,
            )
            for requirement_id in REQUIREMENT_IDS
        )
    execution = CaseExecution(
        case_id=CASE_ID,
        parent_id=PARENT,
        status='PASS' if error_type is None else 'BLOCKED',
        steps=steps,
        completed_count=completed,
        total_count=2,
        error_type=error_type,
        ready_to_execute=error_type is None,
    )
    execution.validate()
    return execution
