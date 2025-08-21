
# iot_sim_ops (Simplified)

This repo tracks database schema, docs, and Postman assets for a minimal OneLink-like demo:

- Auth with opaque tokens (1h TTL, stored in `auth_token`)
- Basic operations: usage query, purchase, SIM status query/change

## Structure
```
db/migrations
  V001__init_schema.sql
  V002__seed_demo.sql
docs/
postman/
.github/workflows/ci.yml
```

## Local usage
1. Run `V001__init_schema.sql`, then `V002__seed_demo.sql` in MySQL.
2. Copy the `issued_demo_token` from the final SELECT for API calls.
