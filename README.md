# Config Vault Local

A local versioned configuration registry with strict schema validation, environment generation checks, promotion, exact rollback and audit history. Built for Lord_Sumi's portfolio. This is configuration tooling, not an encrypted secrets manager.

## Test and use

Python 3.10+, standard library only. Run `python -m unittest discover -s tests -v` and `python demo.py`, or use Actions → Tests and demo → Run workflow. The temporary synthetic demonstration publishes development/production values, promotes a revision and rolls production back to its exact prior value.

Import Vault from config_vault. register(app,schema) establishes an immutable schema. publish(app,environment,value,expected_generation=...,actor=...,reason=...) creates a new application revision and advances an environment head. get returns the current data, checksum, revision metadata and environment generation. promote checks both source and target generations and reuses the exact source revision. rollback selects an existing revision for an existing environment while increasing its generation. history lists deployment actions in generation order.

## Schema contract

This is an explicit subset, not a full JSON Schema implementation. Supported types are object, array, string, integer, number and boolean. Supported keywords are properties, required, additionalProperties, items, enum, minimum, maximum, minLength and maxLength. Unknown keywords, invalid bounds and invalid schema shapes are rejected. Additional properties are rejected by default. Booleans are not integers; equality of enum values uses canonical JSON. Configuration is bounded to 64 KiB with nesting limits. Object keys must be strings and nonfinite values are rejected.

## Consistency

SQLite transactions combine generation checks, immutable revisions, head changes and audit entries. Two writers with the same expected generation cannot both win. Promotion checks that its source has not moved since inspection. Rollback preserves the selected revision's data and checksum exactly; it never rewinds the environment generation. Reads use a database snapshot. A revision hash detects accidental modification but is not an authentication signature against an attacker rewriting the database.

Twelve local tests passed on 31 October 2026: invalid schemas/configurations, concurrent generation conflicts, exact promotion/rollback, stale sources, missing targets, immutable schema registration, restart history, modified records, application/environment separation, nested arrays, strict value types and audit metadata validation. CI runs the same checks and demo.

## Limits

Local trusted administration only: there is no authentication server, remote API, encrypted secret storage, secret redaction/export masking, automatic application reload, dynamic schema migration or distributed coordination. Anyone with database access can read configurations. Actors/reasons are recorded labels, not verified identities. Registering a changed schema for an existing application is rejected; use a separate application namespace until an explicit migration system exists. Large-scale history retention and performance are unbenchmarked. Keep actual credentials out of configuration examples and repositories.

MIT license; see LICENSE.
