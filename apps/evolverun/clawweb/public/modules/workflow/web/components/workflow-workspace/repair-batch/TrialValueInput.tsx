import { useState } from 'react'
import { button } from './repair-view'

export type TrialValueSchema = {
  type?: string | string[]; title?: string; description?: string; enum?: unknown[]; default?: unknown;
  sensitive?: boolean; writeOnly?: boolean; format?: string; required?: string[];
  properties?: Record<string, TrialValueSchema | boolean>; items?: TrialValueSchema | boolean | TrialValueSchema[];
}
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value) }
export function trialValueType(schema: TrialValueSchema | boolean, value?: unknown): string {
  const spec = typeof schema === 'boolean' ? {} : schema
  const declared = Array.isArray(spec.type) ? spec.type.find(type => type !== 'null') ?? 'null' : spec.type
  return declared ?? (spec.properties ? 'object' : spec.items ? 'array' : Array.isArray(value) ? 'array' : value !== null && typeof value === 'object' ? 'object' : typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string')
}
function properties(schema: TrialValueSchema): Record<string, TrialValueSchema | boolean> {
  const fields = { ...schema.properties }
  for (const name of schema.required ?? []) if (!Object.hasOwn(fields, name)) fields[name] = { type: 'string' }
  return fields
}
function itemSchema(schema: TrialValueSchema, index: number): TrialValueSchema | boolean {
  return Array.isArray(schema.items) ? schema.items[index] ?? {} : schema.items ?? {}
}
export function trialValueDefaults(schema: TrialValueSchema | boolean): unknown {
  if (typeof schema === 'boolean') return undefined
  if (Object.hasOwn(schema, 'default')) return structuredClone(schema.default)
  if (trialValueType(schema) !== 'object') return undefined
  const defaults = Object.fromEntries(Object.entries(properties(schema)).map(([key, spec]) => [key, trialValueDefaults(spec)]).filter(([, value]) => value !== undefined))
  return Object.keys(defaults).length ? defaults : undefined
}
function newValue(schema: TrialValueSchema | boolean): unknown {
  const defaults = trialValueDefaults(schema)
  if (defaults !== undefined) return defaults
  if (typeof schema !== 'boolean' && schema.enum?.length) return structuredClone(schema.enum[0])
  switch (trialValueType(schema)) {
    case 'object': return {}
    case 'array': return []
    case 'number': case 'integer': return 0
    case 'boolean': return false
    case 'null': return null
    default: return ''
  }
}
/** Checkbox controls represent false even when a required field has not been touched. */
export function completeTrialValue(schema: TrialValueSchema | boolean, value: unknown): unknown {
  if (typeof schema === 'boolean') return value
  if (Array.isArray(value)) return value.map((item, index) => completeTrialValue(itemSchema(schema, index), item))
  if (!record(value)) return value
  const fields = properties(schema)
  const completed = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, completeTrialValue(fields[key] ?? {}, item)]))
  for (const key of schema.required ?? []) if (completed[key] === undefined && trialValueType(fields[key] ?? {}) === 'boolean') completed[key] = false
  return completed
}

