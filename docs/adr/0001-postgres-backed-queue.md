# ADR 0001 — PostgreSQL-backed job queue for v1

Status: accepted · Date: 2026-09-13

## Context
The pipeline needs idempotent, retryable background jobs (§28). Options: Celery/RQ (Redis), a hosted broker, or a table in the existing PostgreSQL.

## Decision
Use a `jobs` table with `SELECT … FOR UPDATE SKIP LOCKED`, idempotency keys, exponential backoff and a dead-letter status. No additional infrastructure.

## Consequences
* One database to run, back up and reason about; matches §46 ("do not build many databases").
* Throughput is bounded by PostgreSQL row locking — adequate for a single-machine v1 (tens of jobs/second is far beyond what LLM extraction can consume).
* The queue module exposes `enqueue/claim/complete/fail`; swapping in a broker later means reimplementing that interface only.
