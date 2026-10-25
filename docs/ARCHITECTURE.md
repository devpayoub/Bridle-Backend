# Architecture and design decisions

This document explains *why* the backend is shaped the way it is. For setup and API
usage see [README.md](../README.md).

---

## 1. Shape of the system

A **modular monolith**. One deployable unit (API + worker + beat), strict internal module
boundaries, no network hops between domains. The boundary discipline is what makes a later
extraction into services mechanical rather than a rewrite: each app already owns its models,
services and public functions.

### Dependency direction

```
common  ←  accounts  ←  organizations  ←  api_keys
                              ↑
        audit ← billing ← assistants ← policies
                              ↑
                         knowledge
                              ↑
                       conversations  →  ai_gateway
                              ↑
                            usage
```

Rules that keep this acyclic:

* `common` depends on nothing in the project (base models, error envelope, crypto, logging).
* `organizations` owns the capability matrix and tenant resolution; everything tenant-scoped
  depends on it, never the reverse.
* `policies` owns the engine (pure Python, no Django models in `engine/`) and the
  `AssistantPolicy` model; `assistants` imports policy *services* lazily inside functions to
  avoid a module-level cycle.
* `ai_gateway` never imports application models — it only knows its own DTOs.
* `conversations.chat` is the only place that composes policy + retrieval + provider + usage.

### Why a monolith

The workload is I/O-bound and shares one database with foreign keys across domains
(assistant → conversation → message). Splitting it early would introduce distributed
transactions for no benefit. The seams that *will* need to scale independently (provider
calls, document ingestion) already run in Celery workers, so they can be separated first.

---

## 2. Request pipeline

```
RequestContextMiddleware      → correlation id, timing, redacted structured logs
Authentication                → ApiKeyAuthentication | JWTAuthentication | SessionAuthentication
Tenant resolution             → TenantContext(organization, role|scopes, membership|api_key)
Capability permission         → tenant.can("assistant:write")
View (thin)                   → serializer validation → service call
Service                       → business rules, transactions
Model                         → constraints, indexes
Exception handler             → single error envelope + Retry-After
```

**Views stay thin on purpose.** Anything with a rule (state transitions, entitlement checks,
quota, policy decisions) lives in a service so it can be reused by Celery tasks, the smoke
test and future management commands — and tested without HTTP.

### Tenant resolution details

`resolve_tenant()` is the only way to obtain an organization:

* API-key callers: organization comes from the key. A conflicting `X-Organization-Id` is
  rejected rather than trusted.
* User callers: the id from the header (or the single membership) is looked up **through**
  the membership table. No membership → `404`.
* Inactive organization → `403` with a stable code.
* The resulting `TenantContext` is cached on the request and carries the capability set, so
  every downstream check is a set lookup.

Cross-tenant object access returns **404, not 403**: a 403 would confirm the object exists.

---

## 3. Data model

```
User ──< OrganizationMembership >── Organization ──1 Subscription ──> Plan
                                      │
                                      ├──< APIKey ──> Assistant (optional scope)
                                      ├──< Assistant ──< AssistantConfigurationVersion
                                      │        │
                                      │        ├──1 AssistantPolicy
                                      │        ├──< Conversation ──< Message
                                      │        │        └──< PolicyEvaluation
                                      │        └──>< KnowledgeBase ──< Document ──< DocumentChunk
                                      │                              └──< DocumentProcessingJob
                                      ├──< UsageRecord ──> Assistant | APIKey | Conversation
                                      ├──< AuditLog
                                      └──< UsageLimit | BillingPeriod | UsageSummary
```

Notes:

* **UUID primary keys** for externally referenced resources (not enumerable).
* **`organization` is denormalised onto `DocumentChunk` and `RetrievalRecord`.** Retrieval is
  the hottest read path; filtering by tenant without a join makes an accidental cross-tenant
  read much harder to write and much cheaper to run.
* **`Assistant.allowed_topics` / `prohibited_topics` live on the assistant** (they are part
  of its declared domain). Thresholds, response constraints, escalation rules and tool
  permissions live on `AssistantPolicy`. `PUT /assistants/{id}/policy/` writes both in one
  transaction, so there is exactly one write path and no drift.
* **Configuration snapshots are immutable.** Every mutation writes
  `AssistantConfigurationVersion(version=n, snapshot=json)` and bumps `Assistant.config_version`;
  `Conversation.config_version` records what served it. JSON is used here because it is a
  genuine historical artefact, not live relational data.
