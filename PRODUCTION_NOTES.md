# Production Notes & Operational Guide

This document outlines key technical considerations, architectural enhancements, and operational best practices for deploying the **Document Intake Assistant** in a production environment.

---

## 1. Persistent Storage & Transactional Integrity

### Current Prototype
- Uses an in-memory `SessionStore` backed by a Python `dict` and `asyncio.Lock`.
- Sessions are volatile and lost on server restart or across worker processes.

### Production Architecture
- **Primary Database (PostgreSQL):**
  - Store normalized sessions, message histories, and immutable audit logs in PostgreSQL.
  - JSONB columns can store structured `WishesState` snapshots with GIN indexing for querying.
- **Session Cache (Redis):**
  - Cache active session states with TTL (e.g., 24 hours) for fast sub-millisecond retrieval during conversation turns.
- **Optimistic Locking:**
  - Add a `version` integer column to the session record. Each state update increments the version and uses conditional updates (`WHERE id = :id AND version = :version`) to prevent concurrent message write races.

---

## 2. Scalability & Horizontal Distribution

- **Stateless Backend Service:**
  - Decouple compute from state by routing all session mutations through Redis/PostgreSQL.
  - Run multiple stateless FastAPI worker pods behind a load balancer (e.g., AWS ALB, NGINX, or Kubernetes Ingress).
- **Background Task Processing:**
  - Offload non-blocking post-turn tasks (such as document PDF compilation, analytics logging, and notification emails) to background task queues (Celery, ARQ, or AWS SQS).

---

## 3. Rate Limiting & Abuse Protection

- **API Gateway Rate Limiting:**
  - Apply token-bucket rate limits per client IP (e.g., 60 requests/minute) and per active session (e.g., 15 turns/minute) using Redis.
- **Input Guardrails:**
  - Enforce strict byte limits on incoming payloads (2,000 characters per message, maximum 100KB per JSON payload) to prevent denial-of-service via massive payloads.
- **Upstream LLM Quota Protection:**
  - Track upstream Gemini token usage and request rates per tenant/user to prevent exhausting API limits.

---

## 4. Authentication, Authorization & Multi-Tenancy

- **User Authentication:**
  - Implement OAuth2 / OpenID Connect (OIDC) or JWT session tokens (Auth0, Clerk, or Supabase Auth).
- **Tenant Isolation:**
  - Include `tenant_id` and `user_id` foreign keys on all session records.
  - Enforce Row Level Security (RLS) in PostgreSQL so users can only access their own intake sessions.
- **Role-Based Access Control (RBAC):**
  - Distinguish between `EndUser` (can submit turns and view their draft), `LegalReviewer` (read-only audit logs, export verified documents), and `Admin`.

---

## 5. Data Privacy, Compliance & Security (PII / GDPR / HIPAA)

- **PII Protection in Logs:**
  - Structured logs must **never** record raw user messages, extracted names, addresses, or LLM prompt bodies.
  - Log only metadata (session ID, turn duration, token count, validation status, error codes).
- **Encryption:**
  - **In-Transit:** Enforce TLS 1.3 for all HTTP and WebSocket connections.
  - **At-Rest:** Use AES-256 / KMS-managed encryption for PostgreSQL database volumes and Redis caches.
  - **Field-Level Encryption:** Apply envelope encryption for sensitive fields (e.g., legal names, addresses) prior to storing in the database.
- **Data Retention & Right to be Forgotten:**
  - Implement automated cleanup jobs for abandoned sessions after 30 days.
  - Provide an endpoint for complete session and data erasure compliant with GDPR Article 17.

---

## 6. Prompt Injection Defense & Input Sanitization

- **Delimiter Isolation:**
  - User messages are strictly enclosed within `<user_message>` tags in LLM prompts.
  - System instructions explicitly instruct the model that content within `<user_message>` is untrusted data and must never be interpreted as instructions.
- **Output Validation as a Security Boundary:**
  - Even if prompt injection succeeds in altering LLM output, the code validation layer (`updates.py`) rejects any fields not in the `FieldPath` enum, enforces data types, and requires exact evidence matches.
- **Content Security Policy (CSP):**
  - Serve strict CSP headers: `default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://api.generativeai.google; object-src 'none'`.
  - Frontend continues using `textContent` for all DOM updates, eliminating DOM-based XSS vulnerabilities.

---

