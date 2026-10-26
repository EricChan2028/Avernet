import * as ctrl from '@/services/backendApi/bots/privateBotSessionController';
import { botSessionService, resolveChatRuntimeStage, type ChatBotView } from '@/services/workspace/botSessionService';

jest.mock('@/services/backendApi/bots/privateBotSessionController');

const bot: ChatBotView = {
  botId: 'service:owner', realBotId: 'service', ownerId: 'owner', botType: 'service',
  displayName: 'Service Bot', online: true, chatable: true,
};
const session = { session_id: 's', title: '验证', model: 'm', agent_id: '', message_count: 0, gmt_create: '', gmt_modified: '' };

beforeEach(() => {
  jest.resetAllMocks();
  for (const method of ['listBotSessions', 'listFavoriteSessions', 'listBotSessionMessages', 'listBotModels'] as const) {
    jest.mocked(ctrl[method]).mockResolvedValue({ code: 200000, data: { items: [], total: 0 } });
  }
  for (const method of ['createBotSession', 'getBotSession', 'updateBotSession'] as const) {
    jest.mocked(ctrl[method]).mockResolvedValue({ code: 200000, data: session });
  }
  jest.mocked(ctrl.deleteBotSession).mockResolvedValue({ code: 200000, data: { deleted: true } });
  jest.mocked(ctrl.deleteBotSessionMessages).mockResolvedValue({ code: 200000, data: { deleted: true } });
  jest.mocked(ctrl.favoriteBotSession).mockResolvedValue({ code: 200000, data: { session_id: 's', favorited: true } });
  jest.mocked(ctrl.unfavoriteBotSession).mockResolvedValue({ code: 200000, data: { session_id: 's', favorited: false } });
});

test.each(['draft', 'verify', 'online'] as const)('%s 的所有会话请求选择同一运行环境', async (stage) => {
  const target = { ...bot, runtimeStage: stage };
  const params = expect.objectContaining({ user_id: 'user', stage });
  await botSessionService.listSessions(target, 'user');
  await botSessionService.listSessionsPage(target, 'user', 2);
  await botSessionService.createSession(target, 'user');
  await botSessionService.getSessionDetail(target, 'user', 's');
  await botSessionService.listMessagesPage(target, 'user', 's', 2);
  await botSessionService.listModels(target, 'user');
  await botSessionService.updateSessionModel(target, 'user', 's', 'new-model');
  await botSessionService.updateSessionTitle(target, 'user', 's', '新标题');
  await botSessionService.clearContext(target, 'user', 's');
  await botSessionService.deleteSession(target, 'user', 's');
  await botSessionService.toggleFavorite(target, 'user', 's', true);
  await botSessionService.toggleFavorite(target, 'user', 's', false);
  await botSessionService.listFavoriteSessionsPage(target, 'user');
  expect(ctrl.listBotSessions).toHaveBeenNthCalledWith(1, 'service', params);
  expect(ctrl.listBotSessions).toHaveBeenNthCalledWith(2, 'service', params);
  expect(ctrl.createBotSession).toHaveBeenCalledWith('service', params, { title: '新会话' });
  expect(ctrl.getBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.listBotSessionMessages).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.listBotModels).toHaveBeenCalledWith('service', params);
  expect(ctrl.updateBotSession).toHaveBeenNthCalledWith(1, 'service', 's', params, { model: 'new-model' });
  expect(ctrl.updateBotSession).toHaveBeenNthCalledWith(2, 'service', 's', params, { title: '新标题' });
  expect(ctrl.deleteBotSessionMessages).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.deleteBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.favoriteBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.unfavoriteBotSession).toHaveBeenCalledWith('service', 's', params);
  expect(ctrl.listFavoriteSessions).toHaveBeenCalledWith('service', params);
});

test.each([
  { label: '我的服务 Bot', overrides: { botType: 'service' }, stage: 'online' },
  { label: '我的个人 Bot', overrides: { botType: 'personal' }, stage: undefined },
  { label: '团队服务 Bot', overrides: { isTeamBot: true }, stage: undefined },
  { label: '好友服务 Bot', overrides: { isFriendBot: true }, stage: undefined },
  { label: '未声明类型 Bot', overrides: { botType: undefined }, stage: undefined },
])('$label 仅在我的服务 Bot 请求中携带 stage', async ({ overrides, stage }) => {
  const target = { ...bot, ...overrides };
  expect(resolveChatRuntimeStage(target)).toBe(stage);
  await botSessionService.listSessions(target, 'user');
  await botSessionService.listSessionsPage(target, 'user');
  await botSessionService.listFavoriteSessionsPage(target, 'user');
  await botSessionService.listModels(target, 'user');
  await botSessionService.createSession(target, 'user');
  await botSessionService.getSessionDetail(target, 'user', 's');
  await botSessionService.listMessagesPage(target, 'user', 's');
  await botSessionService.updateSessionModel(target, 'user', 's', 'new-model');
  await botSessionService.updateSessionTitle(target, 'user', 's', '新标题');
  await botSessionService.clearContext(target, 'user', 's');
  await botSessionService.deleteSession(target, 'user', 's');
  await botSessionService.toggleFavorite(target, 'user', 's', true);
  await botSessionService.toggleFavorite(target, 'user', 's', false);
  for (const method of Object.values(ctrl)) {
    if (!jest.isMockFunction(method)) continue;
    for (const args of method.mock.calls) {
      const params = args.find((arg: unknown) => arg && typeof arg === 'object' && 'user_id' in arg);
      expect(params).toEqual(expect.objectContaining(stage ? { stage } : {}));
      expect(Object.prototype.hasOwnProperty.call(params, 'stage')).toBe(stage !== undefined);
    }
  }
});

test('个人 Bot 工坊显式调试阶段不被线上默认值覆盖', async () => {
  const target = { ...bot, botType: 'personal', runtimeStage: 'draft' as const };
  expect(resolveChatRuntimeStage(target)).toBe('draft');
  await botSessionService.listSessions(target, 'user');
  expect(ctrl.listBotSessions).toHaveBeenCalledWith('service', expect.objectContaining({ stage: 'draft' }));
});
