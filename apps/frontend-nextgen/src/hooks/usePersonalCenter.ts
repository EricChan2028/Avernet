import { PERSONAL_CENTER_PAGE_SIZE } from '@/domain/community/constants';
import { communityErrorMessage } from '@/domain/community/errors';
import type { PersonalCenterActivityPage, PersonalCenterActivityType } from '@/domain/community/types';
import { EMPTY_ACTIVITY_PAGE, type UsePersonalCenterResult } from './personalCenterModel';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { communityService } from '@/services/community';
import { useCallback, useEffect, useRef, useState } from 'react';

export type { UsePersonalCenterResult } from './personalCenterModel';

export function usePersonalCenter(): UsePersonalCenterResult {
  const { identity } = useHumanIdentity();
  const ownerUserId = identity?.userId.trim() ?? '';
  const [activityType, setActivityTypeState] = useState<PersonalCenterActivityType>('AUTHORED');
  const [page, setPage] = useState(1);
  const [activityPage, setActivityPage] = useState<PersonalCenterActivityPage>(EMPTY_ACTIVITY_PAGE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const activityRequestRef = useRef(0);

  useEffect(() => {
    if (!ownerUserId) return;
    const requestId = ++activityRequestRef.current;
    setLoading(true);
    setError(null);
    communityService
      .getPersonalCenterActivities({
        ownerUserId,
        actorScope: 'ALL',
        activityType,
        targetType: 'ALL',
        page,
        pageSize: PERSONAL_CENTER_PAGE_SIZE,
      })
      .then((result) => {
        if (requestId !== activityRequestRef.current) return;
        setActivityPage(result);
      })
      .catch((activityError: unknown) => {
        if (requestId !== activityRequestRef.current) return;
        setError(communityErrorMessage(activityError, '个人中心内容加载失败，请稍后重试'));
        setActivityPage(EMPTY_ACTIVITY_PAGE);
      })
      .finally(() => {
        if (requestId === activityRequestRef.current) setLoading(false);
      });
  }, [activityType, ownerUserId, page, reloadNonce]);

  const setActivityType = useCallback((type: PersonalCenterActivityType) => {
    setActivityTypeState(type);
    setPage(1);
  }, []);

  const goToPage = useCallback((nextPage: number) => {
    setPage(nextPage);
  }, []);

  const reload = useCallback(() => {
    setReloadNonce((nonce) => nonce + 1);
  }, []);

  return {
    ownerUserId,
    activityType,
    page,
    pageSize: PERSONAL_CENTER_PAGE_SIZE,
    activityPage,
    loading,
    error,
    setActivityType,
    goToPage,
    reload,
  };
}
