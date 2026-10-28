import { Button } from '@/components/ui/Button';
import type { CommunityReply, InteractionSummary } from '@/domain/community/types';
import { Heart, MessageSquare } from 'lucide-react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { formatDateTime, type ReplyElementRegistrar } from './model';

interface ThreadReplyProps {
  reply: CommunityReply;
  targetAuthorName: string;
  targetPostId?: string;
  onLocateTarget?: (postId: string) => void;
  registerElement?: ReplyElementRegistrar;
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityReply['author']) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityReply['author']) => void;
  followAuthor?: (author: CommunityReply['author']) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityReply['author']) => boolean;
  canReply?: boolean;
  likeCount?: number;
  likePending?: boolean;
  onLike?: (postId: string) => void;
  onReply?: (target: { postId: string; authorName: string }) => void;
}

export function ThreadReply({
  reply,
  targetAuthorName,
  targetPostId,
  onLocateTarget,
  registerElement,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  canReply = false,
  likeCount = 0,
  likePending = false,
  onLike,
  onReply,
}: ThreadReplyProps) {
  return (
    <article
      data-post-id={reply.id}
      ref={registerElement?.(reply.id)}
      className="rounded-xl border border-border/60 bg-muted/40 px-3 py-2.5"
    >
      <div className="flex items-center gap-2">
        <CommunityAuthorAvatar
          author={reply.author}
          size={24}
          currentUserId={currentUserId}
          summary={getAuthorSummary?.(reply.author)}
          onLoadSummary={() => loadAuthorSummary?.(reply.author)}
          onFollow={() => void followAuthor?.(reply.author)}
          followLoading={authorFollowLoading?.(reply.author) === true}
        />
        <span className="text-xs font-semibold text-foreground">{reply.author.displayName}</span>
        <span className="text-xs text-muted-foreground">回复</span>
        {targetPostId ? (
          <Button
            variant="link"
            size="sm"
            className="h-auto min-h-0 p-0 text-xs"
            aria-label={`定位${targetAuthorName}`}
            onClick={() => onLocateTarget?.(targetPostId)}
          >
            @{targetAuthorName}
          </Button>
        ) : (
          <span className="text-xs font-medium text-primary">@{targetAuthorName}</span>
        )}
      </div>
      <p className="m-0 mt-2 whitespace-pre-wrap text-xs leading-relaxed text-foreground">{reply.body}</p>
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          {canReply ? (
            <button
              type="button"
              className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
              aria-label="回复"
              onClick={() => onReply?.({ postId: reply.id, authorName: reply.author.displayName })}
            >
              <MessageSquare className="size-3.5" aria-hidden />
              回复
            </button>
          ) : null}
          {onLike ? (
            <button
              type="button"
              disabled={likePending}
              aria-label="点赞回复"
              onClick={() => onLike(reply.id)}
              className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Heart className="size-3.5" aria-hidden />
              {likeCount}
            </button>
          ) : null}
        </div>
        <span className="text-[10px] text-muted-foreground">{formatDateTime(reply.createdAt)}</span>
      </div>
    </article>
  );
}
