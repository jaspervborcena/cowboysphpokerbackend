# Game Server Monitoring TODO

Last reviewed: 2026-10-19

This is a future implementation plan. It does not claim monitoring or failover is already configured.

## Current State

- The game server is deployed as a Cloud Run service; Cloud Run provides platform logs and service metrics in Google Cloud.
- `GET /health` currently returns `{"status":"ok"}`. It confirms the process can answer HTTP, but does not check Firestore, Pub/Sub, Redis, table ownership, or active game health.
- API errors are emitted as structured JSON logs with a trace ID, error code/type, technical message, stack trace, and HTTP context. The public error response also includes the trace ID.
- Poker-specific metrics, a project dashboard, alert policies, and incident runbooks are not yet implemented/configured in this repository.
- Cloud Run restarting an instance is not equivalent to safely recovering its active tables. Complete the durable multi-worker routing, fencing, and recovery requirements in `TODO.md` before relying on automatic table failover.

## Monitoring Approach

Start with Google Cloud Monitoring, Cloud Logging, and Error Reporting for the existing Cloud Run deployment. Avoid operating a separate Prometheus/Grafana/Kibana stack for the initial rollout. Consider Grafana or Managed Service for Prometheus only if there is a concrete need for cross-platform dashboards or Prometheus-compatible metrics. Reassess log storage/search only if Cloud Logging no longer meets requirements.

## Implementation Plan

### 1. Cloud Run Baseline Dashboard

- [ ] Confirm production project, service names, regions, environment labels, and who receives alerts.
- [ ] Create a Cloud Monitoring dashboard for the game server and wallet API, with request count, 4xx/5xx rates, latency percentiles, instance count, container restarts, CPU, and memory.
- [ ] Add an uptime check for the public game-server `/health` endpoint and show its availability history.
- [ ] Add alert policies for sustained service unavailability, elevated 5xx rate, high latency, repeated container restarts, and sustained resource saturation.
- [ ] Route notifications to the agreed email or on-call channel; document how to acknowledge and investigate alerts.

### 2. Health and Dependency Readiness

- [ ] Keep liveness lightweight: it should report whether the process/event loop is alive and must not trigger restart loops during a dependency outage.
- [ ] Add a separate readiness check for required dependencies and configuration, with bounded timeouts; initially include Firestore and only include Pub/Sub/Redis when routing is enabled.
- [ ] Decide how readiness affects Cloud Run traffic and deployment rollouts; do not expose secrets or internal dependency details in health responses.
- [ ] Test process failure, dependency outage, slow dependency response, startup, and recovery behavior.

### 3. Logs and Error Investigation

- [ ] Verify Cloud Logging parses the existing JSON error records into queryable fields, including service, severity, code, trace ID, route, and status.
- [ ] Add request/operation context for WebSocket disconnects, table IDs, worker IDs, and lease generations where useful; never log credentials, card secrets, or sensitive wallet data.
- [ ] Ensure API response trace IDs can be searched directly in Cloud Logging and Error Reporting groups related exceptions.
- [ ] Create saved log queries for server exceptions, Firestore failures, worker/lease failures, settlement failures, and WebSocket disconnect/error patterns.
- [ ] Set log retention and exclusions deliberately; avoid retaining verbose per-action logs indefinitely without a clear operational need.

### 4. Poker Runtime Metrics

- [ ] Export low-cardinality metrics for active tables, active WebSocket connections, actions by outcome, action latency, and turn-timer drift.
- [ ] Measure worker lease claims, renewals, expirations, conflicts, fencing rejections, and recovery attempts.
- [ ] Measure Firestore transaction latency/failures, pending settlement count and oldest pending age, and checkpoint commit/recovery outcomes.
- [ ] When Pub/Sub/Redis routing is enabled, measure command/reply latency, delivery failures, retry counts, and queue/backlog depth.
- [ ] Use bounded labels such as service, region, operation, and outcome. Do not label metrics with user IDs, table IDs, hand IDs, or other unbounded identifiers; use structured logs for investigating a specific entity.
- [ ] Add charts and alerts for stale leases, repeated action failures, excessive timer drift, old pending settlements, checkpoint recovery failures, and routing backlog.

### 5. Incident Runbooks and Failure Drills

- [ ] Write short runbooks for service unavailable, Firestore unavailable, Pub/Sub/Redis unavailable, elevated action latency, stale worker lease, and stuck settlement.
- [ ] Define alert severity, owner, escalation path, and user-facing impact for each alert.
- [ ] In a non-production environment, terminate a worker during an active table and verify lease expiry, checkpoint restoration, fencing of the stale worker, and no duplicate chip mutations.
- [ ] Exercise Cloud Run rollback and verify readiness, logs, alert delivery, and recovery after deployment.
- [ ] Load-test representative table and connection counts; record latency, error rate, Firestore contention, quotas, and cost before increasing worker count.

## Acceptance Criteria

- Operators can see service availability, request failures/latency, resource saturation, WebSocket/table activity, lease health, and settlement backlog on a single dashboard.
- Alerts reach the agreed recipient and each has a tested investigation/runbook path.
- A trace ID from an API error leads to the relevant structured log without exposing private game or wallet data.
- A worker failure drill demonstrates safe table recovery before multi-worker live tables are enabled.
- Monitoring and logging costs, retention, and alert thresholds are reviewed before production rollout.

## Related Work

- Multi-worker correctness and failover prerequisites: `TODO.md` under **Next Work: Durable Multi-Worker Tables**.
- Existing app health endpoint: `app/main.py`.
- Existing structured error handling: `app/errors.py`.
