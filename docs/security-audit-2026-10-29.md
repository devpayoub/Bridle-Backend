# Security audit — exposed tokens, keys and URLs

**Date:** 2026-10-29
**Scope:** `backend/`, `frontend/`, the running `BridleAI` stack, and everything reachable
through the public ngrok tunnel
**Method:** read-only inspection, then the adversarial view from outside
**Status:** findings below; code fixes applied where the fix is in code. Config and credential
actions are listed as **yours to run** and are not performed by the audit.

No secret appears in this document. Values are masked; the report records *where* a secret
leaks, never *what* it is.

---

## 1. Summary

| # | Severity | Finding | State |
| --- | --- | --- | --- |
| **F1** | **High** | Telegram bot token written to worker logs (7 lines) | Code fixed; **token must be rotated** |
| **F2** | **High** | `DEBUG=True`, `ALLOWED_HOSTS=['*']`, no HTTPS redirect — behind a public tunnel | Yours to fix |
| **F3** | Medium | Full OpenAPI schema (64 endpoints) and Swagger UI public | Yours to fix |
| **F4** | Medium | Django admin login page public | Yours to fix |
| **F5** | Medium | Webhook path key in access logs (9 lines) and in the ngrok dashboard | App logs fixed; rotate keys |
| **F6** | Medium | `AI_CREDENTIALS_KEY` unset — bot tokens keyed on `SECRET_KEY` | Yours to fix |
| **F7** | Low | `/api/v1/ready/` advertised feature state | Fixed |

**Refuted hypotheses** (checked, not assumed): the signing key is **not** the public default;
git history contains **no** secret; the database stores **no** plaintext credential; the API
returns **no** secret in any response.

---

## 2. What an attacker can do today

With only the public tunnel URL and no credentials:

1. Read the complete API surface — 64 endpoints, every field, every enum — from
   `/api/schema/`, and browse it at `/api/docs/`.
2. Reach `/admin/login/` and attempt credentials without tripping a channel-level limit.
3. Learn which subsystems are enabled from `/api/v1/ready/` (fixed in this change).
4. Cause a 500 and read Django's technical error page, which prints the traceback **with
   local variables** — because `DEBUG=True`.

With access to container logs or the ngrok dashboard — which support bundles, log shippers and
screen shares routinely expose:

5. Recover the **bot token** (F1) and control the bot: read its updates, send messages as it,
   move its webhook.
6. Recover the **webhook path key** (F5) and post forged updates to that channel.

What they **cannot** do: read tenant data, forge a session, or decrypt a stored credential —
the signing key is strong and every secret at rest is encrypted or hashed.

---

## 3. Findings in detail

### F1 — Bot token in worker logs · High

**Evidence**

```
$ docker logs assistant-platform-worker-1 | grep -oE "bot[0-9]{6,}:[A-Za-z0-9_-]+"
bot8876276056:<secret>     × 7 lines, 2026-10-28 23:39:54 and 23:40:22
```

**Mechanism.** `httpx` logs every request at INFO:

```
INFO httpx HTTP Request: POST https://api.telegram.org/bot<id>:<secret>/sendMessage "HTTP/1.1 200"
```

The Telegram Bot API puts the token **in the URL path**, so any HTTP client that logs its URLs
writes the token. `apps/channels/telegram/client.py` deliberately never logs and raises with
`from None`, but it cannot control a library's own logger.

**Why the redaction filter missed it.** The filter exists and is correct
(`SecretRedactionFilter` → `record.msg = redact(...)`, `args = ()`), but the seven lines were
emitted by a worker process started **before** the Telegram pattern was added to
`_REDACTION_PATTERNS`. Verified live afterwards:

```
INFO httpx HTTP Request: POST https://api.telegram.org/bot[REDACTED]/sendMessage
```

**Blast radius.** Full control of the bot: read incoming messages, send as the bot, repoint the
webhook to an attacker host.

**Action**

