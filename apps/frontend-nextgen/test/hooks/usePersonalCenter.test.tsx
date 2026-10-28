/** @jest-environment jsdom */
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { PERSONAL_CENTER_PAGE_SIZE } from '@/domain/community/constants';
import { usePersonalCenter } from '@/hooks/usePersonalCenter';
import { communityService } from '@/services/community';
import { act, renderHook, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('@/hooks/useHumanIdentity', () => ({ useHumanIdentity: jest.fn() }));
jest.mock('@/services/community', () => ({
  communityService: {
    getPersonalCenterActivities: jest.fn(),
  },
}));

const mockedIdentity = useHumanIdentity as jest.MockedFunction<typeof useHumanIdentity>;

describe('usePersonalCenter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedIdentity.mockReturnValue({
      identity: { userId: '149844', displayName: '当前用户', online: true },
      status: 'ready',
    });
    jest
      .mocked(communityService.getPersonalCenterActivities)
      .mockResolvedValue({ items: [], total: 0 });
  });

  it('loads the ALL actor and target activity list from the backend', async () => {
    const { result } = renderHook(() => usePersonalCenter());
    expect(result.current.activityType).toBe('AUTHORED');

    await waitFor(() =>
      expect(communityService.getPersonalCenterActivities).toHaveBeenCalledTimes(1),
    );
    expect(communityService.getPersonalCenterActivities).toHaveBeenCalledWith({
      ownerUserId: '149844',
      actorScope: 'ALL',
      activityType: 'AUTHORED',
      targetType: 'ALL',
      page: 1,
      pageSize: PERSONAL_CENTER_PAGE_SIZE,
    });
  });

  it('switching content type resets to the first page', async () => {
    const { result } = renderHook(() => usePersonalCenter());
    await waitFor(() =>
      expect(communityService.getPersonalCenterActivities).toHaveBeenCalledTimes(1),
    );

    act(() => result.current.goToPage(3));
    await waitFor(() => expect(result.current.page).toBe(3));
    act(() => result.current.setActivityType('REPLIED'));

    expect(result.current.page).toBe(1);
    await waitFor(() =>
      expect(communityService.getPersonalCenterActivities).toHaveBeenLastCalledWith({
        ownerUserId: '149844',
        actorScope: 'ALL',
        activityType: 'REPLIED',
        targetType: 'ALL',
        page: 1,
        pageSize: PERSONAL_CENTER_PAGE_SIZE,
      }),
    );
  });

  it('retries the activity list on reload and clears a stale error', async () => {
    jest
      .mocked(communityService.getPersonalCenterActivities)
      .mockRejectedValueOnce(new Error('activities unavailable'))
      .mockResolvedValue({ items: [], total: 0 });
    const { result } = renderHook(() => usePersonalCenter());
    await waitFor(() => expect(result.current.error).not.toBeNull());

    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(communityService.getPersonalCenterActivities).toHaveBeenCalledTimes(2);
  });
});
