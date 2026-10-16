import Ajv from 'ajv';
import { containsSecret } from '@avernet/clawweb-shared/server/services/redaction';
import { RepairBatchError, boundedRepairJson } from '../contracts/repair-batch.js';
const sensitiveField = /password|passwd|secret|token|credential|authorization|cookie|api.?key|private.?key/i;
const privateKey = (key: string) => sensitiveField.test(key) || containsSecret({ [key]: 'redaction-probe' });
function redactText(value: string): string {
  return value.replace(/\b(Bearer\s+)\S+|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+/gi, '[REDACTED]')
    .replace(/((?:password|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*)[^\s,;]+/gi, '$1[REDACTED]');
}
/** History is always text, capped, and strips sensitive keys plus exact input values from output. */
export function redactTrial(value: unknown, sensitiveValues: string[] = []): unknown {
  if (typeof value === 'string') {
    let text = redactText(value);
    for (const raw of sensitiveValues.sort((a, b) => b.length - a.length)) if (raw) text = text.split(raw).join('[REDACTED]');
    return text.slice(0, 8192);
  }
  if (Array.isArray(value)) return value.slice(0, 100).map(item => redactTrial(item, sensitiveValues));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).slice(0, 100).map(([key, item]) => [key, privateKey(key) ? '[REDACTED]' : redactTrial(item, sensitiveValues)]));
  return value;
}
function schemaRecord(schema: unknown): Record<string, unknown> {
  return schema && typeof schema === 'object' && !Array.isArray(schema) ? schema as Record<string, unknown> : {};
}
function privateSchema(schema: unknown): boolean {
  const node = schemaRecord(schema);
  return node.sensitive === true || node.writeOnly === true || node.format === 'password';
}
function allStrings(value: unknown): string[] {
  return ['string', 'number', 'boolean'].includes(typeof value) ? [String(value)] : value && typeof value === 'object' ? Object.values(value).flatMap(allStrings) : [];
}
export function trialSensitiveValues(value: unknown, schema?: unknown): string[] {
  if (privateSchema(schema)) return allStrings(value);
  const node = schemaRecord(schema);
  if (Array.isArray(value)) return value.flatMap((item, index) => trialSensitiveValues(item, Array.isArray(node.items) ? node.items[index] : node.items));
  if (!value || typeof value !== 'object') return [];
  const properties = schemaRecord(node.properties);
  return Object.entries(value).flatMap(([key, item]) => privateKey(key) ? allStrings(item)
    : trialSensitiveValues(item, properties[key] ?? node.additionalProperties));
}
/** Schema annotations protect business-named fields as well as conventional secret keys. */
export function redactTrialInput(value: unknown, schema?: unknown): unknown {
  if (privateSchema(schema)) return '[REDACTED]';
  const node = schemaRecord(schema);
  if (Array.isArray(value)) return value.slice(0, 100).map((item, index) => redactTrialInput(item, Array.isArray(node.items) ? node.items[index] : node.items));
  if (value && typeof value === 'object') {
    const properties = schemaRecord(node.properties);
    return Object.fromEntries(Object.entries(value).slice(0, 100).map(([key, item]) => [key, privateKey(key) ? '[REDACTED]'
      : redactTrialInput(item, properties[key] ?? node.additionalProperties)]));
  }
  return redactTrial(value);
}
export function validateTrialInput(input: unknown, schema: Record<string, unknown> | null): asserts input is Record<string, unknown> {
  boundedRepairJson(input, 32 * 1024);
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new RepairBatchError('INVALID_INPUT', '参数需要 JSON 对象');
  if (!schema) return;
  // Full JSON Schema validation, without coercion, implicit defaults or remote refs.
  try {
    const validate = new Ajv({ strict: false, allErrors: true, ownProperties: true }).compile(schema);
    if (!validate(input)) throw new RepairBatchError('INVALID_INPUT', `参数校验失败：${validate.errors?.slice(0, 5).map(error => `${error.instancePath || '$'} ${error.message}`).join('；')}`);
  } catch (error) {
    if (error instanceof RepairBatchError) throw error;
    throw new RepairBatchError('INVALID_INPUT', '候选的参数定义无法校验，请检查 input.schema');
  }
}
