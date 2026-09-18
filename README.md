# iot-sim-ops

最小可用（MVP）的物联卡运营演示系统。包含 **MySQL** 数据层、**FastAPI** Mock 服务、**前端静态页**（登录/控制台）、**Postman** 集合，以及 **systemd 常驻 + 日志归档** 运维脚本。

> 适合教学与联调：查询卡片 → 查询当月用量 → 订购（加包、幂等） → 修改卡状态（停/复机） → 业务日志落盘归档。该仓库是演示系统，不代表生产环境安全性、性能或可用性承诺。

---

## 目录结构

```
/iot-sim-ops
├── api/mock-fastapi/            # 后端（FastAPI + Uvicorn）
│   ├── app.py                   # 主应用
│   ├── requirements.txt
│   ├── run.sh                   # 本地前台启动脚本
│   └── web/                     # 静态前端
│       ├── login.html
│       └── index.html
├── db/
│   ├── migrations/              # 迁移脚本（V001, V002, V005-V009）
│   ├── schema.sql               # 空库初始化入口，顺序引用七个历史迁移
│   ├── seed.sql                 # 可重复的非覆盖 synthetic 用量种子
│   └── iot_sim_ops_reset.sql    # 显式销毁并重建开发演示库
├── postman/                     # Postman 集合与环境
├── scripts/
│   └── export_logs.sh           # journald → 文件日志导出 & 仅保留最近10份
├── ops/systemd/                 # systemd 单元文件（部署模板）
│   ├── iot-sim-ops-api.service
│   ├── iot-sim-ops-logdump.service
│   └── iot-sim-ops-logdump.timer
└── README.md
```

---

## 运行环境

* OS：Ubuntu 20.04+/22.04+
* Python：3.10+（已在 3.12 验证）
* MySQL：8.0+
* 必要工具：`git`、`python3-venv`、`systemd`

---

## 数据库初始化

先选择一台专用的 MySQL 演示服务，并从仓库根目录在空的 `iot_sim_ops` 库上执行。`schema.sql` 只是 MySQL 客户端 `SOURCE` 入口；表定义仅在 V001、V002、V005–V009 七个历史迁移中，未另建一套 DDL。V002/V006 含历史种子和覆盖式更新，因此完整初始化仅用于空演示库，不能当作无损升级流程。

**正常初始化（一次）**

```bash
mysql -h 127.0.0.1 -P 3306 -uroot -p < db/schema.sql
mysql -h 127.0.0.1 -P 3306 -uroot -p < db/seed.sql
```

以后需要补齐演示用量时，只重复运行 `db/seed.sql`；它使用 `INSERT IGNORE`，不会覆盖已有用量或订购后的套餐累计。旧 `bulk_seed_10k.sql` 依赖另一套 `offering/sim_id` 表结构，已移除。

**明确销毁并重建（仅可丢弃的开发演示库）**

```bash
mysql -h 127.0.0.1 -P 3306 -uroot -p < db/iot_sim_ops_reset.sql
```

`iot_sim_ops_reset.sql` 会在所选 MySQL 服务上 `DROP DATABASE iot_sim_ops`，然后复用上述初始化入口；不要对有需保留数据的库运行。历史迁移不修改，也不在这里声称存在通用 migration 账本或无损升级能力。

**验收**

```sql
-- 随机看 5 张卡是否有归属
SELECT s.iccid, u.username
FROM sim_card s LEFT JOIN user_account u ON s.owner_user_id=u.user_id
LIMIT 5;

-- 看当月用量/订单表是否在
SHOW TABLES LIKE 'sim_usage';
SHOW TABLES LIKE 'sim_purchase';
```

---

## 本地开发启动（前台）

```bash
cd api/mock-fastapi
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip -r requirements.txt

# 可选：提供 .env（数据库连接）
cat > .env <<'EOF'
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASS=你的密码
DB_NAME=iot_sim_ops
CORS_ORIGINS=*
CORS_ALLOW_CREDENTIALS=false
EOF

# 启动
./run.sh
# 浏览器访问:
# http://127.0.0.1:8000/web/login.html
```

