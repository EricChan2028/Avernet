import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { BotEditorResource, BotEditorResourcePreview } from '@/domain/botEditor';
import { buildVisibleResourceTree, formatResourceBytes } from '@/services/botWorkshop/resourceTree';
import {
  ChevronRight,
  Copy,
  Download,
  File,
  FilePlus,
  Folder,
  FolderOpen,
  FolderPlus,
  Loader2,
  Trash2,
  Upload,
} from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { ResourcePreviewDrawer } from './ResourcePreviewDrawer';

/** 行首留白（px），与设计稿行内 icon 前 7px 呼应 */
const ROW_BASE_PADDING = 8;
/** 每层目录缩进步距（px） */
const INDENT_STEP = 20;

export function ResourcePanel({
  resources,
  editable,
  desktop = false,
  onOpenFolder,
  onCreateDirectory,
  onDelete,
  onUpload,
  onPreview,
  onDownload,
  onCopyPath,
  onLoadDirectory,
  loadingPaths,
}: {
  resources: BotEditorResource[];
  editable: boolean;
  desktop?: boolean;
  onOpenFolder?: (path?: string) => Promise<void>;
  onCreateDirectory: (path: string) => Promise<void>;
  onDelete: (path: string) => Promise<void>;
  onUpload: (path: string, file: File) => Promise<void>;
  onPreview: (path: string) => Promise<BotEditorResourcePreview>;
  onDownload: (path: string, type: BotEditorResource['type']) => Promise<void>;
  onCopyPath: (path: string) => Promise<void>;
  onLoadDirectory: (path: string) => Promise<void>;
  loadingPaths: string[];
}) {
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<{ path: string; result: BotEditorResourcePreview }>();
  const [directory, setDirectory] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [createParent, setCreateParent] = useState('');
  const [expanded, setExpanded] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<string[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const uploadDirectoryRef = useRef('');
  const visibleResources = useMemo(() => buildVisibleResourceTree(resources, expanded), [expanded, resources]);
  const breadcrumbSegments = useMemo(() => directory.split('/').filter(Boolean), [directory]);
  const resourceIconClassName = (item: BotEditorResource) => {
    if (item.type === 'folder') return 'text-warning';
    if (/\.json$/i.test(item.name)) return 'text-violet-500';
    if (/\.(md|mdx)$/i.test(item.name)) return 'text-emerald-500';
    return 'text-zinc-400';
  };
  const uploadToDirectory = async (target: string, file: File) => {
    await onUpload(target ? `${target}/${file.name}` : file.name, file);
    if (target) await onLoadDirectory(target);
  };
  const toggleDirectory = (target: string) => {
    const open = expanded.includes(target);
    setDirectory(target);
    if (open) {
      setExpanded((current) => current.filter((path) => path !== target));
      return;
    }
    setExpanded((current) => [...current, target]);
    if (!loaded.includes(target)) {
      void onLoadDirectory(target)
        .then(() => setLoaded((current) => [...new Set([...current, target])]))
        .catch(() => undefined);
    }
  };
  return (
    <div className="flex min-h-full flex-col bg-background">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border bg-card px-8 py-5 lg:px-10">
        <div>
          <h2 className="m-0 text-sm font-semibold tracking-tight">资源目录</h2>
          <p className="m-0 mt-1 text-xs text-muted-foreground">上传文件、新建目录，支持预览、下载与删除。</p>
        </div>
        <div className="flex max-w-full shrink-0 flex-wrap justify-end gap-2">
          {desktop && onOpenFolder ? (
            <Button variant="outline" size="sm" onClick={() => void onOpenFolder(directory || undefined)}>
              打开本地目录
            </Button>
          ) : null}
          <Input
            ref={uploadRef}
            hidden
            type="file"
            aria-label="资源文件选择"
            onChange={(event) => {
              const file = event.target.files?.[0];
              const target = uploadDirectoryRef.current;
              // 失败提示已由 act 的 toast 负责，吞掉传播避免 dev 遮罩报 Unhandled Rejection
              if (file) void uploadToDirectory(target, file).catch(() => undefined);
              event.target.value = '';
            }}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={!editable}
            leftIcon={<Upload className="size-4" />}
            onClick={() => {
              uploadDirectoryRef.current = directory;
              uploadRef.current?.click();
            }}
          >
            上传文件
          </Button>
          <Button
            size="sm"
            disabled={!editable}
            leftIcon={<FolderPlus className="size-4" />}
            onClick={() => {
              setCreateParent(directory);
              setCreateOpen(true);
            }}
          >
            新建目录
          </Button>
        </div>
      </div>
      <div className="w-full px-8 pb-10 pt-6 lg:px-10">
        <div className="mb-4 flex min-h-11 items-center gap-2 rounded-lg border border-border bg-card px-4">
          <Folder aria-hidden className="size-4 shrink-0 text-zinc-400" />
          <nav aria-label="当前目录路径" className="flex min-w-0 items-center gap-1.5 text-[13px]">
            <span className="shrink-0 text-zinc-500 dark:text-zinc-400">根目录</span>
            {breadcrumbSegments.map((segment, index) => (
              <span key={`${index}:${segment}`} className="flex min-w-0 items-center gap-1.5">
                <ChevronRight aria-hidden className="size-3 shrink-0 text-zinc-300 dark:text-zinc-600" />
                <span
                  className={`truncate ${
                    index === breadcrumbSegments.length - 1
                      ? 'font-medium text-foreground'
                      : 'text-zinc-600 dark:text-zinc-400'
                  }`}
                >
                  {segment}
                </span>
              </span>
            ))}
          </nav>
          <span className="ml-auto hidden shrink-0 pl-4 text-xs text-zinc-400 sm:block">点击文件夹可切换上传位置</span>
        </div>
        {resources.length ? (
          <div className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
            {visibleResources.map(({ item, depth }) => {
              const isFolder = item.type === 'folder';
              const isOpen = expanded.includes(item.path);
              const selected = directory === item.path || preview?.path === item.path;
              return (
                <div
                  key={item.path}
                  data-depth={depth}
                  className={`group relative flex h-9 items-center rounded-md pr-1.5 transition-colors ${
                    selected ? 'bg-primary/5' : 'hover:bg-zinc-200/80 dark:hover:bg-zinc-800/70'
                  }`}
                  style={{
                    // 文件行补一个箭头槽（16px 图标 + 6px 间距），使两层 icon 纵向对齐
                    paddingLeft: `${ROW_BASE_PADDING + depth * INDENT_STEP + (isFolder ? 0 : 22)}px`,
                  }}
                >
                  {/* 祖先层级导轨：行行相接拼成连续竖线（dmore 051 的 Line 12 规格） */}
                  {Array.from({ length: depth }, (_, index) => (
                    <span
                      key={index}
                      aria-hidden
                      data-rail
                      className="absolute inset-y-0 w-px bg-zinc-200 dark:bg-zinc-800"
                      style={{ left: `${ROW_BASE_PADDING + index * INDENT_STEP + 9}px` }}
                    />
                  ))}
                  {isFolder ? (
                    <Button
                      variant="ghost"
                      className="h-auto min-w-0 flex-1 justify-start gap-1.5 rounded-md px-1.5 py-1.5 text-left font-normal hover:bg-transparent"
                      aria-label={`${isOpen ? '收起' : '展开'}${item.name}`}
                      aria-expanded={isOpen}
                      title={item.path}
                      onClick={() => toggleDirectory(item.path)}
                      leftIcon={
                        loadingPaths.includes(item.path) ? (
                          <Loader2 className="size-4 shrink-0 animate-spin text-zinc-400" />
                        ) : (
                          <ChevronRight
                            aria-hidden
                            className={`size-4 shrink-0 text-zinc-400 transition-transform duration-150 ${
                              isOpen ? 'rotate-90' : ''
                            }`}
                          />
                        )
                      }
                    >
                      {isOpen ? (
                        <FolderOpen aria-hidden className="size-4 shrink-0 text-warning" />
                      ) : (
                        <Folder aria-hidden className="size-4 shrink-0 text-warning" />
                      )}
                      <span className="ml-1 min-w-0 flex-1 truncate text-[13px] font-normal text-zinc-600 dark:text-zinc-400">
                        {item.name}
                      </span>
                      <span className="sr-only">{item.path}</span>
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      className="h-auto min-w-0 flex-1 justify-start gap-2 rounded-md px-1.5 py-1.5 text-left font-normal hover:bg-transparent"
                      aria-label={`预览${item.name}`}
                      title={item.path}
                      disabled={desktop && (item.size ?? 0) > 1048576}
                      // 预览失败（如大文件 413000）已由全局错误通道提示，吞掉传播避免 dev 遮罩报 Unhandled Rejection
                      onClick={() =>
                        void onPreview(item.path)
                          .then((result) => setPreview({ path: item.path, result }))
                          .catch(() => undefined)
                      }
                    >
                      <File aria-hidden className={`size-4 shrink-0 ${resourceIconClassName(item)}`} />
                      <span className="min-w-0 flex-1 truncate text-[13px] font-normal text-zinc-600 dark:text-zinc-400">
                        {item.name}
                      </span>
                      {item.size !== undefined ? (
                        <span className="mr-1 shrink-0 text-xs tabular-nums text-zinc-400">
                          {formatResourceBytes(item.size)}
                        </span>
                      ) : null}
                      <span className="sr-only">
                        {item.path}
                        {item.size !== undefined ? ` · ${formatResourceBytes(item.size)}` : ''}
                      </span>
                    </Button>
                  )}
                  <div
                    className={`relative z-10 flex shrink-0 items-center gap-0.5 transition-opacity ${
                      selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
                    }`}
                  >
                    {item.type === 'folder' ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7 rounded-md text-zinc-500"
                          disabled={!editable}
                          aria-label={`向${item.name}添加文件`}
                          leftIcon={<FilePlus className="size-4" />}
                          onClick={() => {
                            uploadDirectoryRef.current = item.path;
                            uploadRef.current?.click();
                          }}
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7 rounded-md text-zinc-500"
                          disabled={!editable}
                          aria-label={`在${item.name}中新建子目录`}
                          leftIcon={<FolderPlus className="size-4" />}
                          onClick={() => {
                            setCreateParent(item.path);
                            setCreateOpen(true);
                          }}
                        />
                      </>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 rounded-md text-zinc-500"
                      aria-label={`复制路径${item.name}`}
                      leftIcon={<Copy className="size-4" />}
                      onClick={() => void onCopyPath(item.path)}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7 rounded-md text-zinc-500"
                      disabled={desktop && item.type === 'folder'}
                      aria-label={`下载${item.type === 'folder' ? '文件夹' : '文件'}${item.name}`}
                      leftIcon={<Download className="size-4" />}
                      onClick={() => void onDownload(item.path, item.type)}
                    />
                    <ConfirmDialog
                      title="删除资源"
                      description={`确认递归删除「${item.path}」？`}
                      confirmVariant="destructive"
                      onConfirm={() => onDelete(item.path)}
                      disabled={!editable}
                    >
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 rounded-md text-zinc-500 hover:text-destructive"
                        aria-label={`删除${item.name}`}
                        leftIcon={<Trash2 className="size-4" />}
                      />
                    </ConfirmDialog>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border bg-card py-14">
            <Empty compact title="工作区为空" description="当前目录没有文件或文件夹。" />
          </div>
        )}
      </div>
      <ResourcePreviewDrawer preview={preview} onClose={() => setPreview(undefined)} />
      <Modal open={createOpen} onOpenChange={setCreateOpen}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>{createParent ? `在 ${createParent} 中新建子目录` : '新建目录'}</ModalTitle>
          </ModalHeader>
          <Input autoFocus value={path} onChange={(event) => setPath(event.target.value)} placeholder="输入目录名称" />
          <ModalFooter>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button
              disabled={!path.trim()}
              onClick={() =>
                void onCreateDirectory(createParent ? `${createParent}/${path.trim()}` : path.trim())
                  .then(async () => {
                    if (createParent) await onLoadDirectory(createParent);
                    setPath('');
                    setCreateOpen(false);
                  })
                  .catch(() => undefined)
              }
            >
              创建
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}