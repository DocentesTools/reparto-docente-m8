# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-08-29

`2.0.0` was prepared on 2026-08-25 and never published — `1.1.0` is still the
newest release on `origin`. The remediation batch below therefore rides this
same unreleased number rather than taking one of its own, so its breaking role
changes reach consumers inside the `1.1.0` → `2.0.0` step they already have to
take. It releases together with `@mano8/astro-reparto-m8@2.0.0`: a `1.x` client
cannot drive this contract, and a client below `2.0.0` does not gate the
administrator read floors this version introduces.

### Added

- **A teacher binds their own profile with a claim code** (remediation `W1.4`).
  Linking a profile to an account needed the head to know that account's user
  id, and the accounts directory belongs to `fa-auth-m8`, which restricts it to
  superusers by its own design — so naming a colleague was a superuser act and
  nothing here may widen it. The direction is reversed instead:
  `POST /teacher-profiles/{id}/claim-code` (`CurrentAdmin`, `201`) mints a
  single-use code and returns it **once**, and `POST /teacher-profiles/claim`
  takes that code and links the profile to the *caller's own* account, read
  from the token. The claim schema carries no `user_id`, so no payload can name
  another account; the code is ~98 bits from `secrets`, stored only as a
  SHA-256 digest, expiring (`CLAIM_CODE_TTL_HOURS`, default 72) and consumed by
  the redemption that succeeds; minting again replaces the outstanding code.
  The linkage reuses `link_user`, so the "one profile per account" `409` is the
  existing rule rather than a second copy of it, and a caller refused by it
  leaves the code redeemable for the teacher it was meant for. Both halves
  audit `teacher_profile.claim_code_issued` / `teacher_profile.claimed`, once
  per process the profile participates in.

  `POST /teacher-profiles/claim` is the service's only reader-floor mutation:
  its authorization is the credential, not the role, and requiring `WRITER`
  would leave a read-only participant permanently unable to reach their own
  view. `tests/test_authorization_boundaries.py` classifies it explicitly
  rather than by default, and asserts the absent `user_id`.

  **This changes the schema** — `teacher_profile` gains `claim_code_hash` and
  `claim_code_expires_at`, both nullable and on neither the public nor the
  request schemas. Per the Compose bootstrap policy the revision is produced
  from these models on a deployment's first start-up against a clean database.

- **`GET /assignment-processes/{id}/summary` reports balanced, pending and
  overloaded participant counts** (remediation `W1.6`).
  `balanced_participant_count`, `pending_participant_count` and
  `overloaded_participant_count` are read off the same per-participant `state`
  the department-head dashboard's assignment summary already computes
  (plan §6.2), never re-derived from raw hours, so the two views can never
  disagree. All three are nameless aggregate counts, keeping `/summary` safe
  for a projected shared screen (plan §8.7, `RBAC-07`). No contract-version
  bump: the fields are purely additive.
- Add the three-stage planning, requirements, assignment, feasibility, audit,
  versioning, import/export, and role-safe event-stream workflows.
- Publish the served API surface and the `reparto-docente-m8@2.0.0` contract
  metadata for client compatibility checks.

### Changed

