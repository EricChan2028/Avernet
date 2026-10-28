/** @jest-environment jsdom */
import { CommunityPersonalCenter } from '@/components/Community/CommunityPersonalCenter';
import type { UsePersonalCenterResult } from '@/hooks/usePersonalCenter';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { communityService } from '@/services/community';

jest.mock('@/services/community', () => ({
  communityService: {
    listTopics: jest.fn(),
  },
}));

const mockedCenter: UsePersonalCenterResult = {
  ownerUserId: '149844',
  activityType: 'AUTHORED',
  page: 1,
  pageSize: 10,
  activityPage: {
    items: [
      {
        activityType: 'AUTHORED',
        actor: { actorType: 'BOT', id: 'bot-A' },
        target: { targetType: 'TOPIC', id: 'topic-1' },
        topicId: 'topic-1',
        happenedAt: '2026-10-27T10:00:00Z',
        bodyPreview: 'Bot 发布的主题',
      },
    ],
    total: 1,
  },
  loading: false,
  error: null,
  setActivityType: jest.fn(),
  goToPage: jest.fn(),
  reload: jest.fn(),
  unfollow: jest.fn(),
};

jest.mock('@/hooks/usePersonalCenter', () => ({
  usePersonalCenter: () => mockedCenter,
  PERSONAL_CENTER_PAGE_SIZE: 10,
}));

describe('CommunityPersonalCenter', () => {
  it('renders the PRD-style My page and content tabs', () => {
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);

    expect(screen.getByText('我的')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument();
    expect(
      within(screen.getByRole('group', { name: '我的内容' }))
        .getAllByRole('button')
        .map((item) => item.textContent),
    ).toEqual(['我的帖子', '我的回贴', '我的收藏', '我的点赞', '我的关注', '浏览历史']);
    expect(screen.queryByRole('group', { name: '身份筛选' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '内容类型' })).not.toBeInTheDocument();
    expect(screen.getByText('Bot · bot-A')).toBeInTheDocument();
  });

  it('renders follow identities, their topics, and supports unfollowing', async () => {
    mockedCenter.activityType = 'FOLLOWED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'FOLLOWED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'HUMAN', id: 'user-b' },
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: '张三',
        },
        {
          activityType: 'FOLLOWED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'BOT', id: 'bot-b' },
          happenedAt: '2026-10-27T10:01:00Z',
          bodyPreview: '协作 Bot',
        },
      ],
      total: 2,
    };
    jest
      .mocked(communityService.listTopics)
      .mockResolvedValueOnce({
        items: [
          {
            id: 'topic-2',
            title: '张三的主题',
            body: '正文',
            status: 'open',
            topicType: 'discussion',
            author: { type: 'human', id: 'user-b', displayName: '张三' },
            createdAt: '2026-10-27T09:00:00Z',
            isMine: false,
            canClose: false,
            canEdit: false,
            canDelete: false,
          },
        ],
        total: 1,
      })
      .mockResolvedValue({ items: [], total: 0 });
    const onOpenTopic = jest.fn();
    const onUnfollow = jest.fn().mockResolvedValue(undefined);
    mockedCenter.unfollow = onUnfollow;
    render(
      <CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />,
    );

    expect(screen.getByText('关注的用户')).toBeInTheDocument();
    expect(screen.getByText('关注的 Bot')).toBeInTheDocument();
    expect(screen.queryByText('暂无关注的用户')).not.toBeInTheDocument();
    expect(screen.queryByText('暂无关注的 Bot')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('张三'));

    await waitFor(() =>
      expect(communityService.listTopics).toHaveBeenCalledWith({
        authorId: 'user-b',
        limit: 20,
      }),
    );
    expect(screen.getByText('张三 的帖子')).toBeInTheDocument();
    fireEvent.click(screen.getByText('张三的主题'));
    expect(onOpenTopic).toHaveBeenCalledWith('topic-2');

    fireEvent.click(screen.getByRole('button', { name: '取消关注 张三' }));
    expect(onUnfollow).toHaveBeenCalledTimes(1);

    mockedCenter.activityType = 'AUTHORED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'AUTHORED',
          actor: { actorType: 'BOT', id: 'bot-A' },
          target: { targetType: 'TOPIC', id: 'topic-1' },
          topicId: 'topic-1',
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: 'Bot 发布的主题',
        },
      ],
      total: 1,
    };
    mockedCenter.unfollow = jest.fn();
  });

  it('renders first-level replies as quoted rows when the backend returns a topic target', () => {
    mockedCenter.activityType = 'REPLIED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'REPLIED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'TOPIC', id: 'topic-2' },
          topicId: 'topic-2',
          postId: 'post-1',
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: '这是我发出的一级回复',
        },
      ],
      total: 1,
    };
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={jest.fn()} />);

    expect(screen.getByText('回复')).toBeInTheDocument();
    expect(screen.getByText('这是我发出的一级回复')).toBeInTheDocument();

    mockedCenter.activityType = 'AUTHORED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'AUTHORED',
          actor: { actorType: 'BOT', id: 'bot-A' },
          target: { targetType: 'TOPIC', id: 'topic-1' },
          topicId: 'topic-1',
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: 'Bot 发布的主题',
        },
      ],
      total: 1,
    };
  });

  it('renders reply activities as quoted rows and opens the related topic', () => {
    mockedCenter.activityType = 'REPLIED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'REPLIED',
          actor: { actorType: 'HUMAN', id: '149844' },
          target: { targetType: 'POST', id: 'post-1' },
          topicId: 'topic-2',
          postId: 'post-1',
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: '这是我发出的回复',
        },
      ],
      total: 1,
    };
    const onOpenTopic = jest.fn();
    render(<CommunityPersonalCenter onBack={jest.fn()} onOpenTopic={onOpenTopic} />);

    expect(screen.getByText('话题 #topic-2')).toBeInTheDocument();
    expect(screen.getByText('这是我发出的回复')).toBeInTheDocument();
    fireEvent.click(screen.getByText('这是我发出的回复'));
    expect(onOpenTopic).toHaveBeenCalledWith('topic-2');

    mockedCenter.activityType = 'AUTHORED';
    mockedCenter.activityPage = {
      items: [
        {
          activityType: 'AUTHORED',
          actor: { actorType: 'BOT', id: 'bot-A' },
          target: { targetType: 'TOPIC', id: 'topic-1' },
          topicId: 'topic-1',
          happenedAt: '2026-10-27T10:00:00Z',
          bodyPreview: 'Bot 发布的主题',
        },
      ],
      total: 1,
    };
  });
});
