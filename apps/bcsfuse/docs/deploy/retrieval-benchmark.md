# Qdrant Local / Server 单实例对比（实验分支）

## 本轮测什么

每个后端各测四项：关键词检索、向量检索、带前置标签过滤的关键词检索、带前置标签过滤的向量检索。
关键词采用现有中文二元词／英文词的稀疏向量和 IDF；不是 Server 新增的全文 BM25 功能。
**不执行 Worker ID 精确匹配、业务合并、精排、结果缓存或数据库回查。**
旧接口约 200 ms 的结果包含 ID 精确匹配，不能与本版纯检索数字混在一起。

准备阶段从 TC 已加载的 Local 索引复制一次真实向量和资料，创建独立的内存 Local 集合及 Server 集合。
两者使用同一份记录、同一维度和距离算法、相同稀疏向量；之后不跟随 TC 更新。
复制时短暂持有 TC 索引锁；上传 Server、建索引和调用模型均在释放锁后执行。
只支持最多 5000 条记录的诊断快照；请在低流量时准备并观察内存。TC 原集合和业务检索不变。

查询样本的 dense 向量由已配置 Embedding 模型生成，sparse 向量由本地分词生成。
两者都在准备阶段完成，搜索阶段仅按样本编号查询，无模型调用、不用随机向量。
同一个样本集用于全部对比轮次；不要在 Local 与 Server 两轮之间重新生成。

Local 保留独立互斥锁以避免并发访问风险，Server 不加此锁。
`store` 耗时包含 Local 锁等待或 Server HTTP/gRPC 往返；属于存储访问耗时，不能称为引擎内部 CPU 时间。
同机 Server 与 TC 共享 CPU/内存，不是独立资源池。不要同时压两个后端。

## HTTP 契约 v2（新增 gRPC 对照后端）

沿用宿主认证、预发环境开关和 `/internal/bench` 路径。公共应用不自动挂载接口。

| 接口 | 请求及行为 |
|---|---|
| `GET /info` | 返回实例、dataset/sample ID、数据条数、维度、`search_scope=direct_qdrant`、已配置的 `backends`、过滤字段、Server 准备时的版本及索引状态 |
| `GET /server-info` | 实时返回 Server 的记录数、索引向量数、集合/优化器状态；Server 不可用返回 503 |
| `POST /dataset` | `{"include_server":true,"filter_fields":{"fragment_type":"keyword","availability":"keyword","runtime_state":"keyword"}}`；一次性准备两套固定数据。字段类型可选 keyword/integer/bool |
| `POST /samples` | `{"queries":["告警","查询未解决的缺陷"],"with_vectors":true}`；准备真实 dense 和 sparse 查询向量，返回 sample_set_id。仅测关键词可设 false |
| `POST /search` | `{"backend":"server","mode":"keyword","sample_set_id":"...","sample_index":0,"top_k":10,"filters":{"fragment_type":"profile"}}` |

`backend=local/server/server_grpc`；`mode=keyword/vector`。保留 `both` 兼容旧调用，表示两次顺序查询，但本轮不使用它评估单项能力。
`server` 保留原 HTTP 调用；`server_grpc` 使用宿主配置的 gRPC 客户端，二者查询同一个 Server 集合。客户端均在启动时创建并复用，不在请求中重建连接。选择 gRPC 不会自动回退到 HTTP/Local；未配置或调用失败返回 503。`backends` 只表示已配置，不表示服务就绪；仍需准备集合并实际查询。

过滤条件直接传入 Qdrant `query_filter`，先过滤再取 Top K；多字段 AND，字段值列表表示任一匹配。
过滤字段必须在 `/dataset` 声明；Server 为这些字段创建 payload 索引，Local 不支持这种索引加速。
Top K 是向量记录数量，不是聚合后的 Agent 数量。返回 ID 和分数，不返回资料和向量。

每个应用进程只准备一份数据，重复准备返回 409；更换数据或过滤字段需重启测试进程。
初始化失败返回错误，可修复后重试；不会回退到 TC 或 Local。
未准备数据/向量、sample 过期返回 409，未声明过滤字段返回 422，超过默认 32 个在途搜索返回 429。
Server 不可用返回 503，不能将错误计成正常空结果。关闭测试路由不影响 TC 原接口。
正常退出删除本进程创建的 Server 测试集合；异常退出可能留下孤立测试集合，可在停止测试后清理独立测试目录。

## 机器内操作（无需安装压测依赖）

OCB 分支镜像会复制下列脚本到 `/home/admin/release/bcsfuse/scripts/`。
固定访问 `127.0.0.1:8765`，避免域名路由到另一台实例。默认 1 QPS、30 秒、8 并发。

```bash
cd /home/admin/release/bcsfuse
# 当前导出的 800 条记录中存在这些字段；新数据集按实际字段调整。
/home/admin/run/bin/python scripts/qdrant_compare_bench.py prepare \
  --filter-fields '{"fragment_type":"keyword","availability":"keyword","runtime_state":"keyword"}'

/home/admin/run/bin/python scripts/qdrant_compare_bench.py run --backend local --mode keyword
/home/admin/run/bin/python scripts/qdrant_compare_bench.py run --backend local --mode vector
/home/admin/run/bin/python scripts/qdrant_compare_bench.py run --backend local --mode keyword --filters '{"fragment_type":"profile"}'
/home/admin/run/bin/python scripts/qdrant_compare_bench.py run --backend local --mode vector --filters '{"fragment_type":"profile"}'
# 将上面四条的 --backend local 分别改为 --backend server、--backend server_grpc，再依次运行。
# 逐级增压示例：--qps 5 --seconds 60 --concurrency 8
```

脚本输出目标/实际 QPS、未发出的请求数、错误、空结果数、HTTP/store/调度 P50/P95/P99。
出现错误立即停止继续发压；未发出的请求表示发压不足或并发已满，不是服务器主动丢弃。
机器内发压也占服务机资源，只适合首轮定位。正式容量测试用另一台压测机固定访问目标实例。
不要用全空结果判断有效容量；选择高/低选择率标签，并记录命中比例。
现有 800 条导出样本中，fragment_type=profile 有 262 条，availability=public 有 177 条，runtime_state=online 有 345 条；这只是样本分布，不代表预发全部 2637 条。

## k6 固定到达率

先通过上述接口准备数据和样本，再复用它们运行。认证通过环境注入，不写脚本或日志。

```bash
export BASE_URL='<固定实例地址>' QUERIES_FILE='<查询 JSON 文件绝对路径>'
export REUSE_SAMPLES=1 BACKEND=local
RATES='1 5 10' MODES='keyword vector' bash scripts/run_retrieval_bench.sh
# 分别改 BACKEND=server、BACKEND=server_grpc 测相同样本；设置 FILTERS='{"fragment_type":"profile"}' 测前置过滤。
```

每轮记录 dataset/sample/instance ID、实际 QPS、P99、失败率、CPU/RSS、日志增量及 Server 索引状态。
脚本同时记录 Server 每轮前后的索引状态；索引仍在变化时，不将该轮作为稳态容量。`indexed_vectors_count=0` 表示尚无已建好的 dense HNSW 索引，不能把结果当成 HNSW 能力。
最终实例数必须按目标机器持续压测和冗余需求计算，不能套用 Mac 的短时结果。
