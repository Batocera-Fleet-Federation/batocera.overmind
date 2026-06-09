---
name: overmind-db-management
description: Use this when designing, reviewing, debugging, or modifying Overmind PostgreSQL database schemas, migrations, queries, indexes, pagination, relational modeling, Yoyo migrations, AWS RDS/Postgres configuration, or local Postgres container setup.
---

# Overmind Database Management Skill

## Goal

Ensure Overmind uses PostgreSQL in a lean, relational, performant, and maintainable way.

The database should be treated as the primary system of record for durable application state. Application code should avoid holding large data sets in memory, avoid unnecessary blob storage, and rely on efficient relational queries, indexes, constraints, and pagination.

## Project Context

Overmind uses PostgreSQL.

Local development should use a local PostgreSQL container.

Production is deployed to AWS, using PostgreSQL-compatible infrastructure such as AWS RDS or Aurora PostgreSQL.

Yoyo is used to track and apply database migrations. Any schema change, index change, constraint change, seed/reference-data change, or database structure update must be reflected in Yoyo migrations and applied through the migration process.

AWS credentials, when needed for local production troubleshooting or deployment inspection, are stored locally at:

```text
.github/.credentials
```

That credentials file is intentionally not checked into source control.

Do not add `.github/.credentials` to source control.

Do not print, expose, commit, or copy credentials from `.github/.credentials`.

## Core Database Principles

When working on Overmind database changes, follow these rules:

1. Favor relational schema design over blob storage.
2. Use foreign keys for relationships between entities.
3. Use indexes for common joins, filters, lookups, sorting, and pagination.
4. Use pagination for list endpoints and queries.
5. Avoid loading full tables or large result sets into application memory.
6. Avoid JSON/blob columns unless the data is truly unstructured, rarely queried, or externally sourced.
7. Prefer normalized tables when the application needs to filter, join, search, count, sort, or update individual fields.
8. Keep database work inside the database when appropriate instead of pulling large data into code.
9. Use Yoyo migrations for schema changes.
10. Make database changes backward-compatible when possible.

## Relational Modeling Rules

When reviewing or creating schema, check for:

- Primary keys on every table.
- Foreign keys for parent-child relationships.
- Unique constraints for natural uniqueness.
- Indexes on foreign key columns.
- Indexes on frequently filtered columns.
- Composite indexes for common multi-column filters.
- Explicit `created_at` and `updated_at` timestamps where useful.
- Soft-delete strategy only when needed.
- Proper cascading behavior for deletes and updates.
- Avoidance of duplicate state across tables.

Prefer this:

```sql
CREATE TABLE drones (
  id UUID PRIMARY KEY,
  owner_user_id UUID NOT NULL REFERENCES users(id),
  hostname TEXT NOT NULL,
  status TEXT NOT NULL,
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_drones_owner_user_id ON drones(owner_user_id);
CREATE INDEX idx_drones_status ON drones(status);
CREATE INDEX idx_drones_last_seen_at ON drones(last_seen_at);
```

Avoid this when fields need to be queried independently:

```sql
CREATE TABLE drones (
  id UUID PRIMARY KEY,
  data JSONB NOT NULL
);
```

JSONB is acceptable only when:

- the structure varies significantly,
- the fields are not commonly queried,
- the data is external metadata,
- or the JSONB column has proper GIN/expression indexes for query paths.

## Pagination Rules

All list-style APIs and database reads should use pagination.

Avoid:

```sql
SELECT * FROM roms;
```

Prefer:

```sql
SELECT *
FROM roms
WHERE drone_id = $1
ORDER BY name ASC, id ASC
LIMIT $2 OFFSET $3;
```

For large tables, prefer keyset pagination over deep offset pagination:

```sql
SELECT *
FROM roms
WHERE drone_id = $1
  AND (name, id) > ($2, $3)
ORDER BY name ASC, id ASC
LIMIT $4;
```

When adding pagination, return metadata such as:

```json
{
  "items": [],
  "limit": 100,
  "nextCursor": "...",
  "hasMore": true
}
```

Do not design endpoints that return all ROMs, all sync records, all audit records, or all drone data without pagination.

## Performance Rules

When reviewing query performance:

1. Check whether the query uses indexes.
2. Check whether joins are supported by foreign key indexes.
3. Avoid N+1 query patterns.
4. Avoid loading large tables into application code for filtering.
5. Avoid repeated JSON parsing in application code.
6. Use aggregate queries instead of counting in code.
7. Use `EXPLAIN` or `EXPLAIN ANALYZE` for slow queries.
8. Add indexes based on real access patterns.
9. Do not add excessive indexes that slow writes without clear read benefit.
10. Batch inserts and updates where appropriate.