### 凭据与 CORS 安全

* `.env` 和 `.env.systemd` 已被 Git 忽略；只提交不含真实密码的 `.env.example`。
* 如果任何真实口令曾进入 Git 提交，应立即在数据库或服务器端轮换。只删除当前文件不会清除历史提交，也不会使旧口令失效。
* `CORS_ORIGINS=*` 只适用于不携带浏览器凭据的本地演示。若设置 `CORS_ALLOW_CREDENTIALS=true`，必须把 `CORS_ORIGINS` 改为逗号分隔的明确来源。

### 最小自动化检查

```bash
cd /path/to/iot-sim-ops
python -m pip install \
  -r api/mock-fastapi/requirements.txt \
  -r api/mock-fastapi/requirements-dev.txt
python -m compileall -q api/mock-fastapi
python -m pytest -q tests/test_app.py
```

不连接数据库的快速检查：`python -m pytest -q tests/test_app.py`。完整回归必须在专用、可清理的 MySQL 8 演示库中运行，先执行 `db/schema.sql` 和 `db/seed.sql`，再显式设置 `IOT_TEST_DB_ISOLATED=1`、`IOT_TEST_DB_NAME=iot_sim_ops` 及可选的 `IOT_TEST_DB_HOST/PORT/USER/PASS`，运行 `python -m pytest -q tests/test_db_integration.py tests/test_browser_integration.py`。浏览器测试还需 `python -m playwright install chromium`（Linux CI 使用 `--with-deps`），由 pytest 启动本地 Uvicorn 并连接同一隔离库。DB 测试夹具会清理自己创建的 synthetic 卡、订单和 token；浏览器登录另生成的 token 不由该夹具自动删除，需随专用测试库清理或等待过期。CI 的 `db-migrate` job 必跑两套测试，包含 TestClient HTTP 流、实际唯一键竞态、事务回滚、二次 seed 不覆盖用量，以及浏览器中的提交后响应丢失与乱序响应。切勿把这些变量指向需保留数据的库。

---

## 生产/演示部署（systemd 常驻）

> 以下命令默认代码路径 `/home/<user>/iot-sim-ops`，请替换为你的实际用户名与路径。

1）**安装依赖 & 虚拟环境**

```bash
cd /home/<user>/iot-sim-ops/api/mock-fastapi
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip -r requirements.txt
[ -f .env.example ] && cp .env.example .env   # 可选
```

2）**安装 systemd 单元（从仓库模板复制）**

```bash
sudo cp /home/<user>/iot-sim-ops/ops/systemd/iot-sim-ops-api.service      /etc/systemd/system/
sudo cp /home/<user>/iot-sim-ops/ops/systemd/iot-sim-ops-logdump.service  /etc/systemd/system/
sudo cp /home/<user>/iot-sim-ops/ops/systemd/iot-sim-ops-logdump.timer    /etc/systemd/system/
sudo systemctl daemon-reload
```

> 如需修改端口/用户/路径，直接编辑 `/etc/systemd/system/*.service|*.timer` 对应字段：`User`、`WorkingDirectory`、`ExecStart`、`EnvironmentFile`。

3）**启动与自启动**

```bash
# 主服务（Uvicorn）
sudo systemctl enable --now iot-sim-ops-api
sudo systemctl status iot-sim-ops-api
journalctl -u iot-sim-ops-api -n 200 -f   # 实时日志（journald）

# 日志导出定时器（见下一节）
sudo systemctl enable --now iot-sim-ops-logdump.timer
systemctl list-timers | grep iot-sim-ops-logdump
```

---

## 日志与可观察性

### 1）运行日志（journald）

* 主服务日志默认写入 **journald**：
  `journalctl -u iot-sim-ops-api -n 200 -f`
