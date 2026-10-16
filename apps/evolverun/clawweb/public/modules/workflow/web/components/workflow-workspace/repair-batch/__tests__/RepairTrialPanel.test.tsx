import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import Ajv from 'ajv'
import { beforeEach, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ trials: vi.fn(), trialOptions: vi.fn(), startTrial: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))
import RepairTrialPanel from '../RepairTrialPanel'
const revision = { revision: 1, phase: 'review', stateVersion: 1, draft: { candidateCommit: 'a'.repeat(40) } } as never
const trial = { id: 'trial', requestId: 'r', flowId: 'real-flow', candidateCommit: 'a'.repeat(40), revision: 1,
  status: 'waiting', input: { token: '[REDACTED]' }, target: { label: 'Production Bot' }, createdAtMs: 1, output: '<script>alert(1)</script>' }
beforeEach(() => {
  vi.resetAllMocks()
  api.trials.mockResolvedValue({ configured: true, trials: [] })
  api.trialOptions.mockResolvedValue({ inputSchema: { type: 'object', required: ['query'], properties: { query: { type: 'string' } } },
    inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
})
const panel = (rev = revision) => <MemoryRouter><RepairTrialPanel taskId="task" revision={rev} enabled open onClose={() => {}} onTrials={() => {}} /></MemoryRouter>
it.each([false, true])('handles a removed selected Bot on reopen while preserving unknown-request reconciliation (uncertain=%s)', async uncertain => {
  api.startTrial.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce(trial)
  const view = render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'original')
  if (uncertain) {
    await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
    await screen.findByRole('button', { name: '核对并重试原请求' })
  }
  view.rerender(<MemoryRouter><RepairTrialPanel taskId="task" revision={revision} enabled open={false} onClose={() => {}} onTrials={() => {}} /></MemoryRouter>)
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: { query: { type: 'string' } } }, inputSchemaDigest: 'schema', targets: [] })
  view.rerender(panel())
  await screen.findByText('没有已授权且支持工作流运行的生产 Bot。')
  const start = screen.getByRole('button', { name: uncertain ? '核对并重试原请求' : '开始试跑' })
  if (!uncertain) { expect(start).toBeDisabled(); expect(api.startTrial).not.toHaveBeenCalled() }
  else {
    expect(start).toBeEnabled(); await userEvent.click(start)
    expect(api.startTrial.mock.calls[1]).toEqual(api.startTrial.mock.calls[0])
  }
})
it('never selects a Bot or executes until explicit action, sends typed input and deduplicates double click', async () => {
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  const select = await screen.findByRole('combobox', { name: '执行 Bot' })
  expect(select).toHaveValue('')
  expect(api.startTrial).not.toHaveBeenCalled()
  await userEvent.selectOptions(select, 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'hello')
  await userEvent.dblClick(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledOnce()
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ revision: 1, input: { query: 'hello' }, targetId: 'prod:owner:bot' }))
  expect(api.startTrial.mock.calls[0][1]).not.toHaveProperty('mode')
})
it('reloads persistent results, labels old candidates and renders untrusted output as text', async () => {
  api.trials.mockResolvedValue({ configured: true, trials: [trial] })
  render(panel({ ...revision as object, draft: { candidateCommit: 'b'.repeat(40) } } as never))
  expect(await screen.findByText('该结果属于旧候选')).toBeInTheDocument()
  expect(screen.getByText(/等待审批或人工输入/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /运行详情/ })).toHaveAttribute('href', '/runs/real-flow')
  expect(document.querySelector('script')).toBeNull()
  expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument()
})
it('recovers lost response with the same request and never swaps Bot/input', async () => {
  api.startTrial.mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox'), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'first')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  await screen.findByRole('button', { name: '核对并重试原请求' })
  expect(screen.getByRole('textbox', { name: 'query' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '核对并重试原请求' }))
  await waitFor(() => expect(api.startTrial).toHaveBeenCalledTimes(2))
  expect(api.startTrial.mock.calls[1]).toEqual(api.startTrial.mock.calls[0])
})

