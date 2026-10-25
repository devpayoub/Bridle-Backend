from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api_keys"
    verbose_name = "API keys"

    def ready(self) -> None:
        # Registers the OpenAPI security scheme for API-key authentication.
        from apps.api_keys import schema  # noqa: F401
