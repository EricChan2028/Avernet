export type TrialInputDefinition = { schema?: unknown; requiredParams?: unknown; params?: unknown };
export type NormalizedTrialInputDefinition = { inputSchema: Record<string, unknown> | null; inputDefinition: 'defined' | 'none' | 'missing' };

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}
export class TrialInputDefinitionError extends Error {}
function invalid(): never { throw new TrialInputDefinitionError('工作流参数定义格式无效，请检查 input.schema、requiredParams 和 params。'); }
function names(value: unknown): string[] {
  if (!Array.isArray(value) || value.some(key => typeof key !== 'string' || !key.trim())) invalid();
  return [...new Set(value as string[])];
}
function field(value: unknown): Record<string, unknown> | boolean {
  if (typeof value === 'boolean') return value;
  if (!record(value)) invalid();
  const types = ['string', 'number', 'integer', 'boolean', 'object', 'array', 'null'];
  if (value.type !== undefined && !(typeof value.type === 'string' ? types.includes(value.type)
    : Array.isArray(value.type) && value.type.length && value.type.every(type => types.includes(type)))) invalid();
  if (value.enum !== undefined && !Array.isArray(value.enum)) invalid();
  if (value.required !== undefined) names(value.required);
  return { ...value };
}
function fieldDescriptor(value: unknown): boolean {
  if (!record(value)) return false;
  return typeof value.type === 'string' || Array.isArray(value.type) || Array.isArray(value.enum) || Array.isArray(value.required)
    || ['title', 'description', 'format', 'pattern'].some(key => typeof value[key] === 'string')
    || ['sensitive', 'writeOnly'].some(key => typeof value[key] === 'boolean')
    || ['minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems'].some(key => typeof value[key] === 'number')
    || Object.hasOwn(value, 'default') && !record(value.default);
}

/** Normalize the two existing workflow schema shapes and legacy parameter declarations.
 * Root params is a value map, not a second field-descriptor protocol.
 * This pure boundary is shared by candidate inspection and older-host UI compatibility.
 */
export function normalizeTrialInputDefinition(definition: TrialInputDefinition | null | undefined, legacyRequiredParams?: unknown): NormalizedTrialInputDefinition {
  if (definition != null && !record(definition)) invalid();
  const requiredValue = definition?.requiredParams !== undefined ? definition.requiredParams : legacyRequiredParams;
  const required = requiredValue === undefined ? [] : names(requiredValue);
  const raw = definition?.schema;
  if (raw != null && !record(raw)) invalid();
  const params = definition?.params;
  if (params !== undefined && !record(params)) invalid();
  const declared = raw != null || requiredValue !== undefined || params !== undefined
    || definition != null && Object.keys(definition).length === 0;
  if (!declared) return { inputSchema: null, inputDefinition: 'missing' };

  let schema: Record<string, unknown> = { type: 'object' };
  let properties: Record<string, Record<string, unknown> | boolean> = {};
  if (record(raw)) {
    const jsonSchema = typeof raw.type === 'string' || Array.isArray(raw.type) || 'properties' in raw && !fieldDescriptor(raw.properties)
      || Array.isArray(raw.required) || ['$schema', '$id', '$ref'].some(key => typeof raw[key] === 'string')
      || ['allOf', 'anyOf', 'oneOf'].some(key => Array.isArray(raw[key]))
      || ['$defs', 'definitions', 'patternProperties'].some(key => record(raw[key]) && !fieldDescriptor(raw[key]))
      || ['additionalProperties', 'not'].some(key => typeof raw[key] === 'boolean' || record(raw[key]));
    if (jsonSchema) {
      if (raw.type !== undefined && raw.type !== 'object') invalid();
      if (raw.properties !== undefined && !record(raw.properties)) invalid();
      if (raw.required !== undefined) names(raw.required);
      schema = { ...raw, type: 'object' };
      properties = Object.fromEntries(Object.entries(raw.properties ?? {}).map(([key, value]) => [key, field(value)]));
    } else properties = Object.fromEntries(Object.entries(raw).map(([key, value]) => [key, field(value)]));
  }
  for (const [key, value] of Object.entries(params ?? {})) {
    const existing = Object.hasOwn(properties, key) ? properties[key] : undefined;
    if (typeof existing === 'boolean') continue;
    const type = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
    if (!['string', 'number', 'boolean', 'object', 'array', 'null'].includes(type)) invalid();
    properties[key] = { ...(existing ?? { type }), ...(!existing || !Object.hasOwn(existing, 'default') ? { default: value } : {}) };
  }
  const allRequired = [...new Set([...names(schema.required ?? []), ...required])];
  for (const key of allRequired) if (!Object.hasOwn(properties, key)) properties[key] = { type: 'string' };
  const inputSchema = { ...schema, properties, ...(allRequired.length || schema.required !== undefined ? { required: allRequired } : {}) };
  const constrained = ['$ref', 'allOf', 'anyOf', 'oneOf', 'not', 'patternProperties'].some(key => key in schema);
  return { inputSchema, inputDefinition: Object.keys(properties).length || constrained ? 'defined' : 'none' };
}
