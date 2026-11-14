# iot-sim-ops

物联卡运营的演示后端：登录后查卡、看当月用量、订购流量加包、停机与复机。FastAPI + MySQL，带一个静态控制台页面。

这是实习期间做的教学与联调项目，不是生产系统。值得看的部分是订购接口：同一个请求号重复提交只生成一笔订单，订单与套餐额度在同一个事务里写入，响应丢失后可以用原请求号安全重试。

## 目录

```
api/
  app.py          应用入口、访问日志、查卡 / 状态 / 用量接口
  auth.py         登录、token 鉴权、卡归属校验
  purchases.py    订购事务与订单列表
  db.py           MySQL 连接与连接池
  schemas.py      请求模型、月份校验、响应信封
  web/            登录页与控制台（原生 HTML / CSS / JS）
db/
  migrations/     历史迁移（表结构的唯一定义）
  schema.sql      空库初始化入口
  seed.sql        可重复执行的演示用量
  iot_sim_ops_reset.sql   销毁并重建演示库
deploy/           systemd 单元与日志导出脚本
postman/          可顺序执行的接口集合
tests/            单元测试、真实 MySQL 测试、浏览器测试
```

## 快速开始

需要 Python 3.10+（在 3.12 上验证）和 MySQL 8。以下命令都在仓库根目录执行。

```bash
# 1. 初始化一个空的演示库
mysql -h 127.0.0.1 -uroot -p < db/schema.sql
mysql -h 127.0.0.1 -uroot -p < db/seed.sql

# 2. 安装依赖并配置数据库连接
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp api/.env.example api/.env        # 填入 DB_PASS 等

# 3. 启动
api/run.sh
```

打开 <http://127.0.0.1:8000/web/login.html>，接口文档在 `/docs`。

| 演示账号 | 密码 | 名下的卡 |
|---|---|---|
| alice | user1pass | `8986001200000000001`、`8986001200000000002` |
| bob | user2pass | 其余演示卡 |

演示用量只有 `2025-08` 这一个月。控制台的月份默认是当前月，查用量前先改成 `2025-08`，或者先买一个加包，当月的用量记录会随之创建。

> `db/schema.sql` 只用于空库：其中两个历史迁移带覆盖式的演示数据，重复执行会改写已有行。之后补演示用量只需重跑 `db/seed.sql`。`db/iot_sim_ops_reset.sql` 会 `DROP DATABASE iot_sim_ops`，只能对可丢弃的库使用。

## 接口

除登录、`/alive` 和静态页面外都需要 `Authorization: Bearer <token>`。成功响应统一为：

```json
{ "code": "0", "msg": "ok", "data": {}, "trace": {} }
```

业务错误返回对应的 HTTP 状态码和 `{"detail": "..."}`；请求参数不合法时是 FastAPI 默认的 422，`detail` 为错误列表。鉴权先于参数校验。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 用户名密码换取一小时有效的 token |
| GET | `/sims/search` | 按 `iccid` / `imsi` / `msisdn` 之一查自己名下的卡 |
| GET | `/sims/{iccid}/status` | 卡状态 |
| PATCH | `/sims/{iccid}/status` | `{"action": "SUSPEND" \| "RESUME"}` |
| GET | `/sims/{iccid}/usage?month=YYYY-MM` | 当月已用与套餐额度 |
| POST | `/sims/{iccid}/purchase` | 订购加包 |
| GET | `/sims/{iccid}/purchases` | 订单列表，可按 `month` 过滤，`limit` / `offset` 分页 |
| GET | `/alive` | 存活检查 |

访问别人的卡返回 403；搜索只在自己名下查找，查不到一律 404，不暴露别人的卡是否存在。

### 订购与幂等

```http
POST /sims/8986001200000000001/purchase
X-TransId: 7c1e0d1c-5a4e-4a43-9d0b-3f0f4f2f9b11

{ "month": "2025-08", "package_mb": 1024, "product_id": "pkg_1024m", "pay_amount_cent": 1024 }
```

- `X-TransId` 是调用方生成的请求号（非空、不超过 64 字符）。省略时服务端自动生成并在 `trace.transid` 返回，但这样的请求丢了响应就无法安全重试。
- 同一请求号再次提交：卡、月份、流量、产品、金额五项全部相同则返回原订单；任何一项不同返回 `409 X-TransId conflict`，不泄露原订单内容。归属校验在这之前：对别人的卡仍然是 403。
- 订单和套餐额度在同一事务内写入。累计额度超过 MySQL `INT` 上限时返回 `409 package_mb capacity exceeded`，整单回滚。
- 两个请求并发使用同一请求号时，由数据库唯一键决出先后，后到者按重放或冲突处理。
- 遇到数据库死锁或锁等待超时返回 `503 DB_BUSY`，用原请求号重试即可。
- 提交成功但响应没送达是无法消除的情况，所以重试必须带原请求号。控制台页面就是这样做的：点击购买时先把请求快照存进当前标签页的 `sessionStorage`，结果不明确时保留快照，只允许用原参数重试。

