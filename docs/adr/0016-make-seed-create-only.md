# 0016 · `make seed` is create-only, reads passwords from the environment and reaches Mongo itself

- **Status:** accepted · 2026-11-11 · goal SEED-1
- **Context:** PRD §7.11 says `make seed` creates the org, an HR_ADMIN, sample managers and seed
  templates. The card adds that a second run changes nothing and that seed passwords come from the
  environment. The pieces already exist with their own rules: users through `MongoUserStore.add_user`
  (AUTH-1, ADR 0004), org keys through `keyring.create` (PII-1), the registry through
  `AgentRegistry.sync` (REG-1, which refuses to run before `orgs` has a row), templates through
  `TemplateLifecycle` (TPL-2: publish is HR_ADMIN's and audited). Mongo publishes no port (§7.11).
  Something had to decide what a re-run does with records that already exist, and how the command
  reaches the database.

## Decision

1. **What to seed is data: `config/seed.yaml`.** The org, its users (id, email, name, role and the
   name of the environment variable holding the password) and the template files to publish. The
   file can hold no password: unknown fields are refused, and `password_env` must be a
   `SEED_*_PASSWORD` name. Passwords must be 12+ characters and at most 72 bytes; only bcrypt hashes
   (cost 12, the API's) are stored.
2. **Create-only and idempotent.** Each step looks first and writes only what is missing, in order:
   `orgs` row → org keys (`keyring.verify`, else `create`) → users → registry sync → templates. An
   existing record is never changed: a user keeps its password and role, a template key that already
   has a version is left as it is (HR may have published newer ones). A run that stopped part-way is
   finished by the next one; an unpublished draft with the seed template's exact body is published
   rather than duplicated.
3. **Conflicts stop the seed** instead of being overwritten: an email registered to another user
   (ADR 0004), a login entry without its user record, org keys the master key cannot unwrap, or a
   seed publisher who is not an HR_ADMIN in the stored records.
4. **The seed's first HR_ADMIN is the actor** of the registry sync and of each publish, so the audit
   log shows who the platform was set up as.
5. **Reaching Mongo.** `python -m onb_api.seed` takes `MONGO_URI` and `MONGO_DATABASE` from the
   environment. `scripts/seed.sh`, which `make seed` runs, uses `MONGO_URI` when it is set (the VM,
   through its SSH tunnel). When it is empty it opens a short-lived loopback socat tunnel to this
   compose project's mongo, as the integration tests do, and seeds as the root user. The tunnel is
   labelled with the stack's network and removed on exit.

## Alternatives rejected

- **Upsert everything to match the file** (reset passwords, re-publish a changed template). A re-run
  would silently undo what HR changed in the UI and rotate passwords from a file edit.
- **Seed through the HTTP API.** There is no user-create route (ADR 0004's cross-org email decision
  is still open with Haneef), and the first HR_ADMIN cannot log in before it exists.
- **Publish a Mongo port for the seed.** Contradicts §7.11.
- **Run the registry CLI (`python -m onb_agents.registry sync`) as a subprocess.** The seed calls
  `AgentRegistry.sync` in-process with the same arguments: one connection and one report.

## Consequences

- To change a seeded user or template, use the product (or a later admin goal), not the seed file.
- Root credentials cross the local tunnel, as in the integration tests. The VM path should set
  `MONGO_URI` to a least-privilege user (DEP-1).
- The registry sync audits in every org in `orgs` as the seed's HR_ADMIN (REG-1's behaviour); with
  more than one org that user id appears in other orgs' logs. A `system` actor (REG-1 finding) fixes it.
- Creating users and the org is not audited: there is no actor before the first user exists. A
  `system` actor would let the seed audit them too.
