import { DataTable } from '@/components/ui/DataTable';
import { useBotTableEngineVersions } from '@/hooks/useBotTableEngineVersions';
import { getBotEntryAvailability, type BotActionAvailability, type BotDomain } from '@/services/botWorkshop';
import React from 'react';
import { buildBotTableColumns } from './botTableColumns';
import { DesktopOfflineNotice, DesktopStartProgress } from './DesktopStartProgress';
import type { BotCardManagementAction } from './config';
export type BotInventoryActions = Partial<Record<'view' | 'chat' | 'edit', BotActionAvailability>>;
export interface BotTableProps {
  bots: BotDomain[];
  /** 表格可访问名称 */
  ariaLabel?: string;
  onRowClick?: (bot: BotDomain) => void;
  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onConversation?: (bot: BotDomain) => void;
  canOpenConversation?: (bot: BotDomain) => boolean;
  /** 无详情权限时，整行及查看按钮仅打开基础信息，不进入编辑页。 */
  canEnterDetail?: (bot: BotDomain) => boolean;
  onOpenLogs?: (bot: BotDomain) => void;
  onOpenTaskInstances?: (bot: BotDomain) => void;
  canOpenTaskInstances?: (bot: BotDomain) => boolean;
  onOpenContainers?: (bot: BotDomain) => void;
  canViewContainers?: (bot: BotDomain) => boolean;
  /** 容器接口所需的已解析请求身份。 */
  containerUserId?: string;
  onHealthCheck?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  onManagePublication?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;
  /** 引擎版本观察/更新完成后的回调（index 传入静默刷新列表）。 */
  onEngineVersionUpdated?: (bot: BotDomain) => Promise<void> | void;

  getInventoryActions?: (bot: BotDomain) => BotInventoryActions | undefined;
  getHealthCheckAvailability?: (bot: BotDomain) => BotActionAvailability | undefined;
  getLogAction?: (bot: BotDomain) => BotActionAvailability | undefined;
  getCollaborationMode?: (bot: BotDomain) => 'authorize' | 'request' | undefined;
  /** 通用配置菜单项门禁（collab-permission-entry-migration）；与 onGeneralConfig 成对传入。 */
  getGeneralConfigAvailability?: (bot: BotDomain) => { enabled: boolean; disabledReason?: string } | undefined;
  onGeneralConfig?: (bot: BotDomain) => void;
}

const BotTable: React.FC<BotTableProps> = ({
  bots,
  ariaLabel = 'Bot 列表',
  onRowClick,
  onView,
  onEdit,
  onConversation,
  canOpenConversation,
  canEnterDetail,
  onOpenLogs,
  onOpenTaskInstances,
  canOpenTaskInstances,
  onOpenContainers,
  canViewContainers,
  containerUserId,
  onHealthCheck,
  onChangeSpace,
  onAuthorize,
  onManagePublication,
  onAction,
  onClaimLock,
  onReleaseLock,
  onEngineVersionUpdated,
  getInventoryActions,
  getHealthCheckAvailability,
  getLogAction,
  getCollaborationMode,
  getGeneralConfigAvailability,
  onGeneralConfig,
}) => {
  const showOwner = bots.some((bot) => bot.spaceKind === 'team');
  const [expandedProgressIds, setExpandedProgressIds] = React.useState<Set<string>>(() => new Set());
  const [openMenuKey, setOpenMenuKey] = React.useState<string>();
  const toggleProgress = React.useCallback((botId: string) => {
    setExpandedProgressIds((current) => {
      const next = new Set(current);
      if (next.has(botId)) next.delete(botId);
      else next.add(botId);
      return next;
    });
  }, []);
  const engineVersions = useBotTableEngineVersions({
    bots,
    userId: containerUserId,
    onUpdated: onEngineVersionUpdated,
  });
  const columns = React.useMemo(
    () =>
      buildBotTableColumns({
        showOwner,
        expandedProgressIds,
        toggleProgress,
        openMenuKey,
        setOpenMenuKey,
        containerUserId,
        engineVersionStates: engineVersions.states,
        engineVersionUpdateAvailability: engineVersions.updateAvailability,
        onOpenEngineInfo: engineVersions.openInfo,
        onUpdateEngineVersion: engineVersions.openUpdate,
        onView,
        onEdit,
        onConversation,
        canOpenConversation,
        onOpenLogs,
        onOpenTaskInstances,
        canOpenTaskInstances,
        onOpenContainers,
        canViewContainers,
        onHealthCheck,
        onChangeSpace,
        onAuthorize,
        onManagePublication,
        onAction,
        onClaimLock,
        onReleaseLock,
        getInventoryActions,
        getHealthCheckAvailability,
        getLogAction,
        getCollaborationMode,
        getGeneralConfigAvailability,
        onGeneralConfig,
      }),
    [
      showOwner,
      onView,
      onEdit,
      onConversation,
      canOpenConversation,
      onOpenLogs,
      onOpenTaskInstances,
      canOpenTaskInstances,
      onOpenContainers,
      canViewContainers,
      containerUserId,
      onHealthCheck,
      onChangeSpace,
      onAuthorize,
      onManagePublication,
      onAction,
      onClaimLock,
      onReleaseLock,
      getInventoryActions,
      getHealthCheckAvailability,
      getLogAction,
      getCollaborationMode,
      openMenuKey,
      expandedProgressIds,
      toggleProgress,
      getGeneralConfigAvailability,
      onGeneralConfig,
      engineVersions.states,
      engineVersions.updateAvailability,
      engineVersions.openInfo,
      engineVersions.openUpdate,
    ],
  );
  const handleRowClick = React.useCallback(
    (bot: BotDomain) => {
      if (!getBotEntryAvailability(bot).enabled) return;
      if (onRowClick) {
        onRowClick(bot);
        return;
      }
      if (canEnterDetail && !canEnterDetail(bot)) {
        onView(bot);
        return;
      }
      if (bot.runtime.isAgentCodingBot && onConversation && (canOpenConversation?.(bot) ?? true)) {
        onConversation(bot);
        return;
      }
      onView(bot);
    },
    [onRowClick, onView, onConversation, canOpenConversation, canEnterDetail],
  );

  return (
    <>
      <DataTable
        columns={columns}
        rows={bots}
        getRowKey={(bot) => bot.cardId ?? bot.entityKey}
        onRowClick={handleRowClick}
        isRowClickable={(bot) => getBotEntryAvailability(bot).enabled}
        rowHeight="h-18"
        renderExpandedRow={(bot) =>
          bot.deployment === 'local' &&
          ['deploying', 'failed', 'offline'].includes(bot.lifecycle) &&
          expandedProgressIds.has(bot.id) ? (
            bot.lifecycle === 'offline' ? <DesktopOfflineNotice botId={bot.id} /> : <DesktopStartProgress botId={bot.id} />
          ) : null
        }
        ariaLabel={ariaLabel}
        minWidth={showOwner ? 'min-w-[1212px]' : 'min-w-[1132px]'}
      />
      {engineVersions.dialogs}
    </>
  );
};

export default BotTable;