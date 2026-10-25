// k6: fixed arrival rate, precomputed real query vectors, one pinned process.
import http from 'k6/http';
import { check, fail } from 'k6';
import execution from 'k6/execution';
import { Rate, Trend } from 'k6/metrics';

const base = (__ENV.BASE_URL || '').replace(/\/$/, '');
const token = __ENV['BCSFUSE_BENCH_TOKEN'];
const loginCookie = __ENV['BCSFUSE_BENCH_COOKIE'];
const mode = __ENV.MODE || 'keyword';
const backend = __ENV.BACKEND || 'local';
const topK = Number(__ENV.TOP_K || 10);
const queries = JSON.parse(open(__ENV.QUERIES_FILE || './retrieval_bench_queries.example.json'));
const filters = JSON.parse(__ENV.FILTERS || '{}');
const maxErrorRate = Number(__ENV.MAX_ERROR_RATE || '0.001');
const p99Limit = Number(__ENV.P99_LIMIT_MS || '0');
if (!Number.isFinite(maxErrorRate) || maxErrorRate < 0 || maxErrorRate >= 1 ||
    !Number.isFinite(p99Limit) || p99Limit < 0) {
  throw new Error('MAX_ERROR_RATE must be in [0,1); P99_LIMIT_MS must be nonnegative');
}
if (!base || (!token && !loginCookie) || !['vector', 'keyword', 'both'].includes(mode)) {
  throw new Error('Set BASE_URL, benchmark token or gateway cookie, and MODE=vector|keyword|both');
}
if (!Array.isArray(queries) || queries.length < 1 || queries.length > 64) {
  throw new Error('QUERIES_FILE must contain 1..64 representative query strings');
}

const valid = new Rate('bench_valid');
const limited = new Rate('bench_limited');
const serverError = new Rate('bench_server_error');
const transportError = new Rate('bench_transport_error');
const emptyResults = new Rate('bench_empty_results');
const httpTime = new Trend('bench_http_ms', true);
const storeTime = new Trend('bench_store_ms', true);
const dispatchWait = new Trend('bench_dispatch_wait_ms', true);
const vectorTime = new Trend('bench_vector_ms', true);
const keywordTime = new Trend('bench_keyword_ms', true);
const headers = { 'Content-Type': 'application/json',
  ...(token ? { Authorization: `Bearer ${token}` } : {}),
  ...(loginCookie ? { Cookie: loginCookie } : {}),
};

export const options = {
  setupTimeout: '10m',
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  scenarios: {
    search: {
      executor: 'constant-arrival-rate', rate: Number(__ENV.RATE || 10), timeUnit: '1s',
      duration: __ENV.DURATION || '30s', preAllocatedVUs: Number(__ENV.VUS || 32),
      maxVUs: Number(__ENV.MAX_VUS || 128), gracefulStop: '30s',
    },
  },
  thresholds: {
    bench_valid: [`rate>=${1 - maxErrorRate}`],
    'http_req_failed{phase:search}': [`rate<=${maxErrorRate}`],
    dropped_iterations: ['count==0'],
    ...(p99Limit > 0 ? { bench_http_ms: [`p(99)<=${p99Limit}`] } : {}),
  },
};

export function setup() {
  const infoResponse = http.get(`${base}/internal/bench/info`, { headers, redirects: 0, tags: { phase: 'setup' } });
  if (infoResponse.status !== 200) fail(`info failed: HTTP ${infoResponse.status}`);
  const info = infoResponse.json();
  if (info.storage_only !== true || typeof info.instance_id !== 'string') {
    fail('Response is not benchmark info; check gateway login and deployed benchmark routes');
  }
  if (backend === 'server_grpc' && !(info.backends || []).includes(backend)) fail('Deploy the gRPC benchmark first');
  if (info.search_scope !== 'direct_qdrant' || !info.dataset_ready || (backend !== 'local' && !info.server_ready)) fail('Prepare direct-Qdrant dataset first');
  if (!(info.vector_count > 0)) fail('Index is empty; do not measure empty-index throughput');
  if (__ENV.REUSE_SAMPLES === '1') {
    if (!info.sample_count || (mode !== 'keyword' && info.sample_source !== 'configured_embedding')) fail('Prepare query samples first');
    console.log(JSON.stringify({ backend, mode, dataset_id: info.dataset_id, sample_set_id: info.sample_set_id, instance_id: info.instance_id, filters, vector_count: info.vector_count }));
    return info;
  }
  const prepared = http.post(`${base}/internal/bench/samples`, JSON.stringify({
    queries, with_vectors: mode !== 'keyword',
  }), { headers, redirects: 0, timeout: '9m', tags: { phase: 'setup' } });
  if (prepared.status !== 200) fail(`sample preparation failed: HTTP ${prepared.status}`);
  const samples = prepared.json();
  if (samples.instance_id !== info.instance_id) fail('Requests reached different processes; pin one instance');
  console.log(JSON.stringify({ instance_id: info.instance_id, vector_count: info.vector_count,
    dimension: info.dimension, model: info.model, packages: info.packages,
    sample_source: samples.sample_source, sample_count: samples.sample_count,
    mode, top_k: topK, max_error_rate: maxErrorRate, p99_limit_ms: p99Limit,
    max_inflight: info.max_inflight, logging: info.logging }));
  return { ...samples, dataset_id: info.dataset_id };
}

export default function (samples) {
  const response = http.post(`${base}/internal/bench/search`, JSON.stringify({
    sample_set_id: samples.sample_set_id,
    sample_index: execution.scenario.iterationInTest % samples.sample_count,
    mode, backend, top_k: topK, filters,
  }), { headers, redirects: 0, timeout: '10s', tags: { phase: 'search', mode } });
  httpTime.add(response.timings.duration);
  limited.add(response.status === 429);
  serverError.add(response.status >= 500);
  transportError.add(response.status === 0);
  let data = null;
  try { data = response.json(); } catch (_) { /* Count malformed responses as failures. */ }
  if (response.status === 409 || (data && data.instance_id && data.instance_id !== samples.instance_id)) {
    execution.test.abort('Sample set changed or traffic reached another process; pin one instance');
  }
  const ok = check(response, {
    'valid storage response': () => response.status === 200 && data &&
      data.sample_set_id === samples.sample_set_id && data.instance_id === samples.instance_id &&
      data.mode === mode && data.backend === backend && data.dataset_id === samples.dataset_id &&
      data.search_scope === 'direct_qdrant' && Array.isArray(data.vector_hits) && Array.isArray(data.keyword_hits) &&
      data.timing_ms && Number.isFinite(data.timing_ms.store),
  });
  valid.add(ok);
  if (ok) {
    emptyResults.add(data.vector_hits.length + data.keyword_hits.length === 0);
    storeTime.add(data.timing_ms.store);
    dispatchWait.add(data.timing_ms.dispatch_wait);
    if (mode !== 'keyword') vectorTime.add(data.timing_ms.vector);
    if (mode !== 'vector') keywordTime.add(data.timing_ms.keyword);
  }
}
