# 0002 · Workers claim jobs across orgs through a narrow queue repository

- **Status:** accepted · 2026-11-10 · goal EVT-1
- **Context:** PRD §7.9 sends all database access through `TenantRepository`, which adds `org_id` to
  every filter and raises without a tenant context. A worker cannot work that way: it claims the
  next job before it knows the job's org, and a job without `org_id` must still be claimed so it can
  be dead-lettered (§7.9 Event bus). §7.9 also says every index on a tenant collection starts with
  `org_id`, but the claim needs an index that leads with `status`.

## Decision

1. **`jobs` and `dead_letters` are tenant collections with one extra, narrow path.**
   `onb_data.QUEUE_COLLECTIONS = {"jobs", "dead_letters"}`. Producers write jobs through
   `store.tenant("jobs")`, so `org_id` comes from the context. Workers use `store.queue(name)`, a
   `QueueRepository` with only: `claim` (one atomic `find_one_and_update`), `update_leased` /
   `delete_leased` (match `{_id, lease_token}`), and, on `dead_letters` only, `insert_one` (written
   as given) and a `find` that requires a limit. It has no unscoped update-many, delete-many or
   aggregate. A claim may filter on queue state only (`consumer`, `status`, `available_at`,
   `locked_until`, `attempts`, `max_attempts`, combined with `$or`/`$and`), so it cannot be aimed at
   one org or search payloads. Worker writes may change queue state only (`status`, lease fields,
   `available_at`, `last_error`, timestamps; `$inc` on `attempts`), never `org_id`, `consumer` or
   `payload`. Jobs cannot be inserted or searched through it.
2. **The org is set from the job, before any consumer code runs.** The worker validates `org_id`
   with the same rule as `TenantContext`, dead-letters the job if it is missing or invalid, and
   otherwise runs the handler inside `tenant_scope(job.org_id)`. Everything the handler does goes
   through tenant repositories as usual.
3. **Index exception.** On a queue collection a *non-unique* index may lead with another field
   (`status, consumer, available_at`). A *unique* index there must still start with `org_id`, so no
   uniqueness can be shared across orgs. All other tenant collections are unchanged.
4. **Transactions.** `DataStore.run_transaction(callback)` runs the callback in one transaction
   (`with_transaction`, so transient errors retry the whole callback). The session lives in a
   contextvar that every repository call reads; transactions do not nest.

## Consequences

- T-2's command listener must allow the claim (`findAndModify` on `jobs` without `org_id`) and the
  leased writes on `jobs`/`dead_letters`. It should allow them only for these commands, not for any
  query on the queue collections.
- The cross-tenant suite (T-3) covers consumers through `EventBus.consumers()`: each listed
  consumer needs a case showing that a job for org A cannot read org B.
- A future broker (Kafka, NATS) replaces `MongoEventBus` behind `EventBus`. The queue repository
  goes away with it.
