import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Pagination } from '@/components/ui/Pagination';
import { canCancelStep, canRetryStep, formatTime, isBotEvolutionTask, taskTypes, type Task } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { Plus, RefreshCw, Search } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { stepName, taskTimes } from './presentation';
import { ErrorBox, FilterSelect, Hint, ListSkeleton, PlaceholderPanel, StatusBadge, TablePanel, TypeTag, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

/** 与 Bot 管理操作列同规范的文字链接：primary 蓝字 13px，hover 10% 蓝底。 */
const linkClass = 'h-7 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';
const dangerLinkClass =
  'h-7 rounded-md px-1.5 text-[13px] font-normal text-destructive hover:bg-destructive/10 hover:text-destructive';

/** 当前节点：优先展示可停止的执行节点，退化到可重试/最后一个节点。 */
function stepNameOf(task: Task) {
  const retryTarget = task.steps?.filter((candidate) => canRetryStep(task, candidate)).at(-1);
  const cancelTarget = task.steps?.find((candidate) => canCancelStep(task, candidate));
  const step = cancelTarget ?? retryTarget ?? task.steps?.at(-1);
  return step ? stepName(step.stepType) : '—';
}

const COLUMN_WIDTHS = [
  'w-[clamp(220px,18vw,280px)]',
  'w-[clamp(140px,12vw,240px)]',
  'w-[clamp(100px,8vw,120px)]',
  'w-[clamp(150px,12vw,180px)]',
  'w-[clamp(96px,8vw,112px)]',
  'w-[clamp(84px,7vw,100px)]',
  'w-[clamp(160px,13vw,190px)]',
  'w-[clamp(104px,9vw,130px)]',
  'w-[200px]',
];

export default function TaskList({ onCreate }: { onCreate?: () => void }) {
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [status, setStatus] = useState('all');
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState('');
  const [operationError, setOperationError] = useState('');
  const load = useCallback(
    () =>
      evolutionApi.tasks({
        query,
        category,
        status,
        page,
        pageSize: 20,
        taskTypes: taskTypes.map((item) => item.type).join(','),
        targetType: 'bot',
      }),
    [query, category, status, page, identity.key],
  );
  const result = useResource(load, identity.ready);
  const visibleTasks = result.data?.tasks.filter(isBotEvolutionTask) ?? [];
  const hiddenCount = (result.data?.tasks.length ?? 0) - visibleTasks.length;
  const hasActiveFilter = Boolean(query) || category !== 'all' || status !== 'all';
  const action = async (taskId: string, stepId: string, operation: 'retry' | 'cancel') => {
    if (busy) return;
    setBusy(taskId);
    setOperationError('');
    try {
      await evolutionApi.stepAction(taskId, stepId, operation);
      result.reload();
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : '操作失败');
      throw error;
    } finally {
      setBusy('');
    }
  };
  const columns = useMemo<DataTableColumn<Task>[]>(
    () => [
      {
        id: 'name',
        header: '任务名称',
        width: COLUMN_WIDTHS[0],
        cell: (task) => (
          <div className="min-w-0">
            <Hint content={task.task_name}>
              <span className="block truncate text-sm font-medium text-foreground">{task.task_name}</span>
            </Hint>
            <Hint content={task.task_id}>
              <span className="mt-0.5 block truncate font-mono text-xs text-content-icon">{task.task_id}</span>
            </Hint>
          </div>
        ),
      },
      {
        id: 'remark',
        header: '备注',
        width: COLUMN_WIDTHS[1],
        cell: (task) => (
          <Hint content={task.remark || undefined}>
            <span className="block truncate text-xs text-muted-foreground">{task.remark || '—'}</span>
          </Hint>
        ),
      },
      {
        id: 'type',
        header: '任务类型',
        width: COLUMN_WIDTHS[2],
        cell: (task) => <TypeTag type={task.task_type} />,
      },
      {
        id: 'object',
        header: '进化对象',
        width: COLUMN_WIDTHS[3],
        cell: (task) => (
          <div className="min-w-0">
            <Hint content={task.bot_id}>
              <span className="block truncate text-[13px] text-foreground">{task.bot_id}</span>
            </Hint>
            <span className="mt-0.5 block truncate text-xs text-content-icon">Owner {task.user_id}</span>
          </div>
        ),
      },
      {
        id: 'status',
        header: '状态',
        width: COLUMN_WIDTHS[4],
        cell: (task) => <StatusBadge status={task.status} dot />,
      },
      {
        id: 'creator',
        header: '创建人',
        width: COLUMN_WIDTHS[5],
        cell: (task) => <span className="text-[13px] text-foreground">{task.created_by}</span>,
      },
      {
        id: 'times',
        header: '开始/结束时间',
        width: COLUMN_WIDTHS[6],
        cell: (task) => {
          const times = taskTimes(task);
          return (
            <span className="block text-xs leading-5 text-muted-foreground">
              <span className="block">{formatTime(times.start)}</span>
              <span className="block">{formatTime(times.end)}</span>
            </span>
          );
        },
      },
      {
        id: 'node',
        header: '当前节点',
        width: COLUMN_WIDTHS[7],
        cell: (task) => <span className="text-[13px] text-foreground">{stepNameOf(task)}</span>,
      },
      {
        id: 'actions',
        header: '操作',
        width: COLUMN_WIDTHS[8],
        align: 'end',
        className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
        headerClassName: 'sticky right-0 z-10 bg-muted/50',
        cell: (task) => {
          const retryTarget = task.steps?.filter((candidate) => canRetryStep(task, candidate)).at(-1);
          const cancelTarget = task.steps?.find((candidate) => canCancelStep(task, candidate));
          const manage = isBotEvolutionTask(task);
          return (
            <div className="flex items-center justify-end gap-0.5">
              <Hint content={isBotEvolutionTask(task) ? undefined : '该历史任务不在本期迁移范围，请使用原入口'}>
                <Button
                  type="button"
                  variant="ghost"
                  className={linkClass}
                  disabled={!isBotEvolutionTask(task)}
                  onClick={() => history.push(`/evolution-factory/tasks/${encodeURIComponent(task.task_id)}`)}
                >
                  详情
                </Button>
              </Hint>
              {retryTarget && manage ? (
                <Button
                  type="button"
                  variant="ghost"
                  className={linkClass}
                  disabled={Boolean(busy)}
                  onClick={() => void action(task.task_id, retryTarget.stepId, 'retry').catch(() => undefined)}
                >
                  重试
                </Button>
              ) : null}
              {cancelTarget && manage ? (
                <ConfirmDialog
                  title="停止当前步骤？"
                  description="取消当前 Step；已完成步骤和产物保留。"
                  onConfirm={() => action(task.task_id, cancelTarget.stepId, 'cancel')}
                >
                  <Button type="button" variant="ghost" className={dangerLinkClass} disabled={Boolean(busy)}>
                    停止
                  </Button>
                </ConfirmDialog>
              ) : null}
            </div>
          );
        },
      },
    ],
    [busy],
  );
  return (
    <div className="min-w-0">
      <ErrorBox message={identity.error ?? result.error} retry={result.reload} />
      <ErrorBox message={operationError} />
      <ErrorBox
        message={hiddenCount ? '任务列表返回了筛选范围外的数据，请确认后端已部署筛选参数支持后刷新。' : ''}
        retry={result.reload}
      />
      {/* 筛选行：与 Bot 管理工具行同一 idiom —— 搜索框 + 带常驻小标题的下拉 + 右侧重置/操作。 */}
      <form
        className="mb-4 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search.trim());
          setPage(1);
        }}
      >
        <div className="relative w-full sm:w-[240px] sm:shrink-0">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            aria-label="搜索任务"
            placeholder="任务名称 / ID"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              // 清空即恢复全量，无需再敲回车；有内容时回车提交
              if (!e.target.value && query) {
                setQuery('');
                setPage(1);
              }
            }}
          />
        </div>
        <FilterSelect
          label="任务分类"
          value={category}
          onChange={(v) => {
            setCategory(v);
            setPage(1);
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'diagnosis', label: '诊断类' },
            { value: 'optimization', label: '优化类' },
            { value: 'full', label: '全流程类' },
          ]}
        />
        <FilterSelect
          label="任务状态"
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'running', label: '进行中' },
            { value: 'success', label: '已完成' },
            { value: 'failed', label: '失败/取消' },
          ]}
        />
        <div className="ml-auto flex w-full items-center justify-end gap-2.5 sm:w-auto">
          {hasActiveFilter ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch('');
                setQuery('');
                setCategory('all');
                setStatus('all');
                setPage(1);
              }}
            >
              重置
            </Button>
          ) : null}
          <Button type="button" variant="outline" size="sm" leftIcon={<RefreshCw size={14} />} onClick={result.reload}>
            刷新
          </Button>
          {onCreate ? (
            <Button type="button" leftIcon={<Plus size={14} />} onClick={onCreate}>
              发起进化
            </Button>
          ) : null}
        </div>
      </form>
      {result.loading || identity.loading ? (
        <TablePanel>
          <ListSkeleton widths={COLUMN_WIDTHS} ariaLabel="任务列表加载中" />
        </TablePanel>
      ) : (
        result.data && (
          <>
            <TablePanel>
              <DataTable
                columns={columns}
                rows={visibleTasks}
                getRowKey={(task) => task.task_id}
                rowHeight="h-18"
                ariaLabel="进化任务列表"
                minWidth="min-w-[1320px]"
              />
            </TablePanel>
            {!visibleTasks.length && !hiddenCount ? (
              <PlaceholderPanel className="mt-3">
                <Empty
                  title={hasActiveFilter ? '没有符合条件的任务' : '当前空间暂无进化任务'}
                  description={
                    hasActiveFilter
                      ? '尝试清除搜索词或调整筛选条件。'
                      : '发起一次 Bot 诊断或 Bench 诊断，量化当前能力并定位改进点。'
                  }
                  action={onCreate ? <Button onClick={onCreate}>发起进化</Button> : undefined}
                />
              </PlaceholderPanel>
            ) : null}
            {result.data.total > 0 && !hiddenCount ? (
              <Pagination
                current={page}
                pageSize={20}
                total={result.data.total}
                onChange={setPage}
                layout="between"
                className="mt-4"
              />
            ) : null}
          </>
        )
      )}
    </div>
  );
}