it('shows a useful JSON validation error and allows correction without dispatch', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: null, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox'), 'prod:owner:bot')
  expect(screen.queryByRole('textbox', { name: '输入参数 JSON' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '高级：编辑 JSON' }))
  const input = screen.getByRole('textbox', { name: '输入参数 JSON' })
  await userEvent.clear(input); await userEvent.type(input, 'invalid')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('参数 JSON 格式无效')
  expect(api.startTrial).not.toHaveBeenCalled(); expect(input).toBeEnabled()
})

it('blocks old-host saved execution, explains the upgrade and retains the historical saved-result limitation', async () => {
  const saved = { ...trial, mode: 'saved', status: 'succeeded' }
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: { query: { type: 'string' } } }, inputSchemaDigest: 'schema',
    targets: [{ id: 'prod:owner:bot', label: 'Production Bot', executionMode: 'saved' }] })
  api.startTrial.mockResolvedValue(saved)
  api.trials.mockResolvedValue({ configured: true, trials: [saved] })
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  expect(screen.queryByRole('checkbox', { name: '使用旧插件兼容模式' })).not.toBeInTheDocument()
  expect(screen.getByText(/无法安全固定当前候选.*升级 ClawMind/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始试跑' })).toBeDisabled()
  expect(api.startTrial).not.toHaveBeenCalled()
  expect(screen.getByText('兼容执行：远端保存版 · 运行成功')).toBeInTheDocument()
  expect(screen.getByText(/此结果不代表已验证候选/)).toBeInTheDocument()
  expect(screen.queryByText('候选 v1 · 运行成功')).not.toBeInTheDocument()
})

it('unlocks Bot/input after an explicit structured capability rejection before trial creation', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: { query: { type: 'string' } } }, inputSchemaDigest: 'schema', targets: [
    { id: 'prod:owner:bot', label: 'Production Bot' }, { id: 'prod:owner:other', label: 'Other Bot' },
  ] })
  api.startTrial.mockRejectedValueOnce(Object.assign(new Error('rejected'), { status: 503, body: JSON.stringify({ code: 'CAPABILITY_UNAVAILABLE', error: '请升级所选 Bot 的 ClawMind。' }) })).mockResolvedValueOnce(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'preserved')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('请升级所选 Bot 的 ClawMind。')
  expect(screen.getByRole('textbox', { name: 'query' })).toBeEnabled()
  expect(screen.getByRole('combobox', { name: '执行 Bot' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '核对并重试原请求' })).not.toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '执行 Bot' }), 'prod:owner:other')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial.mock.calls[1][1]).toMatchObject({ targetId: 'prod:owner:other', input: { query: 'preserved' } })
  expect(api.startTrial.mock.calls[1][1].requestId).not.toBe(api.startTrial.mock.calls[0][1].requestId)
})

it.each([
  { status: 503, body: '<html>CAPABILITY_UNAVAILABLE</html>' },
  { status: 503, body: JSON.stringify({ code: 'UPSTREAM_TIMEOUT', error: 'CAPABILITY_UNAVAILABLE' }) },
  { status: 500, body: JSON.stringify({ code: 'CAPABILITY_UNAVAILABLE' }) },
])('keeps the same locked request after an unknown server failure: %j', async failure => {
  api.startTrial.mockRejectedValueOnce(Object.assign(new Error('unknown'), failure)).mockResolvedValueOnce(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'query' }), 'original')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  await screen.findByRole('button', { name: '核对并重试原请求' })
  expect(screen.getByRole('textbox', { name: 'query' })).toBeDisabled()
  expect(screen.getByRole('combobox', { name: '执行 Bot' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '核对并重试原请求' }))
  expect(api.startTrial.mock.calls[1]).toEqual(api.startTrial.mock.calls[0])
})