1. **Rotate the token now** — `/revoke` in @BotFather, then reconnect the bot in the console.
   Rotation is the only remedy; the old value is already in logs.
2. Code: covered by `tests/unit/test_log_secrets.py`.

---

### F2 — A debug server on a public URL · High

**Evidence**

```
DEBUG                True
ALLOWED_HOSTS        ['*']
SECURE_SSL_REDIRECT  False
EXPOSE_API_DOCS      True
```

**Why it matters.** With `DEBUG=True`, an unhandled exception returns Django's technical 500
page: full traceback, local variables, settings fragments. Local variables are exactly where a
bot token, a provider key or a decrypted secret sits mid-request. `ALLOWED_HOSTS=['*']` removes
the Host-header check that would otherwise narrow this.

DRF endpoints are shielded by the project's exception handler (generic JSON, no traceback);
Django-native views — `/admin/` — are not.

**Action** — pick one:

- Tunnel with production-ish settings: `DJANGO_SETTINGS_MODULE=config.settings.production`,
  `DJANGO_DEBUG=False`, `EXPOSE_API_DOCS=False`, `SECURE_SSL_REDIRECT=False` (the tunnel already
  terminates TLS), and a real `DJANGO_ALLOWED_HOSTS`.
- Or keep dev settings and expose **only** what you must. ngrok cannot path-filter, so that
  means a second tunnel or a reverse proxy in front.

---

### F3 — Public API map · Medium

```
GET /api/schema/   200   132 KB   64 endpoints
GET /api/docs/     200   Swagger UI
```

Every route, field name, enum value and error code, unauthenticated. It does not leak data, but
it removes all discovery cost from an attacker — including the exact shape of auth payloads.

**Action.** `EXPOSE_API_DOCS=False` (already the default in `production.py`; it is `True` only
in `development.py`). If you need docs on a tunnel, put them behind the same auth as the app.

---

### F4 — Public admin login · Medium

```
GET /admin/login/   200   4.1 KB
```

Django admin is a well-known credential-stuffing target and it shares the password store with
the app.

**Action.** Do not tunnel `/admin/`. If you must, restrict by source IP at the proxy, or set
`ADMIN_ENABLED` and mount it only on an internal listener. `django-axes`-style lockout is the
proper long-term answer.

---

### F5 — Webhook path key in logs · Medium

**Evidence**

```
$ docker logs BridleAI | grep -c "<path-key>"
9
```

The webhook URL is `/webhooks/telegram/<uuid>/<path-key>/`. Mounting it outside `/api/` kept it
out of the request-path middleware — but the dev server and gunicorn access logs record the
full request line anyway. It is also visible in the ngrok dashboard, and in any screenshot of it.

**Why it matters.** The path key is half of the webhook's authentication (the other half is the
`X-Telegram-Bot-Api-Secret-Token` header, which is *not* in the URL). Alone it is not enough to
forge a request — but it is enough to enumerate a live channel id and to aim at it.

**Action**

1. Code (applied): `_REDACTION_PATTERNS` now masks `/webhooks/<type>/<uuid>/<key>/` → the uuid
   stays for correlation, the key becomes `[REDACTED]`. Covered by tests.
2. External logs (ngrok, nginx, any log shipper) are **not** covered by that — rotate the
   webhook key with `POST /api/v1/channels/{id}/rotate-webhook/` after any exposure, and prefer a
   static domain so keys are not re-shared in screenshots.

---

### F6 — Bot tokens depend on `SECRET_KEY` · Medium

```
W001: Bot tokens are encrypted with SECRET_KEY because AI_CREDENTIALS_KEY is unset.
```

`_fernet()` derives its key from `AI_CREDENTIALS_KEY or SECRET_KEY`. Rotating `SECRET_KEY`
— which F1/F2 make likely — silently invalidates every connected bot token, and each workspace
must reconnect.

**Action.** Set `AI_CREDENTIALS_KEY` to 32+ random bytes **before** the next rotation, then
reconnect bots once so their ciphertext is rewritten under the dedicated key.

