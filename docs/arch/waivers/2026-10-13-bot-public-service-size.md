# Existing BotPublicService size exception

- Scope: `apps/backend/src/agentclaw/community/core/bot_public/services/bot_public_service.py`.
- Owner: Backend maintainers; review is part of the async BCSFuse notification PR.
- Expires: 2026-10-27.
- Baseline: 1,867 lines before this change; 1,883 lines after it.

The incident fix adds only a dispatch wrapper and preserves the publication and
approval paths. New background execution lives in a separate module below the 1,000-line limit.
Splitting the existing publication, approval, friend, and discover behavior in
this patch would broaden the change beyond the requested callback fix.

Follow-up: split BotPublicService by those responsibilities before extending it
further or renewing this exception. No CI gate or source-size allowlist is weakened.
