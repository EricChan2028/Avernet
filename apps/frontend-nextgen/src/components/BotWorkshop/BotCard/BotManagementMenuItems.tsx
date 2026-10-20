import { Button } from '@/components/ui/Button';
import { PopoverContent } from '@/components/ui/Popover';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, ClipboardList, HeartPulse, MapPin, ShieldCheck, Users } from 'lucide-react';
import { canRestartFailedServiceBot, failedServiceDeleteReason, publicationManagementAvailability } from './botManagementPolicy';
import { actionIcon, actionLabel, type BotCardManagementAction } from './config';
import { ActionHelp, MenuSection } from './menuPrimitives';

interface BotManagementMenuItemsProps {
  bot: BotDomain;
  collaborationMode?: 'authorize' | 'request';
  lockedByOther: boolean;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onManagePublication?: (bot: BotDomain) => void;
  /** 以下两项与任务实例原为行内图标按钮，按 dmore 稿 087 收口进「三个点」气泡。 */
  onHealthCheck?: (bot: BotDomain) => void;
  healthCheckAvailability?: BotActionAvailability;
  onOpenTaskInstances?: (bot: BotDomain) => void;
  canOpenTaskInstances?: boolean;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  canGeneralConfig?: boolean;
  generalConfigDisabledReason?: string;
  onGeneralConfig?: (bot: BotDomain) => void;
  /** 关闭气泡（弹确认框/跳转前收起菜单）；onRequestConfirm 触发需二次确认的操作。 */
  onMenuClose: () => void;
  onRequestConfirm: (action: BotCardManagementAction) => void;
}

/**
 * 「三个点」气泡内的菜单分组本体（配置类置顶 → 日常操作 → 发布与运维 → 危险区垫底）。
 * 派生可用性状态与菜单渲染同文件维护，BotManagementMenu 只负责气泡开关与确认弹窗。
 */
