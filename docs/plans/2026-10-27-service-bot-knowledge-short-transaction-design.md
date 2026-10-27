# Service Bot knowledge selection: simple, knowledge-owned state

Approved by the user on 2026-10-27 after rejecting native table expansion and a
new knowledge publication table. Keep the original knowledge binding columns
and publication.ext snapshot, and accept short local transaction coupling.
No native table schema changes, custom scheduler, queue or extra DB table.

Build freezes selection; actual successful deployment replaces online selection
in the same DB Session/transaction as native status CAS. Restore uses the native
operation ledger. Unknown history is a knowledge result, not a native gate.
SQL/persistence errors still fail and roll back local completion; the completion
retry must not send another external deploy. No new remote work under locks.

See apps/backend/specs/2026-10-27-knowledge-short-transaction/ for acceptance,
implementation and verification. The earlier 8e1b81a3d journal/projector design
is superseded, not an accepted delivery.
