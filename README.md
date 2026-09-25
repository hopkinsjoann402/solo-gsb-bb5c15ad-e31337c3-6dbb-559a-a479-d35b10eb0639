# 临时展位三相配电排布 API

基于 **Python + FastAPI + SQLite** 的临时展位三相配电排布服务。

提交最多 **12 台设备**（唯一编号、正整数功率、非空可接入相位集合）以及 **A/B/C 各相容量**，
服务在「所有设备均接入、每台设备整体接入单一允许相位、各相不超容量」的约束下求解分配方案：

1. **首要目标**：使三相负载最大值与最小值之差最小；
2. **次要目标**：差值相同时，取按设备编号排序后的相位序列字典序最小的方案；
3. 不存在可行分配时，返回明确的 `feasible: false` 结果与说明信息。

每次计算的**输入、分配结果与各相负载**均持久化到 SQLite，可随时查询历史记录。
对于原容量下无可行分配的记录，还可通过扩容接口获取「各相最小扩容量 + 对应分配」
建议（保持设备功率与允许相位不变，且不修改原记录）。

此外提供**稳健分相预演**接口：每台设备功率以正整数区间 `[min_power, max_power]`
给出（可在区间内独立变化），寻找一种使**任意功率组合**下各相均不超容量的分配，
并最小化最坏情形下三相最大负载与最小负载之差。预演**只返回结果、不写入历史记录**。

## 快速开始（一键启动）

前置条件：已安装 Docker 与 Docker Compose 插件。

```bash
docker compose up --build
```

依赖安装与数据库初始化（自动建表）均在容器启动时完成，无需任何额外步骤。

启动后访问：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000 | 服务根端口 |
| http://localhost:8000/docs | Swagger 交互式 API 文档 |
| http://localhost:8000/health | 健康检查 |

停止服务：`docker compose down`（加 `-v` 可同时清除数据卷）。

## 配置说明

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_PATH` | 容器内 `/data/app.db`；本地运行 `data/app.db` | SQLite 数据库文件路径，通过环境变量覆盖 |
| 端口映射 | `8000:8000` | 在 `docker-compose.yml` 的 `ports` 中调整宿主机端口 |

数据库文件保存在 Compose 命名卷 `app-data` 中，容器重建后历史计算记录不丢失。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/calculations` | 创建一次排布计算，返回最优分配、各相负载与差值 |
| `GET` | `/api/v1/calculations` | 查询历史计算记录摘要（支持 `limit` / `offset` 分页） |
| `GET` | `/api/v1/calculations/{id}` | 查询单条记录详情（含原始输入设备与分配结果） |
| `POST` | `/api/v1/calculations/{id}/capacity-upgrade` | 为「无可行分配」的记录给出最小扩容建议（原记录不变） |
| `POST` | `/api/v1/robust-preview` | 稳健分相预演：功率区间下的稳健最优分配（**不写入历史记录**） |
| `GET` | `/health` | 健康检查 |

### 请求示例

```bash
curl -X POST http://localhost:8000/api/v1/calculations \
  -H "Content-Type: application/json" \
  -d '{
    "capacities": {"A": 100, "B": 100, "C": 100},
    "devices": [
      {"id": "D1", "power": 10, "allowed_phases": ["A", "B", "C"]},
      {"id": "D2", "power": 20, "allowed_phases": ["A", "B", "C"]},
      {"id": "D3", "power": 30, "allowed_phases": ["B", "C"]}
    ]
  }'
```

### 响应示例（有可行方案）

```json
{
  "id": 1,
  "created_at": "2026-09-24T08:00:00.000000+00:00",
  "capacities": {"A": 100, "B": 100, "C": 100},
  "feasible": true,
  "imbalance": 20,
  "loads": {"A": 10, "B": 20, "C": 30},
  "message": "计算成功：已找到三相不平衡度最小的分配方案",
  "assignments": [
    {"device_id": "D1", "phase": "A"},
    {"device_id": "D2", "phase": "B"},
    {"device_id": "D3", "phase": "C"}
  ]
}
```

### 响应示例（无可行方案）

```json
{
  "id": 2,
  "created_at": "2026-09-24T08:01:00.000000+00:00",
  "capacities": {"A": 5, "B": 0, "C": 0},
  "feasible": false,
  "imbalance": null,
  "loads": null,
  "message": "无可行分配：不存在所有设备均接入且各相不超容量的方案",
  "assignments": []
}
```

## 容量扩容建议

对于返回 `feasible: false` 的计算记录，可调用扩容接口。服务**保持设备功率与允许
相位不变**，计算 A/B/C 各相需要增加的**非负整数容量**以及扩容后的设备分配，
使所有设备都能接入。优化目标依次为：

