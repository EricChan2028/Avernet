import type { PersonalCenterActivityPage, PersonalCenterActivityType } from '@/domain/community/types';

export interface UsePersonalCenterResult {
  ownerUserId: string;
  activityType: PersonalCenterActivityType;
  page: number;
  pageSize: number;
  activityPage: PersonalCenterActivityPage;
  loading: boolean;
  error: string | null;
  setActivityType: (activityType: PersonalCenterActivityType) => void;
  goToPage: (page: number) => void;
  reload: () => void;
}

export const EMPTY_ACTIVITY_PAGE: PersonalCenterActivityPage = { items: [], total: 0 };
