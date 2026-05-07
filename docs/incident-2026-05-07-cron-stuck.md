# Incident: HK-Surge-Core cron silent for 22 days (2026-04-15 → 2026-05-07)

- **Discovered**: 2026-05-07 by user — surge monitor failed to alert on 1384.HK (滴普科技, +29% intraday)
- **Author**: claude-code session (root cause analysis), `brightliu77@gmail.com`
- **Affected job**: `HK-Surge-Core` id `6b656f3bd196`
- **Silent window**: 2026-04-15 15:35:20 HKT → 2026-05-07 14:49:52 HKT (22 days, 23 hours)
- **Resolution**: Hermes restart + recovery path in `cron/jobs.py:get_due_jobs()` rebuilt `next_run_at`
- **Status**: Resolved (job back to `state=scheduled`, `next_run_at=2026-05-08T09:00:00+08:00`)

## Symptoms

- 2026-05-07: 1384.HK surged ~29% during morning session; no surge alert was sent.
- `~/.hermes/profiles/owl/cache/hk_surge_alerts.jsonl` showed only 1 alert today (post-restart) vs typical ~5–15 per trading day on 04-13/04-14.
- `hermes --profile owl cron list` did not show the v2 job (separate problem, fixed in Phase 2).

## Timeline (HKT)

| Time                   | Event |
|------------------------|-------|
| 2026-04-12 15:13–15:15 | Initial registration of `HK-Surge-Core` (`6b656f3bd196`); agent commit `feat: add longbridge hk surge monitor skill` |
| 2026-04-13 / 04-14     | `6b656f3bd196` running normally (~5 min cadence during HK session) |
| 2026-04-15 15:10:08    | New job `20ec872074c8` (also `HK-Surge-Core`-class) starts — first ad-hoc test run |
| 2026-04-15 15:35:20    | **Last successful run of `6b656f3bd196`** (`end_reason='cron_complete'`) |
| 2026-04-15 15:37:32    | Last run of `20ec872074c8` (13 sessions total in its 27 min lifetime) |
| 2026-04-15 15:39:18    | First run of new `671340bf1414` (named `HK-Surge-Core`, same skill) |
| 2026-04-15 15:42:03    | Gateway clean stop (user-initiated) |
| 2026-04-15 15:42:08    | Gateway restart; cron ticker resumes |
| 2026-04-15 15:42:16    | Post-restart only `7c7a91e2d404` (HK-Surge-Extended) and `671340bf1414` resume — `6b656f3bd196` does **not** |
| 2026-04-15 15:43:34    | First run of `7dc82e382b12` (HK-Surge-Error-Relay; script missing — failed every tick) |
| 2026-04-15 → 2026-05-07 | `671340bf1414` ran 2,931 ticks; `7dc82e382b12` ran 4,067 (all failing). `6b656f3bd196` ran 0 times. |
| 2026-05-07 12:25:55    | Last run of `671340bf1414`; last run of `7dc82e382b12` at 12:25:56 |
| 2026-05-07 14:48:08    | Hermes gateway restarted |
| 2026-05-07 14:48:46.988 | `agent.log`: `Job 'HK-Surge-Core' had no next_run_at; recovering recurring run at 2026-05-07T14:50:00+08:00` |
| 2026-05-07 14:49:52    | First run of `6b656f3bd196` after recovery |

## Root cause

Two layered issues:

### (1) Duplicate-name job overwrite at 04-15 15:35–15:42

Between 15:35 and 15:39 on 2026-04-15, the user (or an agent) added a new cron job with `name='HK-Surge-Core'` and a fresh id (`671340bf1414`). The original `6b656f3bd196` was not removed but was *displaced as the operative HK-Surge-Core* — its sessions stopped at 15:35:20. After the 15:42 gateway restart, only `671340bf1414` resumed running. The two jobs coexisted in `jobs.json` with the same `name`, causing only one to be visible/operative depending on iteration order in CLI tooling.

Why `6b656f3bd196` stopped at exactly 15:35:20 and never resumed (even after restart at 15:42:08) is not provable from the current logs — `gateway.log.1` ends at 04-14 09:10 and the 04-15 to 05-02 gateway logs were rotated out. The most likely mechanism: an interactive `hermes cron edit` / `pause` / direct jobs.json mutation set `enabled=false` or stale `next_run_at`, and after restart `get_due_jobs()` filtered it out.