1. **三相扩容总量最小**（`total_upgrade`）；
2. 总量相同时，**单相最大扩容量最小**（`max_upgrade`）；
3. 仍相同时，取按设备编号排序的相位序列**字典序最小**的方案。

接口只返回建议，**原计算记录不做任何修改**。记录不存在返回 `404`；
原记录已有可行分配（无需扩容）返回 `409`。

### 请求示例

```bash
# 先创建一条无可行分配的记录
curl -X POST http://localhost:8000/api/v1/calculations \
  -H "Content-Type: application/json" \
  -d '{
    "capacities": {"A": 5, "B": 0, "C": 0},
    "devices": [
      {"id": "BIG", "power": 50, "allowed_phases": ["A", "B", "C"]},
      {"id": "FIX", "power": 7, "allowed_phases": ["A"]}
    ]
  }'

# 再对该记录请求扩容建议（假设记录 id 为 2）
curl -X POST http://localhost:8000/api/v1/calculations/2/capacity-upgrade
```

### 响应示例

```json
{
  "id": 2,
  "created_at": "2026-09-24T08:01:00.000000+00:00",
  "original_capacities": {"A": 5, "B": 0, "C": 0},
  "capacity_upgrades": {"A": 2, "B": 50, "C": 0},
  "suggested_capacities": {"A": 7, "B": 50, "C": 0},
  "total_upgrade": 52,
  "max_upgrade": 50,
  "assignments": [
    {"device_id": "BIG", "phase": "B"},
    {"device_id": "FIX", "phase": "A"}
  ],
  "loads": {"A": 7, "B": 50, "C": 0},
  "message": "扩容建议：已在最小化三相扩容总量、再最小化单相最大扩容量的目标下，找到使所有设备均接入的容量与分配方案"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `original_capacities` | 原各相容量 |
| `capacity_upgrades` | 各相需要增加的非负整数容量 |
| `suggested_capacities` | 建议容量 = 原容量 + 扩容量 |
| `total_upgrade` / `max_upgrade` | 三相扩容总量 / 单相最大扩容量 |
| `assignments` | 建议容量下的设备相位分配（按设备编号排序） |
| `loads` | 该分配下各相的实际负载（均不超过建议容量） |

### 输入校验规则

- 设备数量 **1 ~ 12** 台，设备编号**唯一**且非空；
- 功率为**正整数**；
- `allowed_phases` 为**非空**数组，元素取自 `A` / `B` / `C`（自动去重）；
- 各相容量为**非负整数**。

不满足规则时返回 `422` 及具体错误信息。

## 稳健分相预演

`POST /api/v1/robust-preview` 用于设备功率尚不确定的场景：每台设备的功率以
**正整数区间** `[min_power, max_power]` 给出（`min_power <= max_power`，二者相等
即固定功率），各设备功率可在区间内**独立变化**。

服务为每台设备选择一个允许相位，使得：

1. **稳健可行**：无论各设备功率在区间内如何取值，各相负载均不超过容量。
   固定一种相位分配后，某相负载的可能范围为
   `[该相设备 min_power 之和, 该相设备 max_power 之和]`，
   因此稳健可行等价于**各相负载上界不超过对应容量**；
2. **最小化最坏负载差**（`worst_imbalance`）：任意功率组合可能产生的
   「三相最大负载 − 三相最小负载」的最大值。取上界的相与取下界的相来自不同
   设备、互不冲突，故其值等于
   `max_p 负载上界_p − min_p 负载下界_p`；
3. 最坏负载差相同时，取按设备编号排序的相位序列**字典序最小**的方案。

返回分配、各相负载的**下界与上界**（`load_ranges`）以及最坏负载差。
不存在任何稳健可行分配时，返回 `feasible: false` 并明确说明无解。

本接口只做预演，**不写入历史计算记录**（不会出现在 `GET /api/v1/calculations`
列表中），可安全重复调用。

### 请求示例

```bash
curl -X POST http://localhost:8000/api/v1/robust-preview \
  -H "Content-Type: application/json" \
  -d '{
    "capacities": {"A": 100, "B": 100, "C": 100},
    "devices": [
      {"id": "D1", "min_power": 8,  "max_power": 12, "allowed_phases": ["A", "B", "C"]},
      {"id": "D2", "min_power": 8,  "max_power": 12, "allowed_phases": ["A", "B", "C"]},
      {"id": "D3", "min_power": 10, "max_power": 18, "allowed_phases": ["B", "C"]}
    ]
  }'
