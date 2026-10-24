# 单实例检索压测（实验分支）

执行顺序、优先级、验收口径和结果记录表见 [支付宝 Agent 搜索压测方案](retrieval-benchmark-plan.md)。批量执行使用 `scripts/run_retrieval_bench.sh`。

用于判断当前 Qdrant Local 存储封装的吞吐；不代表完整推荐接口或独立 Qdrant Server 的性能。
公共应用默认不挂载测试路由，部署宿主必须显式接入并保护访问。接口只读已有索引，不生成 Agent、不修改数据库、不触发重建。

## 查询向量从哪里来

准备阶段将真实查询词交给部署应用配置的 Embedding 服务，生成向量后保存在当前进程内。
压测阶段按样本编号调用存储，**不再调用模型，不缓存搜索结果，不使用随机向量**。
仅测关键词时可跳过向量准备。预置的查询文件只是输入格式示例，请换成实际业务样本。
向量与索引必须使用同一种模型及维度；名称与维度一致也不能证明历史索引使用同一模型，需核对部署记录。

## HTTP 契约 v1

认证由部署宿主显式注入。使用 `benchmark_bearer_auth` 的宿主要求 `Authorization: Bearer <运行时注入的压测凭据>`，未通过返回 401；OCB 实验分支预发沿用已有网关登录和内网访问限制，无需额外压测 Token。公共应用仍不自动挂载路由，也不提供隐式匿名认证。

| 接口 | 请求／结果 |
| --- | --- |
| `GET /internal/bench/info` | 实例及进程标识、存储类型、向量条数、维度、模型、依赖版本、样本数、并发上限、日志 Handler 和生效级别；不返回向量、查询文本或凭据 |
| `POST /internal/bench/samples` | `{"queries":["查账","订票"],"with_vectors":true}`；返回 `sample_set_id`、`sample_count` 和样本来源。每批 1—64 条，每条最多 2048 字符；整批成功后替换旧样本，失败返回 503 并保留旧样本 |
| `POST /internal/bench/search` | `{"sample_set_id":"准备接口返回的ID","sample_index":0,"mode":"both","top_k":10,"filters":{}}`；返回两类命中的 ID/分数及耗时，不返回画像或向量 |

`mode` 可为 `vector`、`keyword`、`both`。`both` 顺序执行两种检索，保留当前存储的锁行为，**不包含业务合并、过滤、精排或查库**。TopK 是存储层的片段数量，可取 1—1000，不是最终 Agent 数。
筛选支持平铺的字符串、整数、布尔值及字符串／整数列表；最多 16 个字段。
`sample_index` 从 0 开始。样本编号不存在返回 404；样本集过期、请求进入另一进程、未准备向量却发起向量查询返回 409。
同一时间仅允许一批样本准备，重复准备返回 409。默认最多 32 个在途搜索，超出返回 429；存储异常返回 503，不伪装成正常空结果。

## 发起压测

压测客户端需要 k6。在**另一台机器**发压，固定访问一个已完成索引加载的应用实例，并保持一个应用进程。
每个进程的样本集不同；脚本发现实例不一致或样本集变化会中止，避免负载均衡导致误测。

先在调用环境设置 `BASE_URL`。OCB 预发使用 `BCSFUSE_BENCH_COOKIE` 传入已有登录 Cookie；独立 Bearer 模式使用 `BCSFUSE_BENCH_TOKEN`。凭据不写入脚本、提交记录或报告；脚本不跟随登录重定向，也不会把网关返回的 HTTP 200 登录错误当成正常测试接口。
在 Avernet 的 `apps/bcsfuse` 目录执行：

```bash
MODE=vector RATE=50 DURATION=2m QUERIES_FILE=./retrieval_bench_queries.example.json \
  k6 run --summary-export /tmp/vector-50.json scripts/retrieval_bench.js
MODE=keyword RATE=50 DURATION=2m QUERIES_FILE=./retrieval_bench_queries.example.json \
  k6 run --summary-export /tmp/keyword-50.json scripts/retrieval_bench.js
MODE=both RATE=50 DURATION=2m QUERIES_FILE=./retrieval_bench_queries.example.json \
  k6 run --summary-export /tmp/both-50.json scripts/retrieval_bench.js
```

k6 的文件路径相对于脚本目录；上例读取 `scripts/retrieval_bench_queries.example.json`。实际样本可用绝对路径指定。
脚本先检查索引非空并准备样本，再按固定到达率发压。准备模型向量的时间不计入搜索压测。
默认仅 10 QPS、30 秒；显式设置 RATE/DURATION 后再逐级提高。可用 TOP_K、FILTERS（JSON）、VUS、MAX_VUS 调整样本条件及压测端并发。
每轮依次尝试 50、100、200、500 QPS，接近瓶颈时缩小步长；候选稳定点至少运行 30 分钟，并追加后台同步／更新场景。

## 如何读数据

- `bench_store_ms`：存储调用总耗时，**包含原有索引锁等待**，没有独立测量锁等待。
- `bench_vector_ms` / `bench_keyword_ms`：两条路径耗时；`both` 的 QPS 表示每请求执行两次检索。
- `bench_dispatch_wait_ms`：进入线程池执行前的等待；HTTP 延迟还包括鉴权、JSON、响应及网络。
- `bench_http_ms`：搜索请求的 HTTP 耗时，报告 P99；不要用包含样本准备请求的全局 `http_req_duration` 作为搜索延迟。
- `bench_valid`、HTTP 错误和 `dropped_iterations`：拒绝、失败、压测端未发出的请求都不能计为有效 QPS。
- 同时从实例监控记录 CPU、当前 RSS、磁盘写入、日志目录增长、连接及后台同步状态。info 中 peak RSS 是进程历史峰值，不是当前内存。

用预期 Agent 数对应的**实际片段数**、真实 4096 或所选维度、不同 TopK 和过滤条件测试。
固定单个查询只能用于热点诊断，不能代表多样化业务负载；本接口没有结果缓存。
当前读写共用锁，不要在此实验中直接去掉锁。多进程不能同时打开同一路径的 Qdrant Local 文件；增加进程数不等于自动获得线性扩展。

## 日志与实例数

先保留现状日志，测每请求字节数、QPS/P99，再用显式 LOG_LEVEL=INFO 对照；必要时用 WARNING 做诊断对照。
日志级别在部署环境修改后重启，不在压测请求中修改全局 Logger。业务候选明细应另在完整推荐链路验证，本接口不会执行那些日志点。
info 仅描述 Python Handler；stdout 重定向、网关、sidecar 和容器采集还需查看部署配置，不能据此认定已经异步或有磁盘总量上限。
日志量估算为有效 QPS × 每请求日志字节数 × 持续秒数，轮转期间应累计活动文件与轮转文件的增量。

本次先给出存储容量，不直接承诺业务实例数。后续完整检索流程测得留有余量的单实例稳定容量 q 后，
三可用区均匀部署、容忍一个区退出的实例数为 `3 × ceil(1667 / (2 × q))`，再用集群压测验证。
关闭压测开关并重启即可移除路由；本功能不改变现有数据和业务检索行为。