Useful commands:

```bash
psql "$DATABASE_URL" -c "\dt"
psql "$DATABASE_URL" -c "\d+ table_name"
psql "$DATABASE_URL" -c "EXPLAIN ANALYZE SELECT ...;"
```

## Indexing Rules

Create indexes for:

- foreign key columns,
- lookup columns,
- frequently filtered columns,
- frequently sorted columns,
- pagination cursors,
- unique business identifiers,
- status fields used in dashboards or background jobs.

Examples:

```sql
CREATE INDEX idx_roms_drone_id ON roms(drone_id);
CREATE INDEX idx_roms_system_id ON roms(system_id);
CREATE INDEX idx_roms_drone_system_name ON roms(drone_id, system_id, name);
CREATE INDEX idx_sync_jobs_drone_status ON sync_jobs(drone_id, status);
CREATE INDEX idx_drone_connections_last_seen ON drone_connections(last_seen_at);
```

Use unique constraints when applicable:

```sql
CREATE UNIQUE INDEX idx_drones_owner_hostname_unique
ON drones(owner_user_id, hostname);
```

For keyset pagination:

```sql
CREATE INDEX idx_roms_drone_name_id
ON roms(drone_id, name, id);
```

## Blob and JSON Storage Rules

Avoid blob or JSON storage for data that needs to be:

- filtered,
- joined,
- counted,
- sorted,
- searched,
- updated field-by-field,
- used in permissions,
- used in sync comparisons,
- displayed in paginated UI pages.

Instead of storing this:

```json
{
  "roms": [
    {
      "name": "Example",
      "system": "nes",
      "path": "/userdata/roms/nes/example.zip",
      "size": 12345,
      "hash": "..."
    }
  ]
}
```

Prefer relational tables:

```sql
CREATE TABLE systems (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE roms (
  id UUID PRIMARY KEY,
  drone_id UUID NOT NULL REFERENCES drones(id),
  system_id UUID NOT NULL REFERENCES systems(id),
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  file_size BIGINT,
  md5_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (drone_id, path)
);

CREATE INDEX idx_roms_drone_id ON roms(drone_id);
CREATE INDEX idx_roms_system_id ON roms(system_id);
CREATE INDEX idx_roms_drone_system_name ON roms(drone_id, system_id, name);
```

## Yoyo Migration Rules

Yoyo is the migration source of truth for Overmind database structure.

Any database change must be represented in a Yoyo migration, including:

- creating tables,
- altering tables,
- dropping tables,
- adding columns,
- removing columns,
- renaming columns,
- changing column types,
- adding constraints,
- removing constraints,
- adding foreign keys,
- removing foreign keys,
- adding indexes,
- removing indexes,
- adding seed/reference data,
- moving data from blobs into relational tables,
- backfilling new relational tables or columns.

Do not make manual schema changes directly against local or production PostgreSQL unless explicitly directed for emergency troubleshooting. If a manual change is made, create a matching Yoyo migration immediately so the repository remains the source of truth.

Before creating a migration:

1. Inspect current schema.
2. Check existing Yoyo migrations.
3. Understand current data shape.
4. Confirm whether data backfill is required.
5. Confirm whether indexes should be created concurrently in production.
6. Ensure rollback strategy is safe where possible.

Typical migration file expectations:

```text
migrations/
  20260609_01_create_drone_tables.py
  20260609_02_add_rom_indexes.py
```

Example Yoyo migration pattern:

```python
from yoyo import step

steps = [
    step(
        """
        CREATE TABLE IF NOT EXISTS drones (
          id UUID PRIMARY KEY,
          owner_user_id UUID NOT NULL REFERENCES users(id),
          hostname TEXT NOT NULL,
          status TEXT NOT NULL,
          last_seen_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS idx_drones_owner_user_id
        ON drones(owner_user_id);

        CREATE INDEX IF NOT EXISTS idx_drones_status
        ON drones(status);
        """,
        """
        DROP INDEX IF EXISTS idx_drones_status;
        DROP INDEX IF EXISTS idx_drones_owner_user_id;
        DROP TABLE IF EXISTS drones;
        """
    )
]
```