it('renders old-host field maps and initializes falsy defaults only once for a candidate', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { userId: { type: 'string', default: '' }, dutyWorkId: { type: 'integer', default: 0 }, enabled: { type: 'boolean', default: false }, taskId: { type: 'string', default: 'initial' } }, inputSchemaDigest: 'schema', targets: [] })
  const view = render(panel())
  expect(await screen.findByRole('textbox', { name: 'userId' })).toHaveValue('')
  expect(screen.getByRole('spinbutton', { name: 'dutyWorkId' })).toHaveValue(0)
  expect(screen.getByRole('checkbox', { name: 'enabled' })).not.toBeChecked()
  const input = screen.getByRole('textbox', { name: 'taskId' })
  await userEvent.clear(input); await userEvent.type(input, 'edited')
  view.rerender(<MemoryRouter><RepairTrialPanel taskId="task" revision={revision} enabled open={false} onClose={() => {}} onTrials={() => {}} /></MemoryRouter>)
  view.rerender(panel())
  expect(await screen.findByRole('textbox', { name: 'taskId' })).toHaveValue('edited')
  expect(screen.queryByRole('textbox', { name: '输入参数 JSON' })).not.toBeInTheDocument()
})

it('uses business key/value fields for missing definitions and locks submitted inputs', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: null, inputDefinition: 'missing', inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: '参数名 1' }), 'userId')
  await userEvent.type(screen.getByRole('textbox', { name: '参数值 1' }), '2088')
  await userEvent.click(screen.getByRole('button', { name: '添加参数' }))
  await userEvent.type(screen.getByRole('textbox', { name: '参数名 2' }), 'taskId')
  await userEvent.type(screen.getByRole('textbox', { name: '参数值 2' }), 'task-a')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ input: { userId: '2088', taskId: 'task-a' } }))
  expect(screen.getByRole('combobox', { name: '执行 Bot' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: '参数值 1' })).toBeDisabled()
})

it('shows explicitly parameterless workflows and sends an empty object', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { type: 'object', properties: {} }, inputDefinition: 'none', inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  expect(await screen.findByText('此工作流无需参数。')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ input: {} }))
})

it('distinguishes load failure from loading and retries without dispatch', async () => {
  api.trialOptions.mockRejectedValueOnce(new Error('cannot read definition'))
  render(panel())
  expect(await screen.findByRole('alert')).toHaveTextContent('无法读取候选参数和授权 Bot，请重试。')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始试跑' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '重新读取参数' }))
  expect(await screen.findByRole('textbox', { name: 'query' })).toBeInTheDocument()
  expect(api.startTrial).not.toHaveBeenCalled()
})

it('ignores options returned after closing, including after a later reopen', async () => {
  let late!: (value: unknown) => void
  api.trialOptions.mockImplementationOnce(() => new Promise(resolve => { late = resolve }))
  const view = render(panel())
  await waitFor(() => expect(api.trialOptions).toHaveBeenCalledOnce())
  view.rerender(<MemoryRouter><RepairTrialPanel taskId="task" revision={revision} enabled open={false} onClose={() => {}} onTrials={() => {}} /></MemoryRouter>)
  view.rerender(panel())
  expect(await screen.findByRole('textbox', { name: 'query' })).toBeInTheDocument()
  await act(async () => late({ inputSchema: { properties: { obsolete: { type: 'string' } } }, targets: [], inputSchemaDigest: 'old' }))
  expect(screen.queryByRole('textbox', { name: 'obsolete' })).not.toBeInTheDocument()
})

it('shows unavailability for the selected Bot and prevents submission', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: null, inputDefinition: 'none', inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot', executionMode: 'unavailable', unavailableReason: '此 Bot 暂不可用' }] })
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  expect(screen.getByText('此 Bot 暂不可用')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开始试跑' })).toBeDisabled()
})

it('submits typed falsy defaults, numeric enums and structured values with sensitive field metadata', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: {
    count: { type: 'integer', default: 0 }, active: { type: 'boolean', default: false }, query: { type: 'string', default: '' },
    choice: { type: 'integer', enum: [1, 2] }, tags: { type: 'array', items: { type: 'integer' } }, credential: { type: 'string', sensitive: true },
  } }, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'choice' }), '2')
  await userEvent.click(screen.getByRole('button', { name: '添加 tags 条目' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'tags.1' })); await userEvent.type(screen.getByRole('spinbutton', { name: 'tags.1' }), '1')
  await userEvent.click(screen.getByRole('button', { name: '添加 tags 条目' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'tags.2' })); await userEvent.type(screen.getByRole('spinbutton', { name: 'tags.2' }), '2')
  expect(screen.getByLabelText('credential')).toHaveAttribute('type', 'password')
  await userEvent.type(screen.getByLabelText('credential'), 'secret-test')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ input: { count: 0, active: false, query: '', choice: 2, tags: [1, 2], credential: 'secret-test' } }))
})

