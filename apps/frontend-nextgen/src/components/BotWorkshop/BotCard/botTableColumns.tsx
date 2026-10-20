import type { DataTableColumn } from '@/components/ui/DataTable';
import type { EngineVersionUpdateAvailability } from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import { botTableEngineVersionKey } from '@/hooks/useBotTableEngineVersions';
import { getBotEntryAvailability, type BotActionAvailability, type BotDomain } from '@/services/botWorkshop';
import BotDescriptionCell from './BotDescriptionCell';
import { BotContainerInfo } from './BotContainerInfo';
import BotInfoCell from './BotInfoCell';
import { BotManagementMenu } from './BotManagementMenu';
import BotPrimaryActionsCell from './BotPrimaryActionsCell';
import BotStatusCell from './BotStatusCell';
import BotTagsCell from './BotTagsCell';
import BotVersionCell from './BotVersionCell';
import type { BotCardManagementAction } from './config';
import type { BotInventoryActions } from './BotTable';

/** 列定义入参：表格壳（BotTable）内的渲染态与回调（引擎版本相关来自 useBotTableEngineVersions）。 */
export interface BotTableColumnsInput {
  showOwner: boolean;
  expandedProgressIds: Set<string>;
  toggleProgress: (botId: string) => void;
  openMenuKey?: string;
  setOpenMenuKey: (next: (current: string | undefined) => string | undefined) => void;
  containerUserId?: string;
  engineVersionStates: Record<string, BotEngineVersionState | undefined>;
  engineVersionUpdateAvailability: Record<string, EngineVersionUpdateAvailability>;
  onOpenEngineInfo?: (bot: BotDomain) => void;
  onUpdateEngineVersion?: (bot: BotDomain) => void;
  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onConversation?: (bot: BotDomain) => void;
  canOpenConversation?: (bot: BotDomain) => boolean;
  onOpenLogs?: (bot: BotDomain) => void;
  onOpenTaskInstances?: (bot: BotDomain) => void;
  canOpenTaskInstances?: (bot: BotDomain) => boolean;
  onOpenContainers?: (bot: BotDomain) => void;
  canViewContainers?: (bot: BotDomain) => boolean;
  onHealthCheck?: (bot: BotDomain) => void;
  getHealthCheckAvailability?: (bot: BotDomain) => BotActionAvailability | undefined;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  onManagePublication?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;
  getInventoryActions?: (bot: BotDomain) => BotInventoryActions | undefined;
  getLogAction?: (bot: BotDomain) => BotActionAvailability | undefined;
  getCollaborationMode?: (bot: BotDomain) => 'authorize' | 'request' | undefined;
  getGeneralConfigAvailability?: (bot: BotDomain) => { enabled: boolean; disabledReason?: string } | undefined;
  onGeneralConfig?: (bot: BotDomain) => void;
}

/** Bot 管理列表列定义（dmore 稿）：恒显描述列 + 单「操作」列（链接与三个点同列收口）。 */
export function buildBotTableColumns(input: BotTableColumnsInput): DataTableColumn<BotDomain>[] {
  const {
    showOwner,
    expandedProgressIds,
    toggleProgress,
    openMenuKey,
    setOpenMenuKey,
    containerUserId,
    engineVersionStates,
    engineVersionUpdateAvailability,
    onOpenEngineInfo,
    onUpdateEngineVersion,
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
    getHealthCheckAvailability,
    onChangeSpace,
    onAuthorize,
    onManagePublication,
    onAction,
    onClaimLock,
    onReleaseLock,
    getInventoryActions,
    getLogAction,
    getCollaborationMode,
    getGeneralConfigAvailability,
    onGeneralConfig,
  } = input;
  return [
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
      cell: (bot) => (
        <BotTagsCell bot={bot} engineVersionState={engineVersionStates[botTableEngineVersionKey(bot)]} onOpenEngineInfo={onOpenEngineInfo} />
      ),
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
        const menuKey = botTableEngineVersionKey(bot);
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
              engineVersionUpdateAvailability={engineVersionUpdateAvailability[menuKey]}
              onOpenEngineInfo={onOpenEngineInfo}
              onUpdateEngineVersion={onUpdateEngineVersion}
            />
          </div>
        );
      },
    },
  ];
}