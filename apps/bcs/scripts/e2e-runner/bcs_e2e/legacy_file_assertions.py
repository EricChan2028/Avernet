"""Explicit native file DTO semantics for the complete CLI-file migration.

No route/auth fallback or side effects. Native enums are not the public-v1 wire
format. Anonymous metadata must omit session identity and internal storage data;
we never fabricate a session field to make it pass the authenticated validator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .file_assertions import FileExpectation

_METADATA = {'file_id','session_id','file_name','mime_type','size','sha256','owner',
             'storage_backend','status','created_at','updated_at'}
_SHARED = _METADATA - {'session_id'}
_OWNER = {'Bot':'bot', 'Human':'human'}


def _semantic(value: object, *, shared: bool):
    if not isinstance(value, dict) or set(value) != (_SHARED if shared else _METADATA):
        return None
    actor=value['owner']
    if (not isinstance(actor,dict) or set(actor)!={'actor_kind','actor_id'}
            or actor.get('actor_kind') not in ('Bot','Human') or value['status'] != 'Ready'):
        return None
    return {**value, 'status':'ready', 'owner':{'actor_kind':_OWNER[actor['actor_kind']], 'actor_id':actor['actor_id']}}


@dataclass(frozen=True)
class LegacyFileExpectation:
    file: FileExpectation

    def matches_metadata(self, value: object) -> bool:
        normalized=_semantic(value, shared=False)
        return normalized is not None and self.file.matches_metadata(normalized)

    def matches_shared_metadata(self, value: object) -> bool:
        normalized=_semantic(value, shared=True)
        if normalized is None:
            return False
        expected=self.file
        return (normalized['file_id']==expected.file_id and normalized['file_name']==expected.file_name
                and normalized['mime_type']==expected.mime_type
                and type(normalized['size']) is int and normalized['size']==expected.size
                and normalized['sha256'] in (None,expected.sha256)
                and normalized['owner']=={'actor_kind':expected.owner_kind,'actor_id':expected.owner_id}
                and normalized['storage_backend']==expected.storage_backend
                and all(type(normalized[key]) is int and normalized[key]>=0 for key in ('created_at','updated_at')))

    def matches_fresh_page(self, value: object) -> bool:
        # The file Story owns a fresh Session with exactly one uploaded file.
        # A prefix page or an unrelated nonzero total must not qualify it.
        return (isinstance(value,dict) and set(value)=={'items','total'}
                and type(value['total']) is int and value['total']==1
                and isinstance(value['items'],list) and len(value['items'])==1
                and self.matches_metadata(value['items'][0]))

    def matches_bytes(self, value: object) -> bool:
        return (isinstance(value,bytes) and len(value)==self.file.size
                and hashlib.sha256(value).hexdigest()==self.file.sha256)

    def matches_local_capabilities(self, value: object, *, max_size: int) -> bool:
        # A local-only assertion: remote backends need their explicitly reviewed
        # expectation rather than silently replacing local with a returned value.
        return (self.file.storage_backend=='local' and type(max_size) is int and max_size>=self.file.size
                and isinstance(value,dict) and set(value)=={'storage','presign_upload','presign_download','inline_view','max_size'}
                and value['storage']=='local' and value['presign_upload'] is False
                and value['presign_download'] is False and value['inline_view'] is True
                and type(value['max_size']) is int and value['max_size']==max_size)