* **Messages are relational rows**, never one JSON blob: retention, anonymisation and
  pagination stay cheap and auditable.
* **Soft delete for documents** keeps audit history while chunks and the stored file are
  removed immediately.

### Tenant-sensitive uniqueness

Assistant names are unique **per organization** (`UniqueConstraint(organization, slug)`), not
globally. The same applies to knowledge bases. Organization slugs are globally unique because
they appear in URLs.

---

## 4. Policy engine

### Interface

```python
PolicyEngine.evaluate_request(spec: PolicySpec, text: str, has_knowledge: bool) -> PolicyDecision
PolicyEngine.evaluate_response(spec: PolicySpec, text: str, context_text: str) -> PolicyDecision
```

`PolicySpec` is a plain dataclass — the engine has no database dependency, which is why it is
fast to test (hundreds of cases in milliseconds) and safe to reuse from a Celery task or a
different service later.

`PolicyDecision` carries `decision`, `reason_code`, `user_safe_message`, `evaluator`,
`evaluator_version`, `confidence` and structured `metadata` (matched topic, scores,
thresholds, matched pattern). No chain-of-thought, no free-text model reasoning.

### Evaluation order (request)

1. Empty / over-length → `REFUSE INVALID_INPUT`.
2. **Platform injection detection** → `REFUSE PROMPT_INJECTION_DETECTED`.
3. Prohibited topic match → `REFUSE PROHIBITED_TOPIC`.
4. Escalation keyword → `ESCALATE`.
5. Allowed-topic scoring:
   `score ≥ allow_threshold` → allow path; `≥ clarify_threshold` → `CLARIFY`; else `REFUSE OUT_OF_DOMAIN`.
6. Knowledge gate when `knowledge_required`.

The order matters: mandatory controls run **before** customer configuration is consulted, so
a permissive topic list cannot neutralise them. There is a test for exactly this.

### Scoring

`score = 0.7 × topic_coverage + 0.3 × query_coverage` over stemmed content tokens, with an
exact-phrase shortcut to `1.0` and `|`-separated aliases per topic. Deterministic, explainable,
and cheap. It is a lexical matcher — see limitations.

### Model-assisted evaluation (optional)

Enabled per assistant (`evaluator_mode=HYBRID`) and globally
(`POLICY_MODEL_EVALUATOR_ENABLED`). It is only consulted when rules **allow**; it can tighten
the decision (`OUT_OF_DOMAIN` → `REFUSE`, low confidence → `CLARIFY`) but never loosens a
refusal. Its output always carries a confidence value and is labelled `evaluator="model"` in
the audit trail so the two signals can be compared later.

### Failure semantics

| Failure | `POLICY_FAIL_MODE=closed` (default) | `=open` |
| --- | --- | --- |
| Rules evaluator raises | `REFUSE EVALUATOR_UNAVAILABLE` | `REFUSE EVALUATOR_UNAVAILABLE` (no rules result exists) |
| Model evaluator raises | `REFUSE EVALUATOR_UNAVAILABLE` | return the rules result, `metadata.degraded=true`, warning logged |

Neither mode silently skips a check: `closed` refuses, `open` records the degradation in the
decision metadata and logs it.

### Response validation

Empty answer, echoed injection, prohibited topic, banned phrases, PII (opt-in), unsupported
numeric claims (when grounded: numbers in the answer absent from the approved context, ratio
> 0.34 → `REVIEW`), and length constraints (truncated at a word boundary, flagged).

`REVIEW` means the answer is **not** returned: the caller receives the safe message and the
decision, so a human or a stricter path can take over.

---

## 5. Chat orchestration

`apps/conversations/chat.py` is deliberately explicit rather than clever:

* Assistant lookup is tenant-scoped and honours API-key assistant binding.
* Rate limit → idempotency claim → policy → retrieval → quota → generation → validation →
  persistence.
* **No database transaction is held during the provider call.** The provider timeout is 45 s;
  holding a transaction that long would pin a connection and risk lock contention.
* **Idempotency**: `(organization, key)` unique row with a request fingerprint. Same key +
  same body replays the stored response; same key + different body → `409`; a claim still in
  progress → `409`. The claim row is deleted if the request fails so the client can retry.
* **Partial failure**: on provider error the user message is stored, no assistant message is
  created, the quota reservation is released and `502` is returned. History never implies an
  answer that was not produced.
