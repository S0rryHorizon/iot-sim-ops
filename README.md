# iot-sim-ops

最小可用的物联卡运营演示系统（MVP）。包含：
- **数据库迁移**（MySQL 8）：认证、物联卡、月用量、购买订单、Token（不透明，1h 过期）
- **Mock 后端**（FastAPI）：`/auth/token`、`/sims/{iccid}/usage`、`/sims/{iccid}/purchase`、`/sims/{iccid}/status`
- **Postman 集合与环境**：一键联调
- **CI（GitHub Actions）**：起 MySQL 8，执行迁移（V001/V002）并做抽查

---

## 目录结构

```
db/
  ├─ migrations/
  │   ├─ V001__init_schema.sql       # 初始化表 & 视图（非破坏）
  │   └─ V002__seed_demo.sql         # 演示数据 & 1h token（非破坏）
  └─ iot_sim_ops_reset.sql           # 本地“一键重置”脚本（会 DROP）
api/
  └─ mock-fastapi/
      ├─ app.py
      ├─ requirements.txt
      ├─ .env.example                # 示例环境变量（可提交）
      └─ run.sh                      # 本地一键启动
postman/
  ├─ postman_iot_sim_ops_collection.json
  └─ postman_iot_sim_ops_env.json
.github/
  └─ workflows/ci.yml                # 迁移 & 抽查
```

---

## 快速开始（本地/虚机）

> 数据库名：`iot_sim_ops`；默认演示账号：`appid=demo-app`、`password=demo-password`

### 1) 数据库初始化

**方式 A：标准迁移（推荐，非破坏）**
```sql
-- 在 DBeaver / mysql client 中顺序执行：
SOURCE db/migrations/V001__init_schema.sql;
SOURCE db/migrations/V002__seed_demo.sql;
```

**方式 B：一键重置（本地清库用，会 DROP）**
```sql
SOURCE db/iot_sim_ops_reset.sql;
```

脚本末尾会输出一条 `issued_demo_token`（1 小时有效），可直接用于 API 鉴权。

### 2) 启动 Mock 后端

> 在 Ubuntu 虚机（例：`192.168.237.130`）上操作

```bash
cd ~/iot-sim-ops/api/mock-fastapi
cp .env.example .env          # 按实际 MySQL 填 DB_*（DB_NAME=iot_sim_ops）
./run.sh                      # 首次会创建 venv 并安装依赖，随后启动 uvicorn:8000
# 若端口占用，可用：
# uvicorn app:app --host 0.0.0.0 --port 8010
```

自测：
```bash
curl http://127.0.0.1:8000/alive
# 期望: {"ok": true, "service": "iot-sim-ops", "version": "0.1.0"}
```

（可选）做成用户级 systemd 后台服务：
```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/iot-sim-ops-api.service <<'EOF'
[Unit]
Description=iot-sim-ops FastAPI (user)
[Service]
WorkingDirectory=/home/<USER>/iot-sim-ops/api/mock-fastapi
EnvironmentFile=/home/<USER>/iot-sim-ops/api/mock-fastapi/.env
ExecStart=/home/<USER>/iot-sim-ops/api/mock-fastapi/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Restart=on-failure
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now iot-sim-ops-api.service
```

### 3) Postman 联调

1. Import：`postman/postman_iot_sim_ops_collection.json` 与 `postman/postman_iot_sim_ops_env.json`  
2. 将环境变量 `baseUrl` 改为你的服务地址：`http://192.168.237.130:8000`（或 8010）  
3. 按顺序调用：
   - **Auth: Get Token**（自动写入 `{{token}}`）
   - **Usage: Get Monthly Usage**
   - **SIM: Get Status**
   - **SIM: Change Status**
   - **Purchase: Add-on Package**（预请求会生成 `{{transid}}`；重复 transid 幂等返回同一单）

---

## 何时用迁移 / 何时用重置？

| 场景 | 使用 |
| --- | --- |
| 日常开发、PR、CI | **V001 → V002 → …**（非破坏、可审计） |
| 全新环境首次初始化 | **V001 → V002** |
| 本地想“一把梭清库重来” | **`db/iot_sim_ops_reset.sql`**（破坏式，仅本地/测试） |

> 未来有新变更：新增 `db/migrations/V003__xxx.sql`，走 PR & CI；本地只需执行新增的 `V003`。

---

## CI

- 路径：`.github/workflows/ci.yml`  
- 流程：拉起 MySQL 8 → 执行 `V001__init_schema.sql`、`V002__seed_demo.sql` → 抽查视图/表  
- 合并主分支前要求 CI 通过（建议在 GitHub 设置里对 `main` 开启分支保护）

---

## API 说明（MVP）

- `POST /auth/token`：使用 `appid/password` 获取不透明 `token`（有效期 3600s），落库到 `auth_token`  
- `GET /sims/{iccid}/usage?month=YYYY-MM`：从 `v_usage_effective` 读取“基础套餐 + 成功加包 − 已用”  
- `POST /sims/{iccid}/purchase`：请求体含 `month`、`package_mb`；Header 需 `X-TransId`（幂等键）  
- `GET /sims/{iccid}/status`、`PATCH /sims/{iccid}/status`：ACTIVE ↔ SUSPENDED（DEACTIVATED 不可变更）

**鉴权**：全部业务接口需 Header `Authorization: Bearer <token>`；服务端校验 `auth_token.expires_at > NOW()`。

---

## 环境变量（`.env`）

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASS=your_mysql_password
DB_NAME=iot_sim_ops
```

> 提醒：`.env` **不要提交**；仓库内提供 `.env.example` 作为模板。

---

## 常见问题排查

- **端口占用**：  
  `sudo ss -ltnp | grep :8000` → 若被旧服务占用，停服务或改端口：`uvicorn ... --port 8010`
- **Token 过期（401）**：  
  1 小时有效，重新调用 `/auth/token` 或执行 `V002` 生成新 token
- **购买 500**：  
  使用仓库当前的 `app.py`（在 Python 中生成 `order_id`，并统一返回 JSON）；日志在前台 uvicorn 控制台
- **视图不更新**：  
  `v_usage_effective` 是汇总视图，只统计 `purchase_order.status='SUCCESS'` 的加包

---

## 维护与运维

**清理过期 token（可 cron）**
```sql
DELETE FROM iot_sim_ops.auth_token WHERE expires_at < NOW();
```

**查看今日订单**
```sql
SELECT * FROM iot_sim_ops.purchase_order
WHERE DATE(created_at)=CURDATE()
ORDER BY created_at DESC;
```

（可选）每天 3:00 清理：
```cron
0 3 * * * mysql -uroot -p'***' -e "DELETE FROM iot_sim_ops.auth_token WHERE expires_at < NOW();"
```

---

## 贡献与变更流程

1. 新建分支（如 `feat/db-xxx`）  
2. 新增迁移文件 `db/migrations/V00N__xxx.sql`（**不要修改历史 V001/V002**）  
3. 提 PR；等待 CI 通过 → 审核合并  
4. 本地执行新增的 `V00N` 即可同步状态

---

## 许可证

本仓库仅用于学习与演示，未附带商业授权条款。
