import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { getBotEntryAvailability, type BotActionAvailability, type BotDomain } from '@/services/botWorkshop';
import React from 'react';
import BotDescriptionCell from './BotDescriptionCell';
import { BotContainerInfo } from './BotContainerInfo';
import BotInfoCell from './BotInfoCell';
import { BotManagementMenu } from './BotManagementMenu';
import BotPrimaryActionsCell from './BotPrimaryActionsCell';
import BotStatusCell from './BotStatusCell';
import BotTagsCell from './BotTagsCell';
import BotVersionCell from './BotVersionCell';
import type { BotCardManagementAction } from './config';
import { DesktopOfflineNotice, DesktopStartProgress } from './DesktopStartProgress';
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
  const columns: DataTableColumn<BotDomain>[] = React.useMemo(
    () => [
      {
        id: 'info',
        header: 'Bot信息',
        width: 'w-[clamp(192px,16vw,240px)]',
        cell: (bot) => <BotInfoCell bot={bot} onClaimLock={onClaimLock} onReleaseLock={onReleaseLock} />,
      },
      {
        id: 'description',
        header: '描述',
        width: 'w-[clamp(160px,14vw,260px)]',
        cell: (bot: BotDomain) => <BotDescriptionCell bot={bot} />,
      },
      {
        id: 'status',
        header: '状态',
        width: 'w-[clamp(96px,8vw,112px)]',
        cell: (bot) => (
          <BotStatusCell
            bot={bot}
            progressExpanded={expandedProgressIds.has(bot.id)}
            onToggleProgress={() => toggleProgress(bot.id)}
          />
        ),
      },
      {
        id: 'version',
        header: '版本',
        width: 'w-[clamp(60px,5vw,72px)]',
        cell: (bot) => <BotVersionCell bot={bot} />,
      },
      {
        id: 'tags',
        header: 'Bot 标签',
        width: 'w-[clamp(120px,10vw,160px)]',
        cell: (bot) => <BotTagsCell bot={bot} />,
      },
      {
        id: 'containers',
        header: '容器信息',
        width: 'w-[clamp(108px,9vw,128px)]',
        cell: (bot) =>
          onOpenContainers ? (
            <BotContainerInfo
              bot={bot}
              userId={containerUserId}
              canView={canViewContainers?.(bot) ?? true}
              onOpen={onOpenContainers}
            />
          ) : (
            <span>—</span>
          ),
      },
      ...(showOwner
        ? [
            {
              id: 'owner',
              header: 'Owner',
              width: 'w-[clamp(96px,8vw,120px)]',
              cell: (bot: BotDomain) => (
                <span className="truncate text-xs text-foreground">{bot.ownerName || bot.ownerId || '—'}</span>
              ),
            },
          ]
        : []),
      {
        id: 'primary-actions',
        header: '操作',
        width: 'w-[284px]',
        align: 'end',
        className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
        headerClassName: 'sticky right-0 z-10 bg-muted/50',
        cell: (bot) => {
          const entry = getBotEntryAvailability(bot);
          const collaborationAllowsConversation = canOpenConversation?.(bot) ?? true;
          const primaryCell = (
            <BotPrimaryActionsCell
              bot={bot}
              onView={onView}
              onEdit={onEdit}
              onConversation={onConversation}
              conversationAllowed={collaborationAllowsConversation && entry.enabled}
              conversationDisabledReason={
                entry.enabled ? (collaborationAllowsConversation ? undefined : '申请协作权限后才可对话') : entry.disabledReason
              }
              onOpenLogs={onOpenLogs}
              inventoryActions={getInventoryActions?.(bot)}
              logAction={getLogAction?.(bot)}
            />
          );
          // 三个点与四个操作链接同处一个 gap-0.5 flex 行，间距由同一 gap 保证
          // 与链接之间完全一致（合并原「更多操作」独立列造成的 td padding 空隙）。
          if (!onAction) return primaryCell;
          const menuKey = bot.cardId ?? bot.entityKey;
          const generalConfigAvailability = getGeneralConfigAvailability?.(bot);
          return (
            <div className="flex items-center justify-end gap-0.5" onClick={(event) => event.stopPropagation()}>
              {primaryCell}
              <BotManagementMenu
                bot={bot}
                open={openMenuKey === menuKey}
                onOpenChange={(open) => setOpenMenuKey((current) => (open ? menuKey : current === menuKey ? undefined : current))}
                collaborationMode={getCollaborationMode?.(bot)}
                lockedByOther={bot.lock?.status === 'other'}
                onAction={onAction}
                onManagePublication={onManagePublication}
                onHealthCheck={onHealthCheck}
                healthCheckAvailability={getHealthCheckAvailability?.(bot)}
                onOpenTaskInstances={onOpenTaskInstances}
                canOpenTaskInstances={canOpenTaskInstances?.(bot)}
                onChangeSpace={onChangeSpace}
                onAuthorize={onAuthorize}
                canGeneralConfig={generalConfigAvailability?.enabled}
                generalConfigDisabledReason={generalConfigAvailability?.disabledReason}
                onGeneralConfig={onGeneralConfig}
              />
            </div>
          );
        },
      },
    ],
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
  );
};

export default BotTable;