## 7. LLM Resilience, Fallbacks & Model Governance

- **Retries & Circuit Breakers:**
  - Use `tenacity` for exponential backoff with jitter on transient network errors (HTTP 429, 502, 503).
  - Implement a circuit breaker (e.g., 5 consecutive failures opens circuit for 30s) to fail fast and notify users gracefully.
- **Model Fallback Chain:**
  - Primary: `gemini-2.5-flash` (fast, cost-efficient structured extraction).
  - Fallback: Secondary provider (e.g., Anthropic Claude 3.5 Haiku or OpenAI GPT-4o-mini) if Gemini encounters an extended outage.
- **Model Version Pinning:**
  - Pin exact model snapshots in production configurations to avoid unexpected behavior shifts from default alias updates.
- **Automated Regression Testing:**
  - Maintain a suite of golden conversation datasets. Run automated extraction accuracy evaluations on any prompt or model version changes.

---

## 8. Observability, Metrics & Telemetry

- **Structured Logging:**
  - Output structured JSON logs containing `timestamp`, `level`, `session_id`, `turn_id`, `latency_ms`, and `status`.
- **OpenTelemetry Distributed Tracing:**
  - Trace end-to-end request latency: `FastAPI Request -> Validation -> LLM Call -> Conflict Check -> Database Write`.
- **Prometheus / Grafana Metrics:**
  - `intake_turns_total` (counter, labeled by status)
  - `intake_turn_latency_seconds` (histogram)
  - `llm_request_duration_seconds` (histogram)
  - `llm_validation_rejections_total` (counter, labeled by rejection reason)
  - `intake_conflicts_detected_total` (counter, labeled by conflict type)
  - `intake_completion_rate` (gauge)

---

## 9. Human-in-the-Loop & Legal Workflow

- **Document Lifecycle State Machine:**
  ```
  [ INTAKE IN PROGRESS ] ──► [ COMPLETED & CONFIRMED ] ──► [ SUBMITTED FOR REVIEW ]
                                                                     │
                                    [ APPROVED & SIGNED ] ◄──────────┴──► [ REVISION REQUESTED ]
  ```
- **Immutable Audit Trail:**
  - Every field modification records `timestamp`, `author_role` (`user_chat`, `user_direct_edit`, `admin_review`), `old_value`, and `new_value`.
  - Audit logs are append-only and cryptographically hashed for non-repudiation.

---

## 10. Long-Running Sessions & Recovery

- **Draft Auto-Saving:**
  - State is persisted after every turn, allowing users to safely refresh their browser or return days later without losing progress.
- **Magic Link Resume:**
  - Allow users to resume an intake session via secure, one-time signed magic links sent to their verified email.

---

## 11. Internationalization (i18n) & Accessibility (a11y)

- **WCAG 2.1 AA Compliance:**
  - High contrast ratio, full keyboard navigation support, visible focus indicators, and `aria-live` regions for real-time conversation updates.
- **Localized Question Banks:**
  - Externalize question templates into locale files (`locales/en.json`, `locales/es.json`) while maintaining the same canonical `FieldPath` enum mappings.
- **Multi-Lingual Intake:**
  - Allow user prompts in different languages; prompt system instructions to extract standardized values into the canonical English schema.

---

## 12. Automated Testing & Load Testing

- **Unit & Integration Tests:**
  - Maintain 100% test coverage across deterministic domain modules (`state`, `updates`, `conflicts`, `questions`, `renderer`).
- **Load Testing:**
  - Benchmark performance with Locust or k6 simulating concurrent user turns to establish system capacity and Redis/DB connection pool sizes.

---

## 13. CI/CD & Containerization

- **Multi-Stage Dockerfile:**
  - Build minimal, hardened Docker images running as non-privileged users (`python:3.11-slim`).
- **Automated CI Gates:**
  - Pre-commit hooks and GitHub Actions running `ruff`, `mypy --strict`, and `pytest`.
- **Blue-Green / Canary Deployments:**
  - Deploy updates with zero downtime; verify health checks (`/api/health`) before traffic shifting.

---

## 14. Disaster Recovery & High Availability

- **Automated Backups:**
  - Daily full backups and continuous WAL archiving for PostgreSQL with a 30-day point-in-time recovery (PITR) window.
- **Multi-Region Redundancy:**
  - Deploy standby backend instances in a secondary cloud region with cross-region database replication for disaster failover.
