import { Textarea } from '@/components/ui';
import type { GroupQuestion, QuestionDraft } from '@/domain/collaboration/groupQuestions';
import { cn } from '@/utils/cn';

export interface GroupQuestionEditorProps {
  question: GroupQuestion;
  interactionKey: string;
  draft: QuestionDraft;
  disabled: boolean;
  registerFirstControl: (element: HTMLElement | null) => void;
  onToggleOption: (optionValue: string) => void;
  onOtherChange: (text: string) => void;
}

/** 单题编辑区：选项与自定义输入保持在表单状态机之外，便于复用并限制父组件职责。 */
export function GroupQuestionEditor({
  question,
  interactionKey,
  draft,
  disabled,
  registerFirstControl,
  onToggleOption,
  onOtherChange,
}: GroupQuestionEditorProps) {
  const showOtherInput = question.options.length === 0 || question.allowOther;
  const otherLabel = question.options.length > 0 ? '自定义回答' : '你的回答';

  return (
    <div className="rounded-md border border-input bg-background p-3">
      <div className="flex items-center gap-2">
        <p className="text-sm font-medium text-foreground">{question.question}</p>
        {draft.skipped ? (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">已跳过</span>
        ) : null}
      </div>
      {question.options.length > 0 ? (
        <div role="group" aria-label={question.question} className="mt-2 space-y-1">
          {question.options.map((option, index) => (
            <label
              key={option.value}
              className={cn(
                'flex items-start gap-2 rounded-md border border-input px-3 py-2 text-sm transition-colors',
                draft.values.includes(option.value) ? 'border-primary/60 bg-primary/5' : 'bg-background',
                disabled ? 'opacity-60' : 'cursor-pointer hover:bg-muted/50',
              )}
            >
              <input
                ref={index === 0 ? registerFirstControl : undefined}
                type={question.multiSelect ? 'checkbox' : 'radio'}
                name={`${interactionKey}:${question.questionId}`}
                value={option.value}
                checked={draft.values.includes(option.value)}
                onChange={() => onToggleOption(option.value)}
                disabled={disabled}
                className="mt-0.5"
              />
              <span className="leading-snug">
                {option.label}
                {option.description ? (
                  <span className="block text-xs text-muted-foreground">{option.description}</span>
                ) : null}
              </span>
            </label>
          ))}
        </div>
      ) : null}
      {showOtherInput ? (
        <label className="mt-2 block">
          <span className="mb-1 block text-xs text-muted-foreground">{otherLabel}</span>
          <Textarea
            ref={question.options.length === 0 ? registerFirstControl : undefined}
            aria-label={otherLabel}
            rows={2}
            size="sm"
            value={draft.other}
            onChange={(event) => onOtherChange(event.target.value)}
            disabled={disabled}
            placeholder={question.options.length > 0 ? '填写选项以外的回答' : '填写你的回答'}
          />
        </label>
      ) : null}
    </div>
  );
}