const control = 'mt-1 block w-full min-w-0 rounded border border-input bg-background p-2'
type Props = { schema: TrialValueSchema | boolean; name: string; value: unknown; disabled: boolean; required?: boolean; sensitive?: boolean; onChange: (value: unknown) => void }
function OpenObjectInput({ name, value, disabled, sensitive, onChange }: Omit<Props, 'schema'>) {
  const [key, setKey] = useState('')
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const current = record(value) ? value : {}
  return <div className="space-y-2">
    {Object.entries(current).map(([key, item]) => <div key={key} className="space-y-1 rounded border border-input p-2">
      <TrialValueInput schema={{}} name={`${name}.${key}`} value={item} disabled={disabled} sensitive={sensitive} onChange={next => onChange({ ...current, [key]: next })} />
      <button type="button" aria-label={`移除 ${name}.${key}`} className={button} disabled={disabled} onClick={() => onChange(Object.fromEntries(Object.entries(current).filter(([other]) => other !== key)))}>移除字段</button>
    </div>)}
    <div className="flex flex-wrap gap-2">
      <input aria-label={`${name} 新字段名`} placeholder="字段名" className={`${control} flex-1`} value={key} disabled={disabled} onChange={event => { setKey(event.target.value); setError('') }} />
      <input aria-label={`${name} 新字段值`} placeholder="字段值" type={sensitive || /password|token|secret|key/i.test(key) ? 'password' : 'text'} className={`${control} flex-1`} value={text} disabled={disabled} onChange={event => setText(event.target.value)} />
      <button type="button" aria-label={`${name} 添加字段`} className={button} disabled={disabled || !key.trim()} onClick={() => {
        if (Object.hasOwn(current, key.trim())) { setError('字段名不能重复。'); return }
        onChange({ ...current, [key.trim()]: text }); setKey(''); setText(''); setError('')
      }}>添加字段</button>
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </div>
}
export default function TrialValueInput({ schema, name, value, disabled, required, sensitive, onChange }: Props) {
  const spec = typeof schema === 'boolean' ? {} : schema
  const type = trialValueType(schema, value)
  const masked = sensitive || spec.sensitive || spec.writeOnly || spec.format === 'password' || /password|token|secret|key/i.test(name)
  const label = <>{spec.title ?? name.split('.').at(-1)}{required ? ' *' : ''}</>
  const description = spec.description && <span className="mt-1 block text-muted-foreground">{spec.description}</span>
  if (schema === false) return <p className="text-sm text-muted-foreground">{label}：此字段不接受输入。</p>
  if (!spec.enum && type === 'object') {
    const fields = properties(spec), current = record(value) ? value : {}
    return <fieldset aria-label={name} className="min-w-0 space-y-3 rounded border border-input p-3"><legend className="px-1 text-sm">{label}</legend>
      {Object.keys(fields).length ? Object.entries(fields).map(([key, child]) => <TrialValueInput key={key} schema={child} name={`${name}.${key}`} value={current[key]} required={spec.required?.includes(key)} disabled={disabled} sensitive={masked} onChange={next => onChange({ ...current, [key]: next })} />)
        : <OpenObjectInput name={name} value={current} disabled={disabled} sensitive={masked} onChange={onChange} />}
      {description}
    </fieldset>
  }
  if (!spec.enum && type === 'array') {
    const values = Array.isArray(value) ? value : []
    return <fieldset aria-label={name} className="min-w-0 space-y-3 rounded border border-input p-3"><legend className="px-1 text-sm">{label}</legend>
      {values.map((item, index) => <div key={index} className="space-y-2 rounded border border-input p-2">
        <TrialValueInput schema={itemSchema(spec, index)} name={`${name}.${index + 1}`} value={item} disabled={disabled} sensitive={masked} onChange={next => onChange(values.map((previous, i) => i === index ? next : previous))} />
        <button type="button" aria-label={`移除 ${name} 第 ${index + 1} 项`} className={button} disabled={disabled} onClick={() => onChange(values.filter((_, i) => i !== index))}>移除条目</button>
      </div>)}
      <button type="button" aria-label={`添加 ${name} 条目`} className={button} disabled={disabled || spec.items === false} onClick={() => onChange([...values, newValue(itemSchema(spec, values.length))])}>添加条目</button>
      {description}
    </fieldset>
  }
  return <label className="block break-words text-sm">{label}
    {spec.enum ? <select aria-label={name} className={control} disabled={disabled} value={value === undefined ? '' : JSON.stringify(value)} onChange={event => onChange(JSON.parse(event.target.value))}>
      <option value="" disabled>请选择</option>{spec.enum.map((item, index) => <option key={JSON.stringify(item)} value={JSON.stringify(item)}>{typeof item === 'object' ? `选项 ${index + 1}` : String(item)}</option>)}
    </select> : type === 'boolean' ? <input aria-label={name} type="checkbox" checked={value === true} disabled={disabled} onChange={event => onChange(event.target.checked)} />
      : <input aria-label={name} type={masked ? 'password' : ['number', 'integer'].includes(type) ? 'number' : 'text'} step={type === 'integer' ? 1 : 'any'} className={control} disabled={disabled} value={value == null ? '' : String(value)}
        onChange={event => { const text = event.target.value; onChange(['number', 'integer'].includes(type) && text !== '' ? Number(text) : text) }} />}
    {description}
  </label>
}
