"""Assistant serializers with strict validation of configurable values."""
from __future__ import annotations

from django.conf import settings
from rest_framework import serializers
from rest_framework.fields import empty

from apps.ai_gateway.registry import available_providers
from apps.assistants.models import Assistant
from apps.knowledge.models import KnowledgeBase

MAX_TOPICS = 100
MAX_TOPIC_LENGTH = 200


def _validate_topics(value, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise serializers.ValidationError({field_name: "Expected a list of topic strings."})
    if len(value) > MAX_TOPICS:
        raise serializers.ValidationError({field_name: f"At most {MAX_TOPICS} topics are allowed."})
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise serializers.ValidationError({field_name: "Every topic must be a string."})
        topic = item.strip()
        if not topic:
            continue
        if len(topic) > MAX_TOPIC_LENGTH:
            raise serializers.ValidationError(
                {field_name: f"Topics must be {MAX_TOPIC_LENGTH} characters or fewer."}
            )
        cleaned.append(topic)
    return cleaned


def validate_generation_parameters(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("generation_parameters must be an object.")
    allowed = {"temperature", "top_p"}
    unknown = set(value) - allowed
    if unknown:
        raise serializers.ValidationError(
            {"generation_parameters": f"Unsupported keys: {sorted(unknown)}. Allowed: {sorted(allowed)}."}
        )
    cleaned: dict = {}
    if "temperature" in value:
        try:
            temperature = float(value["temperature"])
        except (TypeError, ValueError):
            raise serializers.ValidationError({"generation_parameters": "temperature must be a number."})
        if not 0.0 <= temperature <= 1.5:
            raise serializers.ValidationError({"generation_parameters": "temperature must be between 0 and 1.5."})
        cleaned["temperature"] = temperature
    if "top_p" in value:
        try:
            top_p = float(value["top_p"])
        except (TypeError, ValueError):
            raise serializers.ValidationError({"generation_parameters": "top_p must be a number."})
        if not 0.01 <= top_p <= 1.0:
            raise serializers.ValidationError({"generation_parameters": "top_p must be between 0.01 and 1."})
        cleaned["top_p"] = top_p
    return cleaned


class AssistantSerializer(serializers.ModelSerializer):
    knowledge_base_ids = serializers.SerializerMethodField()
    policy_version = serializers.SerializerMethodField()

    class Meta:
        model = Assistant
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "status",
            "domain_type",
            "allowed_topics",
            "prohibited_topics",
            "system_instructions",
            "fallback_message",
            "default_language",
            "model_provider",
            "model_name",
            "generation_parameters",
            "maximum_output_tokens",
            "knowledge_required",
            "knowledge_base_ids",
            "config_version",
            "policy_version",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_knowledge_base_ids(self, obj) -> list[str]:
        return [str(pk) for pk in obj.knowledge_bases.values_list("id", flat=True)]

    def get_policy_version(self, obj) -> int:
        policy = getattr(obj, "policy", None)
        return policy.version if policy else 0


class AssistantWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")
    domain_type = serializers.ChoiceField(choices=Assistant.DomainType.choices, default=Assistant.DomainType.GENERIC)
    allowed_topics = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    prohibited_topics = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    system_instructions = serializers.CharField(
        max_length=8000, required=False, allow_blank=True, default=""
    )
    fallback_message = serializers.CharField(
        max_length=500,
        required=False,
        default="I can only help with questions about our services.",
    )
    default_language = serializers.CharField(max_length=16, required=False, default="en")
    model_provider = serializers.ChoiceField(
        choices=[(p, p) for p in available_providers()], required=False
    )
    model_name = serializers.CharField(max_length=100, required=False)
    generation_parameters = serializers.JSONField(required=False, default=dict)
    maximum_output_tokens = serializers.IntegerField(required=False, min_value=1, max_value=4096, default=600)
    knowledge_required = serializers.BooleanField(required=False, default=False)
    knowledge_base_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list, max_length=50
    )

    def validate_allowed_topics(self, value):
        return _validate_topics(value, "allowed_topics")

    def validate_prohibited_topics(self, value):
        return _validate_topics(value, "prohibited_topics")

    def validate_generation_parameters(self, value):
        return validate_generation_parameters(value)

    def validate_default_language(self, value):
        return value.strip()[:16] or "en"

    def validate(self, attrs):
        allowed = set(attrs.get("allowed_topics") or [])
        prohibited = set(attrs.get("prohibited_topics") or [])
        overlap = allowed & prohibited
        if overlap:
            raise serializers.ValidationError(
                {"prohibited_topics": f"Topics cannot be both allowed and prohibited: {sorted(overlap)[:5]}"}
            )
        # Provider/model defaults come from settings so environments without a
        # paid provider (tests, local dev) stay functional.
        attrs.setdefault("model_provider", settings.AI_DEFAULT_PROVIDER)
        attrs.setdefault("model_name", settings.AI_DEFAULT_MODEL_NAME)
        return attrs


class AssistantUpdateSerializer(AssistantWriteSerializer):
    name = serializers.CharField(max_length=150, required=False)
    domain_type = serializers.ChoiceField(choices=Assistant.DomainType.choices, required=False)
    system_instructions = serializers.CharField(max_length=8000, required=False, allow_blank=True)
    fallback_message = serializers.CharField(max_length=500, required=False)
    model_provider = serializers.ChoiceField(
        choices=[(p, p) for p in available_providers()], required=False
    )
    model_name = serializers.CharField(max_length=100, required=False)
    maximum_output_tokens = serializers.IntegerField(required=False, min_value=1, max_value=4096)
    knowledge_required = serializers.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Drop create-only defaults so a PATCH cannot silently reset fields.
        for field_name in (
            "description",
            "allowed_topics",
            "prohibited_topics",
            "default_language",
            "generation_parameters",
            "knowledge_base_ids",
            "maximum_output_tokens",
            "knowledge_required",
        ):
            self.fields[field_name].required = False
            self.fields[field_name].default = empty

    def validate(self, attrs):
        # PATCH semantics: only fall back to defaults when creating.
        if not self.partial:
            attrs.setdefault("model_provider", settings.AI_DEFAULT_PROVIDER)
            attrs.setdefault("model_name", settings.AI_DEFAULT_MODEL_NAME)
        if self.instance is not None:
            allowed = set(attrs.get("allowed_topics", self.instance.allowed_topics) or [])
            prohibited = set(attrs.get("prohibited_topics", self.instance.prohibited_topics) or [])
            overlap = allowed & prohibited
            if overlap:
                raise serializers.ValidationError(
                    {"prohibited_topics": f"Topics cannot be both allowed and prohibited: {sorted(overlap)[:5]}"}
                )
            return attrs
        return super().validate(attrs)


class AssistantUsageQuerySerializer(serializers.Serializer):
    since = serializers.DateTimeField(required=False)
    until = serializers.DateTimeField(required=False)


class PolicyTestCaseSerializer(serializers.Serializer):
    input = serializers.CharField(max_length=4000)
    expected_decision = serializers.ChoiceField(choices=["ALLOW", "REFUSE", "CLARIFY", "ESCALATE", "REVIEW"])
    has_knowledge = serializers.BooleanField(required=False, default=True)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class AssistantEvaluationSerializer(serializers.Serializer):
    cases = PolicyTestCaseSerializer(many=True, allow_empty=False, max_length=100)


class AssistantDraftRequestSerializer(serializers.Serializer):
    """Input for AI-assisted drafting: a plain-language domain description."""

    description = serializers.CharField(
        min_length=10,
        max_length=2000,
        help_text="Describe the business or domain the assistant should serve.",
    )