For production-scale tables, prefer:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_table_column ON table_name(column_name);
```

Do not use `CREATE INDEX CONCURRENTLY` inside a transaction.

If Yoyo wraps migrations in transactions for the project, handle concurrent index creation carefully according to the project’s existing migration conventions.

Avoid destructive changes unless explicitly requested.

Risky changes include:

- dropping columns,
- dropping tables,
- rewriting large tables,
- changing primary keys,
- changing foreign key behavior,
- converting JSON blobs into relational tables without a backfill plan.

## Local PostgreSQL Container

Local development should use a PostgreSQL container.

When debugging locally, check:

```bash
docker ps
docker compose ps
docker logs postgres --tail=100
psql "$DATABASE_URL" -c "select version();"
```

If using Docker Compose, prefer service names like:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: overmind
      POSTGRES_USER: overmind
      POSTGRES_PASSWORD: overmind
    ports:
      - "5432:5432"
    volumes:
      - overmind_postgres_data:/var/lib/postgresql/data

volumes:
  overmind_postgres_data:
```

Local defaults should not be used for production secrets.

## AWS Production Rules

Production PostgreSQL runs in AWS.

When AWS inspection is needed, credentials may be available locally at:

```text
.github/.credentials
```

Rules:

1. Never commit `.github/.credentials`.
2. Never print credentials.
3. Never copy credentials into code, docs, logs, or prompts.
4. Source credentials only for local commands when necessary.
5. Prefer read-only inspection before making changes.
6. Do not modify production database resources without explicit approval.

Before production database changes, check:

- current RDS/Aurora instance or cluster,
- backups,
- deletion protection,
- maintenance window,
- pending modifications,
- security groups,
- parameter groups,
- storage utilization,
- CPU/memory pressure,
- connection count,
- slow queries if available.

Example pattern:

```bash
set -a
source .github/.credentials
set +a

aws sts get-caller-identity
aws rds describe-db-instances
```

Do not expose the output if it contains sensitive account details unless the user explicitly needs it.

## Query Review Checklist

When reviewing a query, verify:

- Does it use pagination?
- Does it avoid `SELECT *` when only specific columns are needed?
- Does it filter in SQL instead of in application code?
- Does it join on indexed foreign keys?
- Does it avoid N+1 patterns?
- Does it have a deterministic `ORDER BY` for pagination?
- Does it use keyset pagination for large/deep result sets?
- Does it avoid loading large datasets into memory?
- Does it use aggregates in SQL instead of code loops?
- Does it avoid unnecessary JSON extraction?

Bad:

```python
roms = db.query("SELECT * FROM roms")
filtered = [r for r in roms if r["drone_id"] == drone_id]
```

Good:

```sql
SELECT id, name, system_id, path, file_size, md5_hash
FROM roms
WHERE drone_id = $1
ORDER BY name ASC, id ASC
LIMIT $2;
```

## API and Application Rules

Application code should:

1. Request only the data needed.
2. Use pagination on list endpoints.
3. Avoid holding full ROM inventories in memory.
4. Avoid using dictionaries as durable state.
5. Avoid duplicating database state in process memory.
6. Push filtering, sorting, joining, and counting into PostgreSQL.
7. Use database transactions for multi-step writes.
8. Use connection pooling appropriately.
9. Avoid long-running synchronous requests for heavy database operations.
10. Return summaries/counts where full detail is unnecessary.

For large collections like ROMs, saves, configs, audit logs, sync history, or drone events, APIs should expose:

- paginated list endpoint,
- detail endpoint by ID,
- summary/count endpoint,
- filter parameters,
- deterministic sorting,
- cursor or page metadata.

## Common Overmind Data Areas

Consider relational modeling for:

- users,
- roles,
- invitations,
- drones,
- drone approvals,
- drone connection state,
- drone system info,
- ROM systems,
- ROM metadata,
- ROM hashes,
- sync jobs,
- sync job items,
- save files,
- config files,
- audit events,
- background jobs,
- API tokens or device tokens,
- permissions.

Example relationship direction:

```text
users
  -> drones
      -> drone_connections
      -> roms
      -> sync_jobs
          -> sync_job_items
```

## Expected Output Format

When completing database work, respond using this format:

```text
Root cause / objective:
...

Schema changes:
...

Yoyo migrations:
...

Indexes added or changed:
...

Query changes:
...

Pagination changes:
...

Blob-to-relational changes:
...

Local Postgres validation:
...

AWS/production considerations:
...

Risks:
...

Files changed:
...
```

## Safety Rules

Do not:

- commit credentials,
- expose secrets,
- remove production data without explicit approval,
- drop tables without explicit approval,
- run destructive migrations without explicit approval,
- make schema changes outside Yoyo migrations,
- load full production tables into local code,
- replace relational schema with unstructured blobs,
- remove foreign keys to “make it easier,”
- remove indexes without validating query impact.

## Default Bias

When unsure, choose the option that keeps:

- data relational,
- queries indexed,
- reads paginated,
- application memory low,
- schema explicit,
- Yoyo migrations current,
- migrations reversible,
- production changes safe.