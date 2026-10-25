#!/usr/bin/env python3
"""End-to-end smoke test: log in, ensure an assistant, mint a key, chat with it.

This exercises the public API exactly the way a client's backend would, so it
catches wiring problems that a UI click-through hides.

    export SMOKE_EMAIL=you@example.com SMOKE_PASSWORD='...'
    python backend/scripts/smoke_chat.py

Options:
    --base-url   API root            (default http://localhost:8000)
    --provider   assistant provider  (default: whatever the platform default is)
    --message    prompt to send      (default "What time is check-in?")
    --keep       keep the API key instead of revoking it
    --json       print the raw chat response

Exit code 0 means the model answered. Non-zero means something in the chain
failed, and the failing step is named in the output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

STEP = 0


def step(title: str) -> None:
    global STEP
    STEP += 1
    print(f"\n[{STEP}] {title}")


def call(
    base: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    api_key: str | None = None,
    org: str | None = None,
    body: dict | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict]:
    url = f"{base.rstrip('/')}/api/v1{path}"
    headers = {"accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    if org:
        headers["x-organization-id"] = org
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return exc.code, {"raw": raw.decode(errors="replace")[:500]}


def fail(message: str, payload: dict | None = None) -> None:
    print(f"    FAILED: {message}")
    if payload:
        print(json.dumps(payload, indent=2)[:1500])
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--email", default=os.environ.get("SMOKE_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("SMOKE_PASSWORD", ""))
    parser.add_argument("--provider", default="", help="mock | openai. Default: platform default.")
    parser.add_argument("--model", default="", help="Model id to pin on the assistant, e.g. gpt-4o-mini.")
    parser.add_argument("--message", default="What time is check-in?")
    parser.add_argument("--keep", action="store_true", help="Do not revoke the key at the end.")
    parser.add_argument("--json", action="store_true", help="Print the raw chat response.")
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("Set SMOKE_EMAIL and SMOKE_PASSWORD (or pass --email/--password).")

    base = args.base_url
    print(f"Bridle AI smoke test → {base}")

    step("Log in")
    status, payload = call(base, "POST", "/auth/login/", body={"email": args.email, "password": args.password})
    if status != 200:
        fail(f"login returned {status}", payload)
    token = payload["access"]
    print(f"    ok · {payload['user']['email']}")

    step("Resolve workspace")
    status, payload = call(base, "GET", "/organizations/", token=token)
    if status != 200:
        fail(f"listing organizations returned {status}", payload)
    orgs = payload.get("results", payload if isinstance(payload, list) else [])
    if orgs:
        org = orgs[0]
        print(f"    using existing · {org['name']} ({org['plan_code']})")
    else:
        status, org = call(base, "POST", "/organizations/", token=token, body={"name": "Smoke Test Workspace"})
        if status != 201:
            fail(f"creating an organization returned {status}", org)
        print(f"    created · {org['name']}")
    org_id = org["id"]

    step("Find or create an assistant")
    status, payload = call(base, "GET", "/assistants/", token=token, org=org_id)
    if status != 200:
        fail(f"listing assistants returned {status}", payload)
    assistants = payload.get("results", [])
    if assistants:
        assistant = assistants[0]
        print(f"    using existing · {assistant['name']} ({assistant['status']})")
        patch: dict = {}
        if args.provider and assistant["model_provider"] != args.provider:
            patch["model_provider"] = args.provider
        if args.model and assistant["model_name"] != args.model:
            patch["model_name"] = args.model
        if patch:
            status, patched = call(
                base, "PATCH", f"/assistants/{assistant['id']}/", token=token, org=org_id, body=patch
            )
            if status != 200:
                fail(f"updating the assistant returned {status}", patched)
            assistant = patched
            print(f"    updated · {assistant['model_provider']}/{assistant['model_name']}")
    else:
        body = {
            "name": "Smoke Concierge",
            "description": "Answers check-in and breakfast questions.",
            "domain_type": "HOTEL",
            "allowed_topics": ["check-in", "breakfast", "wifi"],
            "prohibited_topics": ["refunds", "legal advice"],
            "system_instructions": "Answer only check-in, breakfast and wifi questions.",
            "fallback_message": "I can only help with hotel questions.",
        }
        if args.provider:
            body["model_provider"] = args.provider
        if args.model:
            body["model_name"] = args.model
        status, assistant = call(base, "POST", "/assistants/", token=token, org=org_id, body=body)
        if status != 201:
            fail(f"creating an assistant returned {status}", assistant)
        print(f"    created · {assistant['name']} · {assistant['model_provider']}/{assistant['model_name']}")

    if assistant["status"] != "ACTIVE":
        status, payload = call(base, f"POST", f"/assistants/{assistant['id']}/activate/", token=token, org=org_id)
        if status != 200:
            fail(f"activating the assistant returned {status}", payload)
        print(f"    activated · {assistant['name']}")

    step("Create an API key")
    status, created = call(
        base,
        "POST",
        "/api-keys/",
        token=token,
        org=org_id,
        body={"name": "smoke test", "scopes": ["chat:write"], "environment": "DEVELOPMENT"},
    )
    if status != 201:
        fail(f"creating an API key returned {status}", created)
    api_key = created["key"]
    masked = f"{api_key[:6]}…{api_key[-4:]}"
    print(f"    ok · {masked} · {len(api_key)} characters")

    step(f"Chat · {args.message!r}")
    status, chat = call(
        base,
        "POST",
        "/chat/completions/",
        api_key=api_key,
        body={"assistant_id": assistant["id"], "message": args.message},
        idempotency_key="smoke-" + os.urandom(8).hex(),
    )
    if status != 200:
        print(f"    FAILED: chat returned {status}")
        print(json.dumps(chat, indent=2)[:1200])
        if not args.keep:
            call(base, "POST", f"/api-keys/{created['id']}/revoke/", token=token, org=org_id)
            print("    (key revoked)")
        sys.exit(1)

    if args.json:
        print(json.dumps(chat, indent=2))
    else:
        print(f"    decision    {chat['decision']} ({chat['reason_code']})")
        print(f"    latency     {chat['latency_ms']} ms")
        print(f"    tokens      {chat['usage']['input_tokens']} in / {chat['usage']['output_tokens']} out")
        print(f"    citations   {len(chat.get('citations') or [])}")
        print(f"    request id  {chat['request_id']}")
        print("\n    answer:")
        for line in (chat["content"] or "").splitlines() or [""]:
            print(f"      {line}")

    if not args.keep:
        step("Clean up")
        status, _ = call(base, "POST", f"/api-keys/{created['id']}/revoke/", token=token, org=org_id)
        print(f"    key revoked ({status})")

    print("\nAll good: the assistant answered through the public API.")


if __name__ == "__main__":
    main()