it('renders required-only fields, validates them before dispatch and allows choosing advanced input immediately', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { type: 'object', required: ['userId'] }, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  expect(screen.getByRole('textbox', { name: 'userId' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('请填写必填参数：userId')
  expect(api.startTrial).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '高级：编辑 JSON' }))
  expect(screen.getByRole('textbox', { name: '输入参数 JSON' })).toBeInTheDocument()
})

it('keeps malformed definitions separate from a missing definition', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { userId: 'string' }, inputSchemaDigest: 'schema', targets: [] })
  render(panel())
  expect(await screen.findByRole('alert')).toHaveTextContent('参数定义格式无效')
  expect(screen.queryByRole('textbox', { name: '参数名 1' })).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('ignores an old candidate response after switching candidates', async () => {
  let late!: (value: unknown) => void
  api.trialOptions.mockImplementationOnce(() => new Promise(resolve => { late = resolve }))
  const view = render(panel())
  await waitFor(() => expect(api.trialOptions).toHaveBeenCalledOnce())
  view.rerender(panel({ ...revision as object, revision: 2, draft: { candidateCommit: 'b'.repeat(40) } } as never))
  expect(await screen.findByRole('textbox', { name: 'query' })).toBeInTheDocument()
  await act(async () => late({ inputSchema: { properties: { obsolete: { type: 'string' } } }, targets: [], inputSchemaDigest: 'old' }))
  expect(screen.queryByRole('textbox', { name: 'obsolete' })).not.toBeInTheDocument()
})

it('uses nested business controls and nullable numeric/boolean widgets without asking for JSON', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { type: 'object', required: ['userId'], properties: {
    userId: { type: 'string' }, profile: { type: 'object', properties: {
      displayName: { type: 'string' }, settings: { type: 'object', properties: {
        retries: { type: ['integer', 'null'], default: 0 }, enabled: { type: ['boolean', 'null'], default: false }, note: { type: 'string', default: '' },
      } },
    } },
  } }, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  expect(document.querySelector('textarea')).toBeNull()
  expect(screen.getByRole('spinbutton', { name: 'profile.settings.retries' })).toHaveValue(0)
  expect(screen.getByRole('checkbox', { name: 'profile.settings.enabled' })).not.toBeChecked()
  await userEvent.type(screen.getByRole('textbox', { name: 'userId' }), 'user-a')
  await userEvent.type(screen.getByRole('textbox', { name: 'profile.displayName' }), 'Alice')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ input: { userId: 'user-a', profile: { displayName: 'Alice', settings: { retries: 0, enabled: false, note: '' } } } }))
})

it('adds and removes structured array entries and preserves typed enums and boolean values', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: {
    orders: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, count: { type: 'integer', default: 0 }, active: { type: 'boolean', default: false } } } },
    choices: { type: 'array', items: { type: 'integer', enum: [1, 2] } },
  } }, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.click(screen.getByRole('button', { name: '添加 orders 条目' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'orders.1.id' }), 'discard')
  await userEvent.click(screen.getByRole('button', { name: '添加 orders 条目' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'orders.2.id' }), 'kept')
  await userEvent.click(screen.getByRole('button', { name: '移除 orders 第 1 项' }))
  expect(screen.getByRole('textbox', { name: 'orders.1.id' })).toHaveValue('kept')
  await userEvent.click(screen.getByRole('button', { name: '添加 choices 条目' }))
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'choices.1' }), '2')
  expect(document.querySelector('textarea')).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ input: { orders: [{ id: 'kept', count: 0, active: false }], choices: [2] } }))
})

