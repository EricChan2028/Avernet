import { describe, expect, it } from 'vitest';
import { normalizeTrialInputDefinition as normalize } from '../repair-trial-input-definition';

describe('normalizeTrialInputDefinition', () => {
  it('normalizes the deployed field-map shape without inventing properties named type or required', () => {
    expect(normalize({ schema: { userId: { type: 'string', description: '账户', sensitive: true }, dutyWorkId: { type: 'integer' }, taskId: { enum: ['a', 'b'] } }, requiredParams: ['userId'] })).toEqual({
      inputDefinition: 'defined', inputSchema: { type: 'object', required: ['userId'], properties: {
        userId: { type: 'string', description: '账户', sensitive: true }, dutyWorkId: { type: 'integer' }, taskId: { enum: ['a', 'b'] },
      } },
    });
    expect(normalize({ schema: { type: { type: 'string' }, required: { type: 'boolean' } } }).inputSchema?.properties).toEqual({ type: { type: 'string' }, required: { type: 'boolean' } });
  });
  it('preserves JSON Schema constraints and merges separate required fields', () => {
    const schema = { type: 'object', additionalProperties: false, required: ['query'], properties: { query: { type: 'string', minLength: 2 } } };
    expect(normalize({ schema, requiredParams: ['userId', 'query'] }).inputSchema).toEqual({ ...schema, required: ['query', 'userId'], properties: { ...schema.properties, userId: { type: 'string' } } });
    expect(schema.properties).toEqual({ query: { type: 'string', minLength: 2 } });
  });
  it('distinguishes business fields named properties/required/type from the JSON Schema wrapper', () => {
    const businessFields = { properties: { type: 'string', description: '业务属性' }, required: { type: 'boolean' }, type: { type: 'string' } };
    expect(normalize({ schema: businessFields }).inputSchema?.properties).toEqual(businessFields);
    const schema = { type: 'object', required: ['properties'], properties: businessFields };
    expect(normalize({ schema }).inputSchema).toEqual(schema);
    expect(normalize({ schema: { properties: { enum: ['a', 'b'] } } }).inputSchema?.properties).toEqual({ properties: { enum: ['a', 'b'] } });
  });
  it('presents required-only inputs with explicit input requirements overriding legacy ones', () => {
    expect(normalize({ requiredParams: ['taskId'] }, ['oldId']).inputSchema).toMatchObject({ required: ['taskId'], properties: { taskId: { type: 'string' } } });
    expect(normalize(undefined, ['userId']).inputSchema).toMatchObject({ required: ['userId'], properties: { userId: { type: 'string' } } });
  });
  it('uses actual root params values as defaults, retaining false, zero, empty strings and structured values', () => {
    expect(normalize({ params: { enabled: false, count: 0, query: '', tags: ['a'], context: { id: 1 } } }).inputSchema?.properties).toEqual({
      enabled: { type: 'boolean', default: false }, count: { type: 'number', default: 0 }, query: { type: 'string', default: '' },
      tags: { type: 'array', default: ['a'] }, context: { type: 'object', default: { id: 1 } },
    });
    expect(normalize({ schema: { count: { type: 'integer', default: 0 } }, params: { count: 5 } }).inputSchema?.properties).toEqual({ count: { type: 'integer', default: 0 } });
  });
  it('distinguishes missing declarations from explicit empty declarations', () => {
    for (const input of [undefined, null, { schema: null }, { schema: undefined, params: undefined }]) expect(normalize(input)).toEqual({ inputSchema: null, inputDefinition: 'missing' });
    for (const input of [{}, { schema: {} }, { schema: { type: 'object' } }, { schema: { type: 'object', properties: {} } }, { requiredParams: [] }, { params: {} }]) expect(normalize(input).inputDefinition).toBe('none');
  });
  it.each([
    { schema: [] }, { schema: 'text' }, { schema: { query: 'string' } }, { schema: { properties: [] } },
    { schema: { properties: { query: 3 } } }, { requiredParams: 'query' }, { requiredParams: [3] }, { params: [] },
  ])('rejects malformed input definitions: %j', definition => {
    expect(() => normalize(definition)).toThrow(/参数定义/);
  });
});
