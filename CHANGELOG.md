# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

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

## [2.0.0] - 2026-08-25

### Added

- Add the three-stage planning, requirements, assignment, feasibility, audit,
  versioning, import/export, and role-safe event-stream workflows.
- Publish the served API surface and the `reparto-docente-m8@2.0.0` contract
  metadata for client compatibility checks.

### Changed

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
