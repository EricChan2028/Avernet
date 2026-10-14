"""Message templates pushed to the Bot for the BBS Browse Loop.

Runtime message kinds:

* ``browse_once`` — ask the Bot to execute one real ``bbs-browse`` Browse Run
  using the trusted Avernet service root supplied by the runtime.
* ``cron_event`` — ask an OpenClaw Bot to register or remove its fixed-name local
  ``*/30 * * * *`` cron task.
* ``skill_install`` — verify that the explicitly configured BBS skills are
  visible in the active Bot workspace; it never links a full skills store.
"""


from __future__ import annotations

import json
from typing import Literal

from agentclaw.community.core.task.forum.models import (
    BBS_BROWSE_LOOP_CRON_NAME,
    BrowseSubscriptionRecord,
)

def browse_once_message(*, bot_id: str, backend_base_url: str, subscription: BrowseSubscriptionRecord) -> str:
    """Compose a one-shot "run bbs-browse skill now" message for a Bot.

    The real BBS skill receives only the trusted service root and dispatch
    identity. It reads all OPEN Topics with the unified internal list API,
    delegates each Topic to ``bbs-topic-handler`` in a child session, and
    reports the run through ``browse-reports``. The runtime message deliberately
    does not carry per-bot feed or reply URLs because those paths belong to the
    skill contract.
    """
    plan = {
        "kind": "bbs-browse",
        "subscription_mode": subscription.mode,
        "bot_id": bot_id,
        "backend": backend_base_url.rstrip("/"),
    }
    body_lines = [
        "[BBS-BROWSE] 立即执行一次 bbs-browse 逛论坛流程,完成后退出本次对话。",
        "触发来源:" + ("框架 cron (A)" if subscription.mode == "framework" else "OpenClaw 自带 cron (B)"),
        f"bot_id={bot_id}",
        f"订阅 mode={subscription.mode}",
        "按规定顺序执行:",
        "  1) 使用 backend 分页读取全部 OPEN Topic,不得使用 actor-aware feed 替代统一列表",
        "  2) 对通过初筛的 Topic 派发 bbs-topic-handler 子 Session,由子 Session 读取全文楼层并决定回复/采纳/结帖",
        "  3) 汇总可信终态,并按要求上报 browse-reports",
        "短期上下文信息(运行时使用):",
        json.dumps(plan, ensure_ascii=False, indent=2),
    ]
    return "\n".join(body_lines)


def cron_event_message(*, bot_id: str, action: Literal["register", "remove"]) -> str:
    """Compose an instruction telling the Bot to add/remove a local cron job.

    Only meaningful for ``mode=openclaw``. The Bot uses OpenClaw's built-in
    ``cron`` tool to manage the local job; persistence semantics are owned by
    OpenClaw.
    """
    if action == "register":
        plan = (
            "用 OpenClaw 内置 cron 工具注册一个本地定时任务:",
            f"  - 固定任务名称: {BBS_BROWSE_LOOP_CRON_NAME!r} (必须完全一致,不要自创名称)",
            "  - 表达式: '*/30 * * * *' (每 30 分钟一次)",
            "  - 触发动作: 运行一次 bbs-browse 流程(统一读取 OPEN Topic→派发子 Session→汇总上报)",
            f"  - 若已存在同名任务({BBS_BROWSE_LOOP_CRON_NAME!r}),更新它即可(不要新建第二个)",
            "  - 失败/离线: 跳过本次,下一 tick 自然恢复",
            "  - 持久化: 依赖 OpenClaw cron 工具自身持久化策略",
        )
    else:
        plan = (
            "删除此前为 bbs-browse 注册的本地 cron 定时任务",
            f"  - 用 OpenClaw 内置 cron 工具按名称 {BBS_BROWSE_LOOP_CRON_NAME!r} 定位并删除",
            "  - 若无此名称任务,静默完成(幂等)",
        )
    return (
        "[BBS-BROWSE-CRON] " + ("请打开" if action == "register" else "请关闭") + f" bbs-browse 的本地 */30 cron。bot_id={bot_id}\n"
        + "\n".join(plan)
    )


# BBS skills the Bot must own locally (in the OpenClaw active pool) for the
# community feature to work: the browse flow, topic handling, and topic
# creation. Listed here so the join prompt — not the cron command — carries
# the names; cron stays thin.
BBS_BROWSE_SKILL_NAMES: tuple[str, ...] = (
    "bbs-browse",
    "bbs-topic-handler",
    "bbs-create-topic",
)


def _cn_listing(names: tuple[str, ...]) -> str:
    """中文顿号列举：单项原样、两项「A 和 B」、多项「A、B 和 C」。"""
    if len(names) == 1:
        return names[0]
    return "、".join(names[:-1]) + " 和 " + names[-1]


def bbs_skill_install_message(*, bot_id: str) -> str:
    """Compose a verification-only message for profile-preinstalled BBS skills.

    Singlebox installs active skills by copying explicitly configured profile
    skill directories into the Bot workspace. Subscription wiring never creates
    a bridge from the active directory to a full content store, so this message
    verifies availability and reports missing skills instead of symlinking from
    a pool.
    """
    listing = _cn_listing(BBS_BROWSE_SKILL_NAMES)
    return (
        f"[BBS-BROWSE-SKILL] 请验证 {listing} 已在当前 OpenClaw 工作区可用。bot_id={bot_id}\n"
        "检查路径优先级：\n"
        f"1) 检查当前工作区 workspace/skills/ 目录，确认是否存在 {listing}（目录及其 SKILL.md）。\n"
        "2) 已存在 → 报告「已可用」。\n"
        "3) 任一 skill 缺失 → 只报告「skill 缺失」，不要从完整内容库创建软链接或修改技能内容。\n"
        "操作完成后逐条回报（已可用 / skill 缺失）。该检查幂等。"
    )
