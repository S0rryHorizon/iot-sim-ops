# iot-sim-ops

最小可用（MVP）的物联卡运营演示系统。包含 **MySQL** 数据层、**FastAPI** Mock 服务、**前端静态页**（登录/控制台）、**Postman** 集合，以及 **systemd 常驻 + 日志归档** 运维脚本。

> 适合教学与联调：查询卡片 → 查询当月用量 → 订购（加包、幂等） → 修改卡状态（停/复机） → 业务日志落盘归档。

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
│   ├── migrations/              # 迁移脚本（V001, V002, V005~V007 ...）
│   ├── iot_sim_ops_reset.sql    # 一键重置（开发演示用，谨慎）
│   └── *.sql
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

> **警告**：`iot_sim_ops_reset.sql` 会重建库，开发环境使用即可。

**方式 A：一键重置**

```bash
mysql -uroot -p < db/iot_sim_ops_reset.sql
```

**方式 B：按迁移顺序执行**

```bash
mysql -uroot -p iot_sim_ops < db/migrations/V001__init_schema.sql
mysql -uroot -p iot_sim_ops < db/migrations/V002__seed_demo.sql
mysql -uroot -p iot_sim_ops < db/migrations/V005__user_and_ownership.sql
mysql -uroot -p iot_sim_ops < db/migrations/V006__seed_demo_users_and_assign_sims.sql
mysql -uroot -p iot_sim_ops < db/migrations/V007__add_sim_purchase_usage.sql
```

**验收**

```sql
-- 随机看 5 张卡是否有归属
SELECT s.iccid, u.username
FROM sim_card s LEFT JOIN users u ON s.owner_user_id=u.id
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
EOF

# 启动
./run.sh
# 浏览器访问:
# http://127.0.0.1:8000/web/login.html
```

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

  * 登录成功后将 `token` 存入 `localStorage`。
* 控制台：`/web/index.html`

  * 搜索卡片（ICCID）并展示基础信息；
  * 查询当月用量（`/usage?month=YYYY-MM`）；
  * 订购（加包）：点击“一键加包”，会自动生成 `X-TransId` 幂等 ID 并调用 `/purchase`；
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
  响应：`{ "code":"0","data":{"token":"...","user":{"user_id":1,"username":"..."}} }`

### SIM 搜索

* `GET /sims/search?iccid=...`
  返回匹配的卡基础信息（含 owner、status 等）。

### 当月用量

* `GET /sims/{iccid}/usage?month=YYYY-MM`
  返回 `{ "used_mb": 200, "package_mb": 1024, ... }`

  > 若开启“购买后累加套餐上限”，加包会实时反映在 `package_mb`。

### 订购（加包）

* `POST /sims/{iccid}/purchase`
  Header：`X-TransId: <uuid>`（幂等）
  请求体示例：`{ "package_mb": 500 }`（字段以实际实现为准）
  返回订单信息，并在幂等冲突时返回原订单。

* `GET /sims/{iccid}/purchases?month=YYYY-MM&limit=20&offset=0`
  返回该月订单列表。

### 卡状态

* `GET /sims/{iccid}/status` → `{ "status": "ACTIVE" | "SUSPENDED" }`
* `PATCH /sims/{iccid}/status`
  请求体：`{ "action": "SUSPEND" | "RESUME" }`

---

## 数据模型（核心表）

* `users`、`user_account`、`auth_token`（登录与鉴权）
* `sim_card`（ICCID、MSISDN、owner\_user\_id、status 等）
* `sim_usage`（iccid、month、used\_mb、package\_mb）

  > 可选逻辑：订购成功后，`package_mb += 本次加包`
* `sim_purchase`（order\_id、iccid、month、package\_mb、status、transid、created\_at）

  > 幂等：同一 `X-TransId` 重复请求返回同一订单
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
