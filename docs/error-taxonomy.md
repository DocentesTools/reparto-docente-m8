# HTTP error taxonomy

Reparto-owned HTTP failures use one stable FastAPI detail envelope:

```json
{
  "detail": {
    "code": "teaching_plans.no_teaching_plan_process",
    "message": "No teaching plan for process 11111111-1111-1111-1111-111111111111.",
    "params": {
      "process_id": "11111111-1111-1111-1111-111111111111"
    }
  }
}
```

`code` and the parameter names are language-neutral contract. Codes are
additive-only after publication: do not rename, reuse, or remove an existing
code. `message` remains the current English compatibility text until the later
i18n boundary is introduced. `params` contains every value interpolated into
that message and is always present, including as `{}` for a literal message.
The complete machine-readable catalog is
[`error-taxonomy.json`](error-taxonomy.json); its source locations make a code
change reviewable and its regression test rejects drift.

## Ownership matrix

| Failure source | Current owner | Classification | Wire behavior |
|---|---|---|---|
| Reparto controller or domain service | `reparto-docente-m8` | Stable domain `code` | `DomainHTTPException` emits exact `{code,message,params}` under `detail`. |
| FastAPI request/path/query/body validation | FastAPI | HTTP status (`422`) | Framework validation-array detail remains unchanged and is classified by status. |
| Authentication, revocation, role floor, API-key and trusted-host failures supplied by `fastapi-m8` | `fastapi-m8` | HTTP status (`401`, `403`, `429`, or `503`) | Remains framework-owned until the separately authorized `fastapi-m8` promotion; Reparto does not rewrap it. |
| Router/method failures | FastAPI / Starlette | HTTP status (`404` or `405`) | Framework-owned and classified by status. |
| Unexpected exceptions | FastAPI / Starlette | HTTP status (`500`) | Keep the framework's generic production response; never expose exception text or synthesize a domain code. |

Validation findings (`PlanValidationMessage`) are a different contract. Their
codes and params are not HTTP error codes and are not registered here.

## Bounded families

The C7 migration is partitioned so later review and maintenance stay bounded:

1. shared controller/service gates;
2. reference data and identity (`academic_years`, `schools`, `departments`,
   `subjects`, `classroom_stages`, `teaching_groups`, `teacher_profiles`);
3. process lifecycle and history (`assignment_processes`, `process_teachers`,
   `teaching_plans`, `process_versions`, `history`, `planning_exchange`);
4. planning composition (`group_subjects`, `teaching_activities`,
   `hour_requirements`);
5. meeting assignment and feasibility (`meeting_sessions`, `selection_turns`,
   `assignments`, dashboard, SSE, snapshots and feasibility services).

Adding a new Reparto-owned error requires a new code and catalog entry. A
framework-owned error must not be copied into this catalog merely to translate
or disguise its message.