### 停机与复机

状态变更和操作流水（`sim_op_log`）在同一事务内写入，流水写不进去则状态也不变。重复执行同一动作返回 200，流水记为 `NOOP`。已销户（`DEACTIVATED`）的卡不能再停复机，返回 `409 sim deactivated`。

## 配置

`api/.env`（不进版本库，模板见 `api/.env.example`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DB_HOST` `DB_PORT` `DB_USER` `DB_PASS` `DB_NAME` | `127.0.0.1` `3306` `root` 空 `iot_sim_ops` | MySQL 连接 |
| `DB_SOCKET` | 空 | 设置后改走 Unix socket |
| `DB_POOL_SIZE` / `DB_POOL_OVERFLOW` | 10 / 10 | 每个进程的连接池容量与临时上限，连接按需创建 |
| `DB_POOL_TIMEOUT` | 5 | 等待空闲连接的秒数，超时返回 `503 DB_BUSY` |
| `DB_POOL_ENABLED` | `true` | 设为 `false` 则每次请求新建连接，用于排查 |
| `CORS_ORIGINS` | `*` | 逗号分隔的来源 |
| `CORS_ALLOW_CREDENTIALS` | `false` | 设为 `true` 时必须给出明确来源，否则拒绝启动 |

连接归还连接池时会回滚未提交的事务，取出时检查是否断连。多进程部署时把各进程的上限相加，并给 MySQL 留出管理连接。

监听地址不在 `.env` 里：`api/run.sh` 读取启动时的环境变量，例如 `API_HOST=127.0.0.1 API_PORT=8001 api/run.sh`；systemd 部署时改单元文件里的 `ExecStart`。

## 测试

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests/test_app.py tests/test_connection_pool.py   # 不需要数据库
.venv/bin/python -m ruff check api tests
```

真实 MySQL 测试和浏览器测试需要一个可丢弃的 MySQL 实例，按“快速开始”初始化好 `iot_sim_ops` 库：

```bash
export IOT_TEST_DB_ISOLATED=1 IOT_TEST_DB_NAME=iot_sim_ops    # 可选 IOT_TEST_DB_HOST/PORT/USER/PASS
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q tests/test_db_integration.py tests/test_browser_integration.py
```

它们覆盖：经真实连接池的完整 HTTP 流程、同一请求号的唯一键竞态、首次加包的并发、容量溢出与写入失败的回滚、状态与流水的原子性，以及浏览器里提交后响应丢失、乱序响应不覆盖当前视图。GitHub Actions 在 PR 以及推送到 `main` / `dev` 时用 MySQL 8 服务跑全部三组测试。

**不要把这些变量指向需要保留数据的库。** 测试会清理自己创建的卡、订单和 token；浏览器登录产生的 token 随测试库一起丢弃或自然过期。

## 部署

`deploy/systemd/` 里是系统级服务模板，默认代码在 `/opt/iot-sim-ops`、以 `iot` 用户运行，按实际情况修改后安装：

```bash
sudo cp deploy/systemd/iot-sim-ops-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iot-sim-ops-api
journalctl -u iot-sim-ops-api -f
```

正常返回的请求会输出一行访问日志（方法、路径、状态码、耗时、`X-Request-Id`、`X-TransId`），登录和停复机另有业务日志。

可选的日志归档：`iot-sim-ops-logdump.service` 和 `.timer` 每 10 分钟把 journald 日志导出到 `logs/`，只保留最近 10 份。手动导出：

```bash
DURATION="30 min ago" deploy/export_logs.sh
```

运行归档服务的用户需要在 `systemd-journal` 组里。

## Postman

导入 `postman/collection.json` 和 `postman/local.environment.json`，按顺序运行即可走完整个流程：登录（自动保存 token）、查卡、用量、新订购、同一请求号重放（断言是同一订单）、同一请求号换参数（断言 409）、订单列表、停机、复机。

## 数据模型

当前接口使用的表：

- `user_account`、`auth_token`：登录与鉴权
- `sim_card`：卡、归属用户、状态
- `sim_usage`：每张卡每月的已用流量与套餐额度
- `sim_purchase`：订单，`transid` 全局唯一
- `sim_op_log`：停复机操作流水

`app_credential`、`usage_monthly`、`purchase_order` 和视图 `v_usage_effective` 来自最早的 appid 鉴权版本，现在的接口不再读写它们；`auth_token.appid` 列同理，登录时写入 `NULL`。它们留在历史迁移里，没有做删除迁移。

## 许可

MIT，见 [LICENSE](LICENSE)。
