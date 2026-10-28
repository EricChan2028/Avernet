import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { PersonalCenterActivity } from '@/domain/community/types';
import { formatDateTime } from '../CommunityTopicDetail/model';
import { actorLabel } from './model';

export interface ActivityListProps {
  items: PersonalCenterActivity[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenTopic: (topicId: string) => void;
}

export function ActivityList({ items, loading, error, onRetry, onOpenTopic }: ActivityListProps) {
  if (loading) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="个人中心列表加载中">
        {[1, 2, 3].map((item) => (
          <Skeleton.ListItem key={item} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-border bg-card">
        <Empty
          title="列表加载失败"
          description={error}
          action={
            <Button variant="outline" onClick={onRetry}>
              重试
            </Button>
          }
        />
      </div>
    );
  }
  if (!items.length) {
    return (
      <div className="rounded-lg border border-border bg-card">
        <Empty title="暂无内容" description="当前身份在这个分类下还没有社区记录。" />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="个人中心内容列表">
      {items.map((item) => {
        const isReply = item.target.targetType === 'POST' || (item.activityType === 'REPLIED' && !!item.postId);
        const primaryText = isReply ? `话题 #${item.topicId}` : item.bodyPreview || '社区内容';
        return (
          <Button
            key={`${item.actor.actorType}:${item.actor.id}:${item.activityType}:${item.target.targetType}:${item.target.id}`}
            variant="ghost"
            className="block h-auto w-full rounded-none border-b border-border/60 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-muted"
            onClick={() => onOpenTopic(item.topicId)}
          >
            <span className="block w-full">
              <span className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_150px]">
                <span className="min-w-0">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-semibold text-foreground">{primaryText}</span>
                    <Badge tone={isReply ? 'neutral' : 'outline'} className="h-5 shrink-0 rounded px-1.5 text-[10px]">
                      {isReply ? '回复' : '主题'}
                    </Badge>
                  </span>
                  {isReply && item.bodyPreview ? (
                    <span className="mt-2 block line-clamp-2 rounded-md bg-muted/50 px-2.5 py-2 text-xs leading-relaxed text-foreground/90">
                      {item.bodyPreview}
                    </span>
                  ) : null}
                </span>
                <span className="min-w-0 text-xs text-muted-foreground sm:text-right">
                  <span className="block truncate">
                    {actorLabel(item.actor.actorType)} · {item.actor.id}
                  </span>
                  <span className="mt-1 block tabular-nums">{formatDateTime(item.happenedAt)}</span>
                </span>
              </span>
            </span>
          </Button>
        );
      })}
    </div>
  );
}
