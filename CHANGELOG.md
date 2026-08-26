# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
