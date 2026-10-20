import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import { labelOf, statusLabel } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import type { ReactElement, ReactNode } from 'react';
import { useCallback, useEffect, useState } from 'react';

/**
 * 「智能进化」首页顶部的胶囊 Tabs —— 与 Bot 管理页（dmore 10/19 稿）同一规范：
 * 激活态 primary 12% 蓝底 + 蓝字 13px/500，未激活灰字，hover 走 muted。
 * 列表页（TaskDetail / DomainDetail 等二级页）继续用 EvolutionHeader 的下划线 tab 表达层级差异。
 */
export type EvolutionTab = 'tasks' | 'templates' | 'versions';
const EVOLUTION_TABS: ReadonlyArray<readonly [EvolutionTab, string]> = [
  ['tasks', '进化任务'],
  ['templates', '评测模板'],
  ['versions', '进化版本'],
];
export function EvolutionTabs({ tab, className }: { tab: EvolutionTab; className?: string }) {
  return (
    <div className={cn('flex items-center gap-1', className)} role="tablist" aria-label="智能进化">
      {EVOLUTION_TABS.map(([value, label]) => (
        <button
          key={value}
          role="tab"
          type="button"
          aria-selected={tab === value}
          className={cn(
            'inline-flex h-8 items-center rounded-md px-3 text-[13px] transition-colors cursor-pointer',
            tab === value
              ? 'bg-primary/10 font-medium text-primary'
              : 'font-normal text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
          onClick={() => history.push(`/evolution-factory${value === 'tasks' ? '' : `?tab=${value}`}`)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * 列表筛选下拉：左侧常驻小标题 + 右侧 Select 框（对齐 BotWorkshopToolbar 的 idiom ——
 * 筛选器名作为常驻标题放在框左侧，框内只反映当前选择）。与 Choice 的区别：
 * Choice 服务于表单字段（label 由 Field 承载），FilterSelect 服务于工具行筛选。
 */
export function FilterSelect({
  label,
  value,
  onChange,
  options,
  triggerClassName = 'w-full sm:w-[132px]',
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  triggerClassName?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
      <span className="shrink-0 text-xs font-medium text-muted-foreground">{label}</span>
      <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger aria-label={label} className={triggerClassName}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options
            .filter((item) => item.value)
            .map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** 结果面板容器：数据视图 = 白底 + 细边框 r10（与 Bot 管理列表面板同规范）。 */
export function TablePanel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('overflow-hidden rounded-[10px] border border-border/60 bg-card', className)}>{children}</div>;
}
/** 结果面板容器：空态 / 错误态 = 40% 灰底留白面板，居中引导（同规范）。 */
export function PlaceholderPanel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('rounded-[10px] bg-muted/40', className)}>{children}</div>;
}

/** 列表骨架：44px 表头条 + 72px 行高，与 DataTable(h-18) 几何一致。首列两行占位，其余单行。 */
export function ListSkeleton({
  widths,
  rows = 6,
  ariaLabel = '列表加载中',
}: {
  widths: string[];
  rows?: number;
  ariaLabel?: string;
}) {
  return (
    <div role="status" aria-label={ariaLabel} className="w-full overflow-x-auto">
      <table aria-hidden className="w-full table-fixed border-collapse text-sm">
        <thead className="bg-muted/50">
          <tr>
            {widths.map((width, index) => (
              <th key={index} className={cn('h-11 px-4 text-left', width)}>
                <Skeleton.Line className="w-14" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, rowIndex) => (
            <tr key={rowIndex} className="h-18 border-b border-border/60">
              {widths.map((width, columnIndex) => (
                <td key={columnIndex} className={cn('px-4 align-middle', width)}>
                  {columnIndex === 0 ? (
                    <div className="space-y-2">
                      <Skeleton.Line className="w-2/3" />
                      <Skeleton.Line className="w-1/3" />
                    </div>
                  ) : (
                    <Skeleton.Line className="w-12" />
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EvolutionHeader({
  tab,
  actions,
  showTabs = true,
}: {
  tab: 'tasks' | 'templates' | 'versions';
  actions?: ReactNode;
  showTabs?: boolean;
}) {
  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">智能进化</h1>
        {actions}
      </header>
      {showTabs && (
        <div role="tablist" aria-label="智能进化" className="flex gap-6 border-b border-border">
          {(
            [
              ['tasks', '进化任务'],
              ['templates', '评测模板'],
              ['versions', '进化版本'],
            ] as const
          ).map(([value, label]) => (
            <Button
              variant="ghost"
              style={{ height: 'auto', whiteSpace: 'normal' }}
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={`border-b-2 px-3 pb-3 text-sm ${
                tab === value ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground'
              }`}
              onClick={() => history.push(`/evolution-factory${value === 'tasks' ? '' : `?tab=${value}`}`)}
            >
              {label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

export function useResource<T>(load: () => Promise<T>, enabled = true) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(enabled);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let current = true;
    setData(undefined);
    setError('');
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    load()
      .then((value) => {
        if (current) setData(value);
      })
      .catch((e: unknown) => {
        if (current) setError(e instanceof Error ? e.message : '加载失败');
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [load, enabled, revision]);
  return { data, error, loading, reload };
}
export function StatusBadge({ status, dot = false }: { status: string; dot?: boolean }) {
  const tone = ['completed', 'succeeded'].includes(status)
    ? 'success'
    : ['failed', 'canceled', 'cancelled'].includes(status)
    ? 'error'
    : status.startsWith('waiting')
    ? 'warning'
    : 'primary';
  // dot 态与 Bot 管理状态列（dmore 规范）几何一致：8px 圆点 + 13px muted 说明文字。
  if (dot)
    return (
      <span className="inline-flex items-center gap-2 whitespace-nowrap text-[13px] text-muted-foreground">
        <span
          aria-hidden="true"
          className={`size-2 rounded-full ${
            tone === 'success'
              ? 'bg-success'
              : tone === 'error'
              ? 'bg-destructive'
              : tone === 'warning'
              ? 'bg-warning'
              : 'bg-primary'
          }`}
        />
        {statusLabel(status)}
      </span>
    );
  return <Badge tone={tone}>{statusLabel(status)}</Badge>;
}
export function Section({ title, children, index }: { title: string; children: ReactNode; index?: number }) {
  return (
    <Card className="p-5 sm:p-6">
      <h2 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
        {index !== undefined && (
          <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
            {index}
          </span>
        )}
        {title}
      </h2>
      {children}
    </Card>
  );
}
export function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-2 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {help && <span className="text-xs text-muted-foreground">{help}</span>}
    </label>
  );
}
export function Choice({
  value,
  onChange,
  options,
  label,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
  disabled?: boolean;
}) {
  return (
    <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label}>
        <SelectValue placeholder="请选择" />
      </SelectTrigger>
      <SelectContent>
        {options
          .filter((item) => item.value)
          .map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  );
}
export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
    >
      <span>{message}</span>
      {retry && (
        <Button variant="outline" size="sm" onClick={retry}>
          重新加载
        </Button>
      )}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-4 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function typeTone(type: string) {
  return type === 'full'
    ? 'bg-[hsl(var(--purple)/0.08)] text-[hsl(var(--purple))]'
    : ['optimize', 'bench_optimize'].includes(type)
    ? 'bg-warning/10 text-warning'
    : type === 'bench'
    ? 'bg-info/10 text-info'
    : 'bg-primary/10 text-primary';
}
export function TypeTag({ type }: { type: string }) {
  return (
    <span className={`inline-flex whitespace-nowrap rounded px-2 py-0.5 text-xs ${typeTone(type)}`}>
      {labelOf(type)}
    </span>
  );
}

/** Overflow labels retain hover and keyboard access through the shared UI primitive. */
export function Hint({ content, children }: { content?: ReactNode; children: ReactElement }) {
  if (!content) return children;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild tabIndex={0}>
          {children}
        </TooltipTrigger>
        <TooltipContent>{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
