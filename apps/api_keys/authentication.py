"""DRF authentication class for organization API keys."""
from __future__ import annotations

from rest_framework.authentication import BaseAuthentication, get_authorization_header

from apps.api_keys.principal import APIKeyPrincipal
from apps.api_keys.services import KEY_PREFIX, authenticate_api_key


class ApiKeyAuthentication(BaseAuthentication):
    """Authenticate ``Authorization: Bearer bb-...`` or ``X-API-Key: bb-...``.

    The class only claims requests whose credential looks like a platform API
    key, so JWT and session authentication continue to work unchanged.
    """

    def authenticate(self, request):
        raw = self._extract(request)
        if not raw:
            return None
        api_key = authenticate_api_key(raw)
        principal = APIKeyPrincipal(api_key=api_key)
        return principal, principal

    def authenticate_header(self, request) -> str:
        return 'Bearer realm="api"'

    @staticmethod
    def _extract(request) -> str | None:
        header_key = request.headers.get("X-API-Key")
        if header_key and header_key.startswith((KEY_PREFIX, "sk_")):
            return header_key.strip()

        parts = get_authorization_header(request).split()
        if len(parts) == 2 and parts[0].lower() == b"bearer":
            try:
                token = parts[1].decode("ascii")
            except UnicodeDecodeError:  # pragma: no cover - malformed header
                return None
            if token.startswith((KEY_PREFIX, "sk_")):
                return token
        return None
