# iot-sim-ops

- `db/migrations`: V001/V002（初始化 schema 与演示数据）
- `db/iot_sim_ops_reset.sql`: 一键重置脚本（会 DROP & RECREATE）
- `api/mock-fastapi`: 最小 Mock 后端（FastAPI），`run.sh` 可启动，`.env.example` 填 DB 连接
- `postman`: 集合与环境（用于联调）
- `.github/workflows/ci.yml`: PR/Push 自动起 MySQL 8 执行迁移并抽查