it('uses business key/value fields for open objects and string entries for arrays without item definitions', async () => {
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: { details: { type: 'object' }, tags: { type: 'array' } } }, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.type(screen.getByRole('textbox', { name: 'details 新字段名' }), 'taskId')
  await userEvent.type(screen.getByRole('textbox', { name: 'details 新字段值' }), 'task-1')
  await userEvent.click(screen.getByRole('button', { name: 'details 添加字段' }))
  await userEvent.click(screen.getByRole('button', { name: '添加 tags 条目' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'tags.1' }), 'business')
  expect(document.querySelector('textarea')).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledWith('task', expect.objectContaining({ input: { details: { taskId: 'task-1' }, tags: ['business'] } }))
})

it('omits cleared optional numeric fields recursively while preserving nullable null, zero, false and untouched defaults', async () => {
  const schema = { type: 'object', properties: {
    limit: { type: 'integer', default: 5 }, nullable: { type: ['number', 'null'], default: 3 }, zero: { type: 'integer', default: 0 }, enabled: { type: 'boolean', default: false },
    details: { type: 'object', properties: { optional: { type: 'number', default: 1.5 }, unchanged: { type: 'integer', default: 7 } } },
    rows: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, optional: { type: 'integer' } } }, default: [{ id: 'a', optional: 4 }] },
    nullableValues: { type: 'array', items: { type: ['integer', 'null'] }, default: [1] },
  } }
  api.trialOptions.mockResolvedValue({ inputSchema: schema, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'limit' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'nullable' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'details.optional' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'rows.1.optional' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'nullableValues.1' }))
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  const input = api.startTrial.mock.calls[0][1].input
  expect(input).toEqual({ nullable: null, zero: 0, enabled: false, details: { unchanged: 7 }, rows: [{ id: 'a' }], nullableValues: [null] })
  expect(Object.hasOwn(input, 'limit')).toBe(false)
  expect(Object.hasOwn(input.details, 'optional')).toBe(false)
  expect(Object.hasOwn(input.rows[0], 'optional')).toBe(false)
  expect(new Ajv({ strict: false }).compile(schema)(input)).toBe(true)
})

it('keeps cleared required numeric fields invalid instead of restoring defaults or silently deleting them', async () => {
  const schema = { type: 'object', required: ['count'], properties: { count: { type: 'integer', default: 5 }, details: { type: 'object', required: ['count'], properties: { count: { type: 'integer', default: 7 } } }, values: { type: 'array', items: { type: 'integer' }, default: [2] } } }
  api.trialOptions.mockResolvedValue({ inputSchema: schema, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockRejectedValue(Object.assign(new Error('invalid'), { status: 400, body: JSON.stringify({ code: 'INVALID_INPUT', error: '嵌套数值必填参数无效' }) }))
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'count' }))
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('请填写必填参数：count')
  expect(api.startTrial).not.toHaveBeenCalled()
  await userEvent.type(screen.getByRole('spinbutton', { name: 'count' }), '0')
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'details.count' }))
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'values.1' }))
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  await screen.findByText('嵌套数值必填参数无效')
  const input = api.startTrial.mock.calls[0][1].input
  expect(input).toEqual({ count: 0, details: { count: '' }, values: [''] })
  expect(new Ajv({ strict: false }).compile(schema)(input)).toBe(false)
})

it.each([
  { type: 'object', additionalProperties: true },
  { type: 'object', additionalProperties: { type: 'string' }, minProperties: 1 },
  { minProperties: 1 },
])('offers business key/value fields for a schema without named properties: %j', async schema => {
  api.trialOptions.mockResolvedValue({ inputSchema: schema, inputSchemaDigest: 'schema', targets: [{ id: 'prod:owner:bot', label: 'Production Bot' }] })
  api.startTrial.mockResolvedValue(trial)
  render(panel())
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'prod:owner:bot')
  expect(screen.queryByText('此工作流无需参数。')).not.toBeInTheDocument()
  await userEvent.type(screen.getByRole('textbox', { name: '参数名 1' }), 'taskId')
  await userEvent.type(screen.getByRole('textbox', { name: '参数值 1' }), 'task-1')
  await userEvent.click(screen.getByRole('button', { name: '开始试跑' }))
  const input = api.startTrial.mock.calls[0][1].input
  expect(input).toEqual({ taskId: 'task-1' })
  expect(new Ajv({ strict: false }).compile(schema)(input)).toBe(true)
})