* **Citations are never fabricated**: they are the chunks actually retrieved, with document
  title, chunk index and page.

### Prompt construction

1. Server-owned preamble (domain restriction, untrusted-context rule, no instruction
   disclosure, no promises, no invented facts).
2. Customer configuration, explicitly labelled subordinate.
3. Bounded history window.
4. Current turn: `<approved_context>` block + question.

Retrieved text is sanitised at ingestion and delimited at generation. This is defence in
depth, not a proof: prompt injection remains an open problem, which is why deterministic
input and output checks run outside the model.

---

## 6. Knowledge pipeline

```
upload → validate (ext, magic bytes, size, UTF-8) → store (generated key)
       → extract → chunk → sanitise → embed (optional) → index → READY
```

* Extraction and embedding run **outside** transactions; only the final persistence
  (delete old chunks, bulk insert, compute `search_vector`, update status) is atomic.
* `search_vector` is computed **in the database** with `SearchVector` so tokenisation matches
  query-time tokenisation exactly.
* Ingestion is idempotent: re-running replaces the document's chunks, so a Celery retry after
  a worker crash cannot duplicate content.
* `DocumentProcessingJob` records attempts, error code and a safe message (no paths, no stack
  traces, no secrets).
* Retrieval interface (`Retriever` protocol) has three implementations: full-text, vector,
  hybrid (reciprocal-rank fusion). One branch failing degrades rather than fails.

---

## 7. Security controls

| Threat | Control |
| --- | --- |
| Broken object-level authorisation | Tenant-filtered querysets + 404 on foreign objects + explicit cross-tenant tests |
| Broken function-level authorisation | Capability matrix, per-action permissions, API-key scopes |
| Cross-tenant leakage | Three layers (queryset, service, permission); denormalised `organization` on chunks |
| SQL injection | ORM only, parameterised queries; no string-built SQL |
| Prompt injection (request) | Pattern detection on normalised text (zero-width stripped) before generation |
| Prompt injection (documents) | Ingestion-time neutralisation + delimited untrusted context + platform rules |
| Secret disclosure | HMAC-stored API keys, redacting log filter, no secrets in errors, docs gated |
| Credential stuffing | Per-IP **and** per-account login throttles |
| Excessive consumption | Redis fixed-window rate limits + pre-call quota reservation with release |
| Unsafe uploads | Extension + magic-byte + size validation, generated storage keys, no execution |
| Replay / duplicate processing | `Idempotency-Key`, unique constraints, idempotent Celery tasks |
| SSRF | No URL fetching or crawling implemented at all |
| Transport | HSTS, SSL redirect, secure cookies, explicit CORS/CSRF origins in production |
| Privilege escalation | Last-owner protection, only owners may change ownership, audit trail |

Audited events: organisation/membership changes, API-key create/rotate/revoke, assistant
create/update/status/duplicate, policy updates, knowledge and document changes, conversation
anonymisation, account deactivation.

---

## 8. Trade-offs made deliberately

| Decision | Alternative | Why |
| --- | --- | --- |
| Rules-first policy engine | Model-only classification | Deterministic, testable, cheap, explainable; the model adds recall without being trusted for refusals |
| Lexical topic matching | Embedding similarity for domain checks | No extra latency/cost per request; failures are measurable and tunable; synonyms handled with aliases |
| Postgres full-text default | Vector search default | Works without embeddings, zero extra cost, good enough for curated handbooks; hybrid available |
| Denormalised `organization` on chunks | Join through knowledge base | Hot path simplicity and safer isolation |
| 404 for cross-tenant access | 403 | Does not disclose existence |
| Rate limit fails open | Fail closed | A cache outage should not take the product down; quotas (spend) still fail closed |
| One-shot migrate container | Migrate on API start | Prevents concurrent migration races across replicas |
| Snapshot config as JSON | Fully normalised history tables | It is an immutable historical artefact; JSON is the right shape |

---

## 9. What is not implemented

See the "Known limitations" section of the README for the authoritative list. The most
important ones for anyone extending this system:

* No live payment provider (internal entitlements only, contract documented).
* The model-based evaluator and OpenAI adapter were tested with a stubbed SDK, not against a
  live endpoint (no credentials in this environment).
* pgvector path is implemented and unit-tested with deterministic vectors, but disabled by
  default and not exercised end to end with real embeddings.
* No invitation emails, no streaming, no crawling, no frontend.
