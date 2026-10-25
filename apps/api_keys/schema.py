"""OpenAPI security scheme for platform API keys."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api_keys.authentication.ApiKeyAuthentication"
    name = "apiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "Organization API key: `Authorization: Bearer sk_live_<prefix>.<secret>`. "
                "The key is bound to one organization; `X-Organization-Id` must match it if sent."
            ),
        }