* 应用内已集成**访问日志中间件**与**业务日志**（`biz_logger`），会输出：

  * `http method=... path=... status=... dur_ms=... ip=... rid=... transid=...`
  * `sims.status get/patch ...`、`sims.search ...`、`sims.usage ...`、`sims.purchase ok ...`

### 2）日志导出到文件（每10分钟一份，仅保留最新10份）

* 脚本：`scripts/export_logs.sh`
* 轮询单元：`ops/systemd/iot-sim-ops-logdump.service`（oneshot）
* 定时器：`ops/systemd/iot-sim-ops-logdump.timer`（每 10 分钟）

导出位置（可修改脚本中的 `ROOT`）：
`/home/<user>/iot-sim-ops/logs/app-YYYYmmdd_HHMM.log`

**手动导出最近 30 分钟（可立即验收）：**

```bash
DURATION="30 min ago" /home/<user>/iot-sim-ops/scripts/export_logs.sh
ls -l /home/<user>/iot-sim-ops/logs
tail -n +1 /home/<user>/iot-sim-ops/logs/app-*.log | grep -E 'auth\.login|sims\.(search|usage|purchase|status)|http method='
```

---

## 前端页面

* 登录页：`/web/login.html`

  * 登录成功后将 `token` 与 `loginUser` 存入 `localStorage`。
* 控制台：`/web/index.html`

  * 搜索卡片（ICCID）并展示基础信息；
  * 查询当月用量（`/usage?month=YYYY-MM`）；
  * 订购（加包）：点击“购买”时先在当前标签的 `sessionStorage` 保存一份按 `loginUser` 隔离的卡、月份、MB、产品、金额和 `X-TransId` 快照（不存 token），再调用 `/purchase`；双击不会并发提交。响应丢失、超时或服务端错误后，刷新页面会显示待确认记录，不会自动重发。点击“重试待确认购买”仍使用原卡、月份、参数和幂等键，即使表单已改变。先核查结果再重试；要发起另一笔购买，必须先得到原请求的明确成功或拒绝。
  * 成功回执立即显示订单号，再独立刷新订单和用量；后续 GET 失败不抹掉回执。切换卡/月及发起更新请求后，旧响应不能覆盖当前结果。明确的参数拒绝和容量不足会释放待确认记录；`X-TransId conflict`、401 与不确定错误保留记录供核查，其中 401 应重新登录同一账号。
  * 刷新状态（GET `/status`）/ 修改状态（PATCH `/status`）。

> **请求头**：`Authorization: Bearer <token>`；
> **订购幂等**：`X-TransId: <uuid>`。

---

## Postman 集合

* 导入 `postman/iot-sim-ops (MVP).postman_collection.json` 与对应环境（`postman/iot-sim-ops (local).postman_environment.json`）。
* 推荐调用顺序：`Login/Get Token → /sims/search → /sims/{iccid}/usage?month= → /sims/{iccid}/purchase → /sims/{iccid}/status (GET|PATCH)`。

---

## API 约定（MVP）

* 所有接口除登录外，均需 Header：`Authorization: Bearer <token>`
* 成功响应统一格式：

  ```json
  { "code": "0", "msg": "ok", "data": { ... }, "trace": {} }
  ```

### Auth

* `POST /auth/login`
  请求体：`{ "username": "...", "password": "..." }`
  响应：`{ "code":"0","data":{"token":"...","token_type":"Bearer","expires_in":3600} }`

### SIM 搜索

* `GET /sims/search?iccid=...`
  返回匹配的卡基础信息（含 owner、status 等）。

### 当月用量

* `GET /sims/{iccid}/usage?month=YYYY-MM`
  返回 `{ "used_mb": 200, "package_mb": 1024, ... }`

  > 若开启“购买后累加套餐上限”，加包会实时反映在 `package_mb`。

### 订购（加包）

