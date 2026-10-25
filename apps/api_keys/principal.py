"""Authentication principal representing an API key.

DRF requires ``request.user``. API keys are not users, so a small immutable
principal is returned instead. Views must use ``request.tenant`` for identity
and never treat the principal as a database user.
"""
from __future__ import annotations

from dataclasses import dataclass

from apps.api_keys.models import APIKey


@dataclass(frozen=True)
class APIKeyPrincipal:
    api_key: APIKey

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = None
    id = None
    email = ""

    @property
    def organization(self):
        return self.api_key.organization

    def has_scope(self, scope: str) -> bool:
        return self.api_key.has_scope(scope)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"APIKeyPrincipal({self.api_key.prefix})"
