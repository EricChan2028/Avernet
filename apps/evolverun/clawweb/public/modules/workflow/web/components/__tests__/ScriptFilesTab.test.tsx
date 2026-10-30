import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ScriptFilesTab from '../ScriptFilesTab'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })

describe('ScriptFilesTab', () => {
  it('reads the authenticated snapshot without offering unsafely wired writes', async () => {
    vi.mocked(fetchJson).mockResolvedValueOnce({ workflowId: 'wf-1', packId: 'p1', baseRef: 'origin/wf/wf-1', baseCommit: 'a'.repeat(40), files: [{ path: 'scripts/run.py', content: 'print(1)\n', mode: '100755' }] })
    render(<ScriptFilesTab workflowId="wf-1" />)
    expect(await screen.findByLabelText('脚本内容')).toHaveValue('print(1)\n')
    expect(screen.getByLabelText('脚本内容')).toHaveAttribute('readonly')
    expect(screen.getByText(/脚本编辑和发布暂不可用/)).toBeInTheDocument()
    expect(vi.mocked(fetchJson)).toHaveBeenCalledWith('/api/workflows/wf-1/pack-scripts', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('ignores a late response from a previously selected workflow', async () => {
    let resolveOld!: (value: unknown) => void
    vi.mocked(fetchJson).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    vi.mocked(fetchJson).mockResolvedValueOnce({ workflowId: 'wf-2', packId: 'p2', baseRef: 'origin/wf/wf-2', baseCommit: 'b'.repeat(40), files: [{ path: 'scripts/new.py', content: 'new', mode: '100644' }] })
    const view = render(<ScriptFilesTab workflowId="wf-1" />)
    view.rerender(<ScriptFilesTab workflowId="wf-2" />)
    expect(await screen.findByLabelText('脚本内容')).toHaveValue('new')
    resolveOld({ workflowId: 'wf-1', packId: 'p1', baseRef: 'origin/wf/wf-1', baseCommit: 'a'.repeat(40), files: [{ path: 'scripts/old.py', content: 'old', mode: '100644' }] })
    await waitFor(() => expect(screen.getByLabelText('脚本内容')).toHaveValue('new'))
    expect(screen.queryByText('scripts/old.py')).not.toBeInTheDocument()
  })

  it('does not fetch or imply an empty snapshot for an unsaved workflow', () => {
    render(<ScriptFilesTab workflowId={null} />)
    expect(screen.getByText(/请先保存工作流/)).toBeInTheDocument()
    expect(fetchJson).not.toHaveBeenCalled()
  })

  it.each([
    [401, '请先登录'], [403, '没有编辑权限'], [404, '未找到'], [409, '冲突或共享 Pack'], [503, '服务暂不可用'],
  ])('provides an actionable, sanitized message for HTTP %i', async (status, message) => {
    vi.mocked(fetchJson).mockRejectedValueOnce(Object.assign(new Error('sensitive private detail'), { status }))
    render(<ScriptFilesTab workflowId="wf-1" />)
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByText(/sensitive private detail/)).not.toBeInTheDocument()
  })

  it('does not expose a raw Git error or show stale file contents after failure', async () => {
    vi.mocked(fetchJson).mockRejectedValueOnce(new Error('sensitive remote details'))
    render(<ScriptFilesTab workflowId="wf-1" />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/无法读取脚本/))
    expect(screen.queryByText(/sensitive remote details/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('脚本内容')).not.toBeInTheDocument()
  })
})