### (2) `next_run_at` nulled on 2026-05-07

Today between 12:25 and 14:48, the user removed `671340bf1414` / `7dc82e382b12` / `20ec872074c8` from `jobs.json` and re-enabled `6b656f3bd196` — but the re-enable path (likely a manual jobs.json edit, not `hermes cron resume`) did not populate `next_run_at`. On the 14:48 gateway restart, `cron/jobs.py:get_due_jobs()` recovery branch (`next_run_at is None` ⇒ `compute_next_run`) fired and re-armed the job:

```
2026-05-07 14:48:46,988 INFO cron.jobs:
  Job 'HK-Surge-Core' had no next_run_at; recovering recurring run at 2026-05-07T14:50:00+08:00
```

This is the smoking-gun line. Code reference: `cron/jobs.py:791-822`.

## Resolution

- Restart healed `6b656f3bd196` via recovery path.
- Phase 2 of recovery plan registered the dedicated v2 job (`2d1cb939ef55`, name `HK-Surge-Core-V2`) so v1 and v2 run side-by-side.
- Currently in `jobs.json`:
  - `6b656f3bd196` HK-Surge-Core    enabled=True next_run_at=2026-05-08T09:00:00+08:00
  - `7c7a91e2d404` HK-Surge-Extended enabled=True next_run_at=2026-05-08T09:00:00+08:00
  - `2d1cb939ef55` HK-Surge-Core-V2  enabled=True next_run_at=2026-05-08T09:00:00+08:00

## Lessons / hardening (proposed)

1. **Reject duplicate-name jobs in `create_job()` / `save_jobs()`**: refuse to write `jobs.json` if two enabled jobs share the same `name`. CLI should require `--force` to override.
2. **Watchdog cron** (Phase 4 of recovery plan): every 30 min during HK trading hours, alert if any `HK-Surge-*` job has had zero sessions in the last `2 * cadence` window.
3. **`save_jobs()` UTF-8**: separate cosmetic issue — `json.dump(..., indent=2)` lacks `ensure_ascii=False`, so any CLI mutation rewrites Chinese as `\uXXXX` and undoes manual UTF-8 edits.
4. **Recovery-path log → metric**: `Job '<name>' had no next_run_at; recovering` should fire a Prometheus counter or push-notification — silent recovery is good behavior, but un-monitored recovery hides upstream corruption (this incident).
5. **Direct-edit pattern documentation**: `~/.claude/projects/-Users-mingliang-repos-stock-alpha/memory/reference_hermes_cron.md` already documents the atomic-tempfile pattern; add a check-list "after editing jobs.json by hand, run `hermes --profile owl cron list` and confirm `next_run_at` non-null".

## Verification checklist (2026-05-08 trading day)

- [ ] 09:05 HKT — `sqlite3 state.db "SELECT id,started_at FROM sessions WHERE id LIKE 'cron_6b656f3bd196_20260508_%' OR id LIKE 'cron_2d1cb939ef55_20260508_%' OR id LIKE 'cron_7c7a91e2d404_20260508_%';"` shows non-zero rows for all three jobs.
- [ ] 10:05 HKT — same check, expect monotonic growth.
- [ ] 11:00 HKT — confirm `hk_surge_core_v2_alerts.jsonl` has rows if any v2 candidate fired (regime-dependent).
- [ ] 13:30 HKT — afternoon session post-resume tick verified.
- [ ] 15:50 HKT — final tick; review alert count vs typical 04-13 baseline.

## References

- `cron/jobs.py:791-822` — `get_due_jobs()` `next_run_at is None` recovery
- `cron/jobs.py:362` — `save_jobs()` (UTF-8 caveat)
- `cron/scheduler.py:1258-1416` — `tick()`
- `~/.hermes/profiles/owl/cron/jobs.json.bak.before_v2_register.20260507_193458` — pre-Phase-2 backup
- `~/.hermes/profiles/owl/logs/agent.log` — recovery line at 14:48:46
- `~/.hermes/profiles/owl/state.db` — sessions table
