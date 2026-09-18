-- Explicit DESTRUCTIVE reset for a disposable local iot_sim_ops demo database.
-- Run only from the repository root and only after choosing the exact MySQL
-- server; this drops all data in the iot_sim_ops database on that server.
-- Normal initialization is db/schema.sql once, followed by repeat-safe seed.sql.
DROP DATABASE IF EXISTS iot_sim_ops;
SOURCE db/schema.sql
SOURCE db/seed.sql
