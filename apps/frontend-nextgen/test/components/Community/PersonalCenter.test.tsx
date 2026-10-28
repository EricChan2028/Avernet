/** @jest-environment jsdom */
import { CommunityPersonalCenter } from '@/components/Community/CommunityPersonalCenter';
import type { UsePersonalCenterResult } from '@/hooks/usePersonalCenter';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, within } from '@testing-library/react';

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
    ).toEqual(['我的帖子', '我的回贴', '我的收藏', '我的点赞', '浏览历史']);
    expect(screen.queryByRole('group', { name: '身份筛选' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '内容类型' })).not.toBeInTheDocument();
    expect(screen.getByText('Bot · bot-A')).toBeInTheDocument();
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
