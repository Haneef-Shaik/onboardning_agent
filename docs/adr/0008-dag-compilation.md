# 0008 · How a template version compiles into a run's DAG

- **Status:** accepted · 2026-11-11 · goal DAG-1
- **Context:** PRD §5.1 says the orchestrator compiles a published template version plus the hire's
  variables into a DAG, pruning tasks whose conditions do not match. FR-R3 says a task becomes
  `ready` only when every dependency is `done` or `skipped`. §6.1 gives `sla: { due: "start_date - 7d" }`;
  the eval fixture also uses `sla_hours: 48` (TPL-1 accepts both). The PRD does not say what a pruned
  task looks like, how a run proves it got the same graph, or what time zone an SLA date is in. RUN-1
  (the run and its LangGraph graph) and the HIL/SLA goals build on the answers.

## Decision

1. **Pruned tasks stay in the DAG as `skipped`, with a reason.** Every template task appears in the
   compiled DAG, in template order and with its `depends_on` edges unchanged. A task whose `when` is
   false starts as `skipped`; every other task starts as `pending`. The reason names the condition
   (`when condition \`role == 'engineer'\` is false for this hire`), never the hire's values. Keeping
   the node lets HR see why a step did not run and keeps the graph's shape equal to the template's.
2. **A skipped task never blocks.** `CompiledDag.ready(done)` treats skipped tasks as finished. A
   dependent's own `when` decides whether it runs; skipping does not cascade.
3. **The compiler is pure and fails closed.** No I/O, clock or randomness; neither argument is changed;
   compiled tasks hold deep copies of the template tasks. It refuses (`CompileError`, with a
   `CompileReason`): a template that is not a published version, an unknown dependency, a cycle, a
   variable that is missing when a condition or SLA reads it, a variable of the wrong type (enum outside
   its values, a date not in `YYYY-MM-DD` form, a bool as a number, NaN), a condition that cannot be
   parsed or decided, an SLA on a non-date variable or outside the calendar, and inputs JSON cannot
   carry. An undecidable condition is never read as false. Variables the template does not declare are
   ignored: a hire record holds more than the template reads.
4. **The DAG hash.** `sha256:` plus the SHA-256 of the canonical JSON of the compiled DAG (template key,
   version, topological order, and every task's template fields, status, skip reason and due date),
   with sorted keys, no spaces, ASCII only and no NaN. The same template version and the same values of
   the variables it reads give the same hash in any process. The hash covers the compiled result, not
   the raw variables, so two hires who compile to the same graph share a hash.
5. **SLA dates.** `sla.due` is resolved at compile time. A day offset gives an ISO date
   (`start_date - 7d` → `2026-11-24`). An hour offset gives an ISO datetime counted from 00:00 UTC of
   the date (`start_date + 36h` → `2026-12-02T12:00:00+00:00`), because a hire's date variable has no
   time zone. `sla_hours` is relative to when the task starts, so it stays on the task for the runtime.

## Consequences

- RUN-1 writes one `run_tasks` row per compiled task, including skipped ones, and stores the DAG hash
  on the run so the pinned version can be checked later.
- If orgs get a time zone, hour offsets should count from the org's midnight; that changes the hash
  of DAGs with hour offsets, which is acceptable because a run stores the hash it was compiled with.
- `TemplateTask.inputs` is a plain dict: a compiled task's copy can be changed by a caller after the
  hash is computed. Consumers must treat compiled tasks as read-only.
