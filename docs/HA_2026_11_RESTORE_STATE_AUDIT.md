# Home Assistant 2026.11 restore-state audit

Audit date: 2026-10-18

Home Assistant Core 2026.11 removes direct access to
`RestoreStateData.last_states`. Code which reads stored state directly must use
`RestoreStateData.async_get_stored_state(entity_id)`. Integrations which extend
`RestoreEntity` and call `async_get_last_state()` or
`async_get_last_extra_data()` remain supported.

The tracked Jarvis source contains no production use of `RestoreStateData`,
`last_states`, `restore_state.async_get`, `RestoreEntity`,
`async_get_last_state()`, or `async_get_last_extra_data()`. No restore-state API
migration is therefore required.

The Jarvis custom integration's sensors extend `CoordinatorEntity` and project
fresh `/api/home` data. Their registry identity is based on the config-entry ID
and a stable semantic key or area ID, not the mutable Home Assistant entity ID.
After a restart, missing room data projects `UNKNOWN` with unavailable/missing
freshness metadata; a prior value is not restored as current truth.

Isolated regressions cover:

- stable registry identity when an entity ID is renamed;
- isolation when another entity later reuses the old entity ID;
- stable canonical physical identity across an entity-ID rename;
- fresh coordinator truth after restart;
- safe handling of missing, `unknown`, and `unavailable` data;
- the absence of removed restore-state internals from production integration
  code.

No live Home Assistant upgrade, entity rename, registry manipulation, or
destructive restore-state test is part of this certification.