* `POST /sims/{iccid}/purchase`
  Header：`X-TransId: <非空白且不超过 64 字符的 ID>`（省略时由服务生成）
  请求体示例：`{ "month": "2025-08", "package_mb": 500, "product_id": "pkg_500", "pay_amount_cent": 700 }`
  同一 ID 只在 `iccid/month/package_mb/product_id/pay_amount_cent` 全相等时返回原订单；不同请求返回通用 409，不附带旧订单。`product_id`/`pay_amount_cent` 可省略或设为 `null`，分别写入订单的 `product_id`/`price_cent`。`package_mb` 为 1–2147483647 的整数，金额为 `null` 或 0–2147483647；累计套餐超出 INT 上限时整单回滚。

* `GET /sims/{iccid}/purchases?month=YYYY-MM&limit=20&offset=0`
  返回该月订单列表，含 `product_id` 和 `price_cent`。购买、用量查询和列表过滤都要求真实有效的 `YYYY-MM` 月份。

### 卡状态

* `GET /sims/{iccid}/status` → `{ "status": "ACTIVE" | "SUSPENDED" }`
* `PATCH /sims/{iccid}/status`
  请求体：`{ "action": "SUSPEND" | "RESUME" }`

---

## 数据模型（核心表）

* `user_account`、`auth_token`（登录与鉴权）
* `sim_card`（ICCID、MSISDN、owner\_user\_id、status 等）
* `sim_usage`（iccid、month、used\_mb、package\_mb）

  > 可选逻辑：订购成功后，`package_mb += 本次加包`
* `sim_purchase`（order\_id、iccid、month、package\_mb、product\_id、price\_cent、status、transid、created\_at）

  > 全局 `uk_transid` 保持不变；重放须先通过当前用户的卡归属校验并且请求五元组相等。
* `sim_op_log`（操作流水：停/复机等）

---

## 常见问题（FAQ）

**Q1: `Access denied for user 'root'@'localhost' (using password: NO)`**
A: systemd 模式下未读取 `.env`。检查单元文件是否包含
`EnvironmentFile=/home/<user>/iot-sim-ops/api/mock-fastapi/.env`，写入 DB\_\* 配置后 `daemon-reload && restart`。

**Q2: 端口 8000 被占用**
A: `sudo lsof -i:8000 -nP` 查 PID，`sudo kill -9 <PID>`；或修改 `ExecStart` 端口。

**Q3: `Unit ... does not exist`**
A: 没把单元文件复制到 `/etc/systemd/system/` 或未 `daemon-reload`。按“生产部署”步骤重新执行。

**Q4: 手动导出日志时报权限**
A: 在 `iot-sim-ops-logdump.service` 的 `[Service]` 段增加：
`SupplementaryGroups=systemd-journal`，然后 `daemon-reload && restart timer`。

**Q5: 订购后“套餐/剩余”没有变化**
A: 确认是否启用了“购买后累加 `sim_usage.package_mb`”逻辑（`app.py` 内 `/purchase` 成功后累加），或前端下单后触发一次 `/usage` 刷新。

---

## 版本与里程碑

* **v0.1.0（MVP）**

  * SIM 搜索 / 当月用量 / 订购（幂等）/ 状态（GET|PATCH）
  * systemd 常驻：`iot-sim-ops-api.service`
  * 日志归档：`export_logs.sh` + `iot-sim-ops-logdump.timer`（10min 一份，仅保留 10 份）
  * 访问日志中间件 + 关键业务日志（status/search/usage/purchase）

---

## 许可

本项目使用仓库内 `LICENSE` 指定的开源许可（若未特别声明，默认 MIT）。

---

## 致谢

* FastAPI / Uvicorn / PyMySQL
* Postman

---

> 如果你在部署或联调中遇到问题，可参考“常见问题”一节或直接查看 journald：
> `journalctl -u iot-sim-ops-api -n 200 -f`；
> 文件导出在：`/home/<user>/iot-sim-ops/logs/`。