```

### 响应示例（有稳健可行方案）

```json
{
  "capacities": {"A": 100, "B": 100, "C": 100},
  "feasible": true,
  "worst_imbalance": 10,
  "load_ranges": {
    "A": {"lower": 8, "upper": 12},
    "B": {"lower": 8, "upper": 12},
    "C": {"lower": 10, "upper": 18}
  },
  "assignments": [
    {"device_id": "D1", "phase": "A"},
    {"device_id": "D2", "phase": "B"},
    {"device_id": "D3", "phase": "C"}
  ],
  "message": "稳健预演成功：已找到使各设备功率在区间内任意取值时各相均不超容量的分配，并最小化最坏情形下三相最大负载与最小负载之差"
}
```

上例中最坏负载差 `18 − 8 = 10`：D3 取上界 18 落在 C 相、D1/D2 取下界 8 落在
A/B 相时可同时出现。

### 响应示例（无稳健可行方案）

当某台设备在其所有允许相位上，只要功率取上界就必然超容（例如只能接 A、
`max_power=10` 而 A 相容量为 9）时，明确返回无解：

```json
{
  "capacities": {"A": 9, "B": 0, "C": 0},
  "feasible": false,
  "worst_imbalance": null,
  "load_ranges": null,
  "assignments": [],
  "message": "无稳健可行分配：不存在任何相位分配，能保证各设备功率在区间内任意取值时各相均不超容量"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `worst_imbalance` | 最坏负载差：任意功率组合下三相最大负载与最小负载之差的最大值 |
| `load_ranges` | 该分配下各相负载的下界（设备 `min_power` 之和）与上界（设备 `max_power` 之和） |
| `assignments` | 设备的稳健相位分配（按设备编号排序） |
| `feasible` | 是否存在稳健可行的分配；为 `false` 时上述字段为 `null` / `[]` |

### 稳健预演输入校验规则

沿用排布计算的设备编号、允许相位、最多 12 台设备及三相容量校验，区别仅在功率：

- `min_power`、`max_power` 均为**正整数**，且 **`min_power <= max_power`**；
- 设备数量 **1 ~ 12** 台、编号**唯一**非空；
- `allowed_phases` **非空**、元素取自 `A` / `B` / `C`（自动去重）；
- 各相容量为**非负整数**。

不满足规则时返回 `422` 及具体错误信息。

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload        # 服务地址 http://127.0.0.1:8000
```

运行测试：

```bash
pytest                               # 全部测试（求解器 + API）
python3 -m unittest tests.test_solver  # 仅求解器测试（无第三方依赖）
```

## 项目结构

```
├── app/
│   ├── main.py       # FastAPI 应用与路由
│   ├── schemas.py    # 请求 / 响应数据模型与校验
│   ├── solver.py     # 三相排布求解器（DFS + 下界剪枝，纯标准库）
│   └── db.py         # SQLite 持久化（启动时自动建表）
├── tests/            # 求解器单元测试（含随机对拍）与 API 集成测试
├── requirements.txt  # 运行依赖
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## 求解算法说明

按设备编号排序后做深度优先搜索，相位按 A→B→C 顺序展开，因此完整方案按
「设备编号排序后的相位序列」字典序依次产生；仅在负载差值**严格更小**时更新最优解，
同差值时自然保留字典序最小方案。配合容量约束剪枝与负载差值下界剪枝，
12 台设备的最坏情形亦可在秒级内完成求解。

**扩容建议**（`capacity-upgrade`）同样按设备编号排序、相位按 A→B→C 展开做 DFS，
分两遍搜索：第一遍求三相扩容总量的最小值，第二遍在总量固定最小的方案中以
`(单相最大扩容量, 相位序列)` 为键选出最优（即先最小化单相最大扩容，再取字典序
最小）。搜索中利用「只允许单一相位的设备造成的被迫超载 + 自由设备功率扣除各相
空闲容量」构造扩容总量下界进行剪枝，12 台设备的最坏情形亦可在数秒内完成。

**稳健分相预演**（`robust-preview`）仍按设备编号排序、相位按 A→B→C 做 DFS，
枚举出的完整方案天然按相位序列字典序排列，因而同最坏负载差时首个方案即字典序
最小者。每个节点维护各相负载的下界和（设备 `min_power` 之和）与上界和
（设备 `max_power` 之和）：上界和超容即不可行，叶子节点以
`max 上界 − min 下界` 评估最坏负载差。剪枝包含两类：一是只允许单一相位的
剩余设备造成的**被迫超载**剪枝；二是最坏负载差的**下界**剪枝，下界综合
「当前最大上界、被迫叠加、功率总和的均衡极限（ceil/floor）以及两相对偶界」
得到 `max` 相上界的下界，综合「各相可达下界和、总和均衡极限、两相对偶界」
得到 `min` 相下界的上界。搜索前再以一次 LPT 贪心可行解作为初始上界阈值加速
（仅借用其差值，不影响最优性与字典序规则），12 台设备可在数秒内完成。