- **The teacher read scope is reconciled with the SSE confidentiality tier**
  (remediation `W5.3`). Two rules governed one question and disagreed.
  `services/read_scope.py` grants a participant a department-wide read, while
  `services/sse.py` projects the same process down to a teacher tier that
  withholds every other participant's figures and withholds
  `extra_hours_reason` even on an event about the viewer themselves. The
  dashboard and the participant list sat at the reader floor and carried the
  *department-head* tier through the gap: per-participant target, assigned and
  remaining hours, the validation findings that now name the participant they
  are about (`W5.1`), and the head's written extra-hours justification.

  **Breaking for a `READER`/`WRITER` caller.** `GET
  /assignment-processes/{id}/dashboard`, `GET
  /assignment-processes/{id}/teachers/` and `GET
  /assignment-processes/{id}/teachers/{teacher_id}` now declare `CurrentAdmin`
  and answer `403` below that floor, joining the feasibility witness and
  diagnostics in `ADMIN_ONLY_READS`. No path was added, removed or re-verbed,
  so `docs/served-api-surface.json` is unchanged and the declared
  `reparto-docente-m8@2.0.0` compatibility range still holds.

  Nothing lost a source: the teacher tier already had `GET …/lan/me` (the
  caller's own participation row plus identifier-free aggregate balances) and
  the projected screen already had `GET …/summary` (nameless aggregate counts,
  `RBAC-07`). Both stay at the reader floor. Scope still resolves *before* the
  role, so an out-of-scope or non-existent process is still a `404` and the new
  `403` cannot confirm that a process exists (§21.4).

- **The department-head read tier holds after the fact as well** (remediation
  `W7.1`). `W5.3` narrowed the two *live* reads and said in the same breath
  that this did not make the reader surface teacher-tier-clean. It did not:
  seven reads still served the same tier to any participant of the department,
  and they are settled here as **one decision rather than seven**.

  **Breaking for a `READER`/`WRITER` caller.** These now declare `CurrentAdmin`
  and answer `403` below that floor, joining `ADMIN_ONLY_READS`:

  | Read | Why it is the head's tier |
  | --- | --- |
  | `GET …/assignments/validations` | Since `W5.1` every §6.3/§6.4 finding names the participant it is about and quotes their hours |
  | `GET …/teaching-plan/validations` | Its twin, and the same messages |
  | `GET …/audit-events/` | The extra-hours event is stored with `reason` — the one key the SSE teacher tier withholds even about the viewer themselves — beside that participant's base, extra and target weekly hours |
  | `GET …/versions` | A snapshot is a whole-process dump; `extra_hours_reason` is restored out of one |
  | `GET …/versions/{left}/compare/{right}` | Reads two snapshots |
  | `GET …/compare-previous-year` | The same `VersionComparison`, one side implied |
  | `GET …/exports` | The inventory of the artefacts built from all of it |

  The comparison-by-id route was not on the remediation's own list of six; it
  returns the identical payload as `compare-previous-year` from the same
  router, so leaving it behind would have been a hole rather than a decision.

  The alternative — projecting each payload down to the teacher tier the way
  `services/sse.py` does — was considered and rejected.
  `DEPARTMENT_HEAD_ONLY_PAYLOAD_KEYS` is a key filter over a flat event
  payload and none of these seven has that shape; a validation report projected
  to the teacher tier is its two counts, which `GET …/teaching-plan/summary`
  already serves at the reader floor and which stays there. Every one of the
  seven already hangs off a router declaring `require_visible_process`, which
  FastAPI resolves before the handler, so an out-of-scope or non-existent
  process is still `404` and the new `403` cannot confirm that a process exists
  (§21.4). `tests/test_read_scope.py` pins all seven for a `READER` *and* a
  `WRITER` — the floor is confidentiality, not the verb.

  No path was added, removed or re-verbed, so `docs/served-api-surface.json` is
  unchanged; the role change itself is what makes the pending release breaking.

- **A participant edit invalidates feasibility only when it moves a solver
  input** (remediation `W1.5`). `PATCH
  /assignment-processes/{id}/teachers/{teacher_id}` carries the selection order
  and the display metadata as well as the hour columns, and it dropped the
  stored evaluation for all of them — so recording an agreed selection order
  reset the plan to `NOT_EVALUATED` and pushed
  `teaching_plan.feasibility_invalidated` to every subscriber, an alarm in the
  middle of a meeting that nothing in the live assignment path was reacting to.
  It now compares the three fields a snapshot actually reads off a participant
  row — `status` and the `base_weekly_hours`/`extra_weekly_hours` pair behind
  the target — and by value, so re-sending a participant's current hours is a
  no-op too. The list is `PARTICIPANT_FEASIBILITY_INPUT_FIELDS`, read off
  `_snapshot_from_slots` rather than guessed, and
  `tests/test_feasibility_participant_fields.py` proves it by mutating every
  column of the row against the real fingerprint, so a column that starts
  feeding the solver fails the suite until it is named there. Adding a
  participant, removing one and the audited `/extra-hours` action move an input
  by construction and stay unconditional, as does undoing an assignment.

  No contract change: no endpoint, schema or status code moves, a client simply
  stops receiving invalidation frames it should never have been sent, and
  `reparto-docente-m8@2.0.0` is unchanged.
- **Every stored hour value is now a `NUMERIC(8, 2)` column** (plan §3.9,
  remediation `W6.2`). `HoursNumeric` was defined but unused; the eleven hour
  columns across `assignment_process`, `department_hour_allocation_revision`,
  `group_subject`, `hour_requirement`, `process_teacher`, `subject` and
  `teaching_activity` are on it, and their Python type is `Decimal` rather than
  `float`. `tests/test_hours_columns.py` enumerates them from the metadata, so a
  new hour column declared as anything else fails the suite.
- **Hour fields cross the API as canonical decimal strings in both directions.**
  Responses already promised `"2.50"`; requests now require it, because the
  entity schemas share `HoursDecimal` with the planning-import boundary, which
  has refused binary floats since it was written. A client sending a JSON number
  for an hour field receives `422` — `@mano8/astro-reparto-m8` has always sent
  the canonical string, so this is a tightening of the contract rather than a
  change to what the supported client does.

  **This changes the schema.** Per the Compose bootstrap policy the revision is
  produced from these models on a deployment's first start-up against a clean
  database, so it must land before that start-up.

- Adopt the `USER < READER < WRITER < ADMIN < SUPERADMIN` role tiers from
  `fa-auth-m8`, including a mounted `READER` floor and explicit route-level
  mutation roles.
- Route authentication exclusively through `fastapi-m8 >=4.4.0,<5.0.0` and
  align the development stack with `fa-auth-m8:2.0.3`.
- Upgrade the runtime to the current pinned Python 3.14 slim image and remove
  unused package-installation tooling from the final container image.

### Fixed

- Remove the last bare authenticated-user route boundary so newly added routes
  inherit the service-wide authorization floor by construction.
- Enforce deterministic feasibility, lifecycle, synchronization, migration,
  and assignment invariants with complete regression coverage.

### Breaking

- Replace the 1.x teaching-assignment payload and lifecycle with the three-stage
  2.0 domain contract. Consumers must use a compatible
  `astro-reparto-m8` 2.x client and service range `>=2.0.0 <3.0.0`.

[2.0.0]: https://github.com/DocentesTools/reparto-docente-m8/compare/v1.1.0...v2.0.0