export function BotManagementMenuItems({
  bot,
  collaborationMode,
  lockedByOther,
  onAction,
  onManagePublication,
  onHealthCheck,
  healthCheckAvailability,
  onOpenTaskInstances,
  canOpenTaskInstances,
  onChangeSpace,
  onAuthorize,
  canGeneralConfig,
  generalConfigDisabledReason,
  onGeneralConfig,
  onMenuClose,
  onRequestConfirm,
}: BotManagementMenuItemsProps) {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const isServiceBot = bot.serviceMode === 'service';
  const restartUnsupported = bot.runtime.engine === 'teclaw';
  const restartDeclared = bot.actions.includes('restart') || Boolean(bot.disabledActions.restart) || canRestartFailedServiceBot(bot);
  const restartPublishDeclared =
    bot.actions.includes('restart_publish') || Boolean(bot.disabledActions.restart_publish);
  const restartPublishDisabledReason = bot.disabledActions.restart_publish;
  const restartDisabledReason = bot.disabledActions.restart;
  const engineRestartDeclared = bot.actions.includes('engine_restart') || Boolean(bot.disabledActions.engine_restart);
  const showEngineRestart =
    !isAgentCodingBot && (restartUnsupported || !isServiceBot || engineRestartDeclared);
  const engineRestartEnabled = bot.actions.includes('engine_restart');
  const engineRestartDisabledReason = bot.disabledActions.engine_restart;
  const publication = publicationManagementAvailability(bot);
  const restartEnabled = bot.actions.includes('restart') || canRestartFailedServiceBot(bot);
  const deleteReason = failedServiceDeleteReason(bot);
  return (
    <PopoverContent align="end" className="w-52 space-y-1 p-2">
      {/* 配置类：Bot 的配置与形态变更操作（验收修正：配置类同组置顶） */}
      <MenuSection>
        {onGeneralConfig ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<ShieldCheck className="size-4" />}
            disabled={!canGeneralConfig}
            onClick={() => {
              onMenuClose();
              onGeneralConfig(bot);
            }}
          >
            {canGeneralConfig ? (
              '通用配置'
            ) : (
              <ActionHelp description={generalConfigDisabledReason ?? '当前不可用'}>通用配置</ActionHelp>
            )}
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start"
          leftIcon={<MapPin className="size-4" />}
          disabled={lockedByOther}
          onClick={() => {
            onMenuClose();
            onChangeSpace?.(bot);
          }}
        >
          变更归属空间
        </Button>
        {bot.serviceMode === 'non-service' && bot.deployment === 'cloud' && bot.canUpgradeToService ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.upgrade}
            disabled={lockedByOther}
            onClick={() => {
              onMenuClose();
              onRequestConfirm('upgrade');
            }}
          >
            {actionLabel.upgrade}
          </Button>
        ) : null}
      </MenuSection>
      {/* 日常操作（协作与查看） */}
      <MenuSection divider>
        {collaborationMode && onAuthorize ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<Users className="size-4" />}
            onClick={() => {
              onMenuClose();
              onAuthorize(bot);
            }}
          >
            {collaborationMode === 'authorize' ? '授权' : '申请操作权限'}
          </Button>
        ) : null}
        {onOpenTaskInstances && (isAgentCodingBot || canOpenTaskInstances) ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<ClipboardList className="size-4" />}
            aria-label={`查看 ${bot.name} 任务实例`}
            onClick={() => {
              onMenuClose();
              onOpenTaskInstances(bot);
            }}
          >
            任务实例
          </Button>
        ) : null}
        {healthCheckAvailability?.visible && onHealthCheck ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<HeartPulse className="size-4" />}
            aria-label={`${bot.name} 健康检查`}
            disabled={lockedByOther || !healthCheckAvailability.enabled}
            onClick={() => {
              onMenuClose();
              onHealthCheck(bot);
            }}
          >
            <ActionHelp
              description={
                lockedByOther
                  ? '该 Bot 正被他人编辑，请先抢锁'
                  : healthCheckAvailability.disabledReason ?? '对该 Bot 的引擎与协作链路执行一次集成健康检查。'
              }
            >
              健康检查
            </ActionHelp>
          </Button>
        ) : null}
        {bot.deployment === 'local' && bot.actions.includes('open_folder') ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            onClick={() => {
              onMenuClose();
              void onAction('open_folder', bot);
            }}
          >
            打开本地目录
          </Button>
        ) : null}
      </MenuSection>
      {/* 发布与运维（流程与运行态操作 · 需二次确认或受锁保护） */}
      <MenuSection divider>
        {bot.serviceMode === 'service' && publication.visible && onManagePublication ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<ArrowUpRight className="size-4" />}
            disabled={lockedByOther || !publication.enabled}
            onClick={() => {
              onMenuClose();
              onManagePublication(bot);
            }}
          >
            {publication.enabled ? '发布与阶段推进' : <ActionHelp description={publication.disabledReason!}>发布与阶段推进</ActionHelp>}
          </Button>
        ) : null}
        {restartUnsupported || !isServiceBot || restartDeclared ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.restart}
            disabled={restartUnsupported || lockedByOther || !restartEnabled}
            onClick={() => {
              onMenuClose();
              onRequestConfirm('restart');
            }}
          >
            <ActionHelp
              description={
                restartUnsupported
                  ? 'TeClaw 暂不支持重启容器'
                  : restartDisabledReason && !bot.actions.includes('restart')
                  ? restartDisabledReason
                  : '指重新启动当前 Bot 实例，重新加载当前会话状态、配置或运行流程。'
              }
            >
              {actionLabel.restart}
            </ActionHelp>
          </Button>
        ) : null}
        {restartPublishDeclared ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.restart_publish}
            disabled={restartUnsupported || lockedByOther || !bot.actions.includes('restart_publish')}
            onClick={() => {
              onMenuClose();
              onRequestConfirm('restart_publish');
            }}
          >
            <ActionHelp
              description={
                restartUnsupported
                  ? 'TeClaw 暂不支持重启发布'
                  : restartPublishDisabledReason && !bot.actions.includes('restart_publish')
                  ? restartPublishDisabledReason
                  : '指重新启动该服务已发布环境（预发/线上）的运行时，不影响草稿机器。'
              }
            >
              {actionLabel.restart_publish}
            </ActionHelp>
          </Button>
        ) : null}
        {showEngineRestart ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.engine_restart}
            disabled={restartUnsupported || lockedByOther || !engineRestartEnabled}
            onClick={() => {
              onMenuClose();
              onRequestConfirm('engine_restart');
            }}
          >
            <ActionHelp
              description={
                restartUnsupported
                  ? 'TeClaw 暂不支持重启引擎'
                  : engineRestartDisabledReason && !engineRestartEnabled
                  ? engineRestartDisabledReason
                  : '指重新启动 Bot 所依赖的底层运行引擎（如 OpenClaw、ClaudeCode 等）。'
              }
            >
              {actionLabel.engine_restart}
            </ActionHelp>
          </Button>
        ) : null}
      </MenuSection>
      {/* 危险区（高风险垫底） */}
      <MenuSection divider>
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start text-destructive"
          leftIcon={actionIcon.delete}
          disabled={lockedByOther || !bot.actions.includes('delete')}
          onClick={() => {
            onMenuClose();
            onRequestConfirm('delete');
          }}
        >
          {deleteReason ? <ActionHelp description={deleteReason}>{actionLabel.delete}</ActionHelp> : actionLabel.delete}
        </Button>
      </MenuSection>
    </PopoverContent>
  );
}