---

### F7 — Readiness leaked feature state · Low

```
{"status":"ready","checks":{...},"vector_search":"disabled"}
```

**Action (applied).** Payload is now `{status, checks}` only; `tests/api/test_system.py` asserts
the absence.

---

## 4. Verified good

| Check | Result |
| --- | --- |
| `SECRET_KEY` is not the repo default | 47-char custom value |
| `.env` ever committed | 0 commits, both repos |
| Secret-shaped strings in git history | 0 matches (`sk-…`, `bot<id>:…`) |
| Channel token at rest | Fernet `gAAAAA…`; plaintext **not** contained |
| Webhook secret at rest | 64-char HMAC digest only |
| API keys at rest | `key_hash` only, no plaintext column |
| Provider key at rest | `api_key_ciphertext` + `last4` |
| `GET /api/v1/channels/` unauthenticated | 401 |
| `GET /api/v1/auth/me/` unauthenticated | 401 |
| `POST /webhooks/...` with wrong path key | 404 (no enumeration oracle) |
| `GET /webhooks/...` | 405 (POST only) |
| Redaction of a live token | `bot[REDACTED]` |

---

## 5. Do these in order

1. **Rotate the Telegram bot token** (@BotFather → /revoke), reconnect the bot. *(F1)*
2. **Set `AI_CREDENTIALS_KEY`** to 32+ random bytes, restart, reconnect bots once. *(F6)*
3. **Stop tunnelling a debug server**: `DJANGO_DEBUG=False`, `EXPOSE_API_DOCS=False`,
   explicit `DJANGO_ALLOWED_HOSTS`. *(F2, F3)*
4. **Rotate the webhook key** for any channel whose URL appeared in a log or screenshot. *(F5)*
5. **Keep `/admin/` off the public listener.** *(F4)*
6. Long term: ship container logs to a store with retention and access control, and treat the
   log pipeline as part of the secret perimeter — F1 existed only because logs are readable.

---

## 6. Not covered

- No fuzzing or credential brute force.
- No review of Telegram's, ngrok's or the AI gateway's own security posture.
- No frontend dependency audit (`npm audit`) — a separate exercise.
- Tenant-isolation and prompt-injection behaviour are covered by the existing test suite, not
  re-audited here.

---

## 7. Remediation log — 2026-10-29

Applied in code (this commit):

| # | Change |
| --- | --- |
| F5 | `_REDACTION_PATTERNS` masks `/webhooks/<type>/<uuid>/<key>/` in app logs |
| F7 | `/api/v1/ready/` returns `{status, checks}` only |
| F6 | `AI_CREDENTIALS_KEY` now set; `_fernet()` no longer derives from `SECRET_KEY` |
| F2 | `DEBUG` and `EXPOSE_API_DOCS` are env-driven in `development.py`; the dev compose no longer hardcodes them |
| F4 | New `ADMIN_ENABLED` setting; `admin/` is unmounted when false |
| — | New check `channels.W004`: warns when `DEBUG` is on while `CHANNELS_PUBLIC_BASE_URL` is set |

Applied to the running environment (`.env`, not committed): `DJANGO_DEBUG=False`,
`EXPOSE_API_DOCS=False`, `ADMIN_ENABLED=False`, `AI_CREDENTIALS_KEY=<generated>`.

Verified from the public URL afterwards:

```
/admin/            404      (was 200)
/admin/login/      404
/api/docs/         404
/api/schema/       404      (was 200, 132 KB, 64 endpoints)
/api/v1/ready/     200      {status, checks} only
/api/v1/channels/  401
```

Still requires a human:

1. **Rotate the Telegram bot token** (@BotFather → /revoke). The value is in logs; no code
   change can undo that.
2. **Reconnect the bot** in the console. Rotating `AI_CREDENTIALS_KEY` invalidated the stored
   ciphertext, so the channel is now `ERROR / credentials_unreadable` by design — reconnect
   re-registers a fresh webhook and a fresh path key.
