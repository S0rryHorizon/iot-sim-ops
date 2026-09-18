-- MySQL client entry point for an EMPTY iot_sim_ops demo database.
-- Run from the repository root: mysql -h HOST -P PORT -u USER -p < db/schema.sql
-- The seven historical migrations below are the only schema definitions.
-- V002 and V006 are historical seed migrations that may overwrite their demo
-- rows when rerun; do not use this entry point as an upgrade or reseed command.
SOURCE db/migrations/V001__init_schema.sql
SOURCE db/migrations/V002__seed_demo.sql
SOURCE db/migrations/V005__user_and_ownership.sql
SOURCE db/migrations/V006__seed_demo_users_and_assign_sims.sql
SOURCE db/migrations/V007__add_sim_purchase_usage.sql
SOURCE db/migrations/V008__add_imsi_to_sim_card.sql
SOURCE db/migrations/V009__purchase_price_and_product.sql
