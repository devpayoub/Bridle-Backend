"""Policy API serializers with strict structural validation."""
from __future__ import annotations

from rest_framework import serializers

from apps.policies.models import AssistantPolicy

CONSTRAINT_KEYS = {"max_chars", "banned_phrases", "required_disclaimer", "block_pii", "messages"}
ESCALATION_KEYS = {"keywords", "message"}
TOOL_KEYS = {"allowed_tools"}


def _string_list(value, *, field: str, max_items: int = 100, max_length: int = 200) -> list[str]:
    if not isinstance(value, list):
        raise serializers.ValidationError({field: "Expected a list of strings."})
    if len(value) > max_items:
        raise serializers.ValidationError({field: f"At most {max_items} entries are allowed."})
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise serializers.ValidationError({field: "Every entry must be a string."})
        if len(item) > max_length:
            raise serializers.ValidationError({field: f"Entries must be {max_length} characters or fewer."})
        if item.strip():
            cleaned.append(item.strip())
    return cleaned


def validate_response_constraints(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("response_constraints must be an object.")
    unknown = set(value) - CONSTRAINT_KEYS
    if unknown:
        raise serializers.ValidationError(
            f"Unsupported response_constraints keys: {sorted(unknown)}"
        )
    cleaned = dict(value)
    if "max_chars" in cleaned:
        try:
            max_chars = int(cleaned["max_chars"])
        except (TypeError, ValueError):
            raise serializers.ValidationError("max_chars must be an integer.")
        if not 40 <= max_chars <= 20000:
            raise serializers.ValidationError("max_chars must be between 40 and 20000.")
        cleaned["max_chars"] = max_chars
    if "banned_phrases" in cleaned:
        cleaned["banned_phrases"] = _string_list(
            cleaned["banned_phrases"], field="banned_phrases", max_items=100, max_length=100
        )
    if "required_disclaimer" in cleaned and not isinstance(cleaned["required_disclaimer"], str):
        raise serializers.ValidationError("required_disclaimer must be a string.")
    if "block_pii" in cleaned and not isinstance(cleaned["block_pii"], bool):
        raise serializers.ValidationError("block_pii must be a boolean.")
    if "messages" in cleaned:
        if not isinstance(cleaned["messages"], dict):
            raise serializers.ValidationError("messages must be an object keyed by reason code.")
        cleaned["messages"] = {
            str(key)[:48]: str(text)[:500] for key, text in list(cleaned["messages"].items())[:30]
        }
    return cleaned


def validate_escalation_rules(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("escalation_rules must be an object.")
    unknown = set(value) - ESCALATION_KEYS
    if unknown:
        raise serializers.ValidationError(f"Unsupported escalation_rules keys: {sorted(unknown)}")
    cleaned = dict(value)
    if "keywords" in cleaned:
        cleaned["keywords"] = _string_list(cleaned["keywords"], field="keywords", max_items=100, max_length=100)
    if "message" in cleaned and not isinstance(cleaned["message"], str):
        raise serializers.ValidationError("message must be a string.")
    return cleaned


def validate_tool_permissions(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("tool_permissions must be an object.")
    unknown = set(value) - TOOL_KEYS
    if unknown:
        raise serializers.ValidationError(f"Unsupported tool_permissions keys: {sorted(unknown)}")
    cleaned = dict(value)
    if "allowed_tools" in cleaned:
        cleaned["allowed_tools"] = _string_list(
            cleaned["allowed_tools"], field="allowed_tools", max_items=50, max_length=60
        )
    return cleaned


class PolicyUpdateSerializer(serializers.Serializer):
    allowed_topics = serializers.ListField(child=serializers.CharField(), required=False)
    prohibited_topics = serializers.ListField(child=serializers.CharField(), required=False)
    industry = serializers.CharField(max_length=32, required=False, allow_blank=True)
    response_constraints = serializers.JSONField(required=False)
    escalation_rules = serializers.JSONField(required=False)
    tool_permissions = serializers.JSONField(required=False)
    uncertainty_handling = serializers.ChoiceField(
        choices=AssistantPolicy.UncertaintyHandling.choices, required=False
    )
    evaluator_mode = serializers.ChoiceField(choices=AssistantPolicy.EvaluatorMode.choices, required=False)
    allow_threshold = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)
    clarify_threshold = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)

    def validate_allowed_topics(self, value):
        return _string_list(value, field="allowed_topics")

    def validate_prohibited_topics(self, value):
        return _string_list(value, field="prohibited_topics")

    def validate_response_constraints(self, value):
        return validate_response_constraints(value)

    def validate_escalation_rules(self, value):
        return validate_escalation_rules(value)

    def validate_tool_permissions(self, value):
        return validate_tool_permissions(value)

    def validate(self, attrs):
        if "allow_threshold" in attrs and "clarify_threshold" in attrs:
            if attrs["clarify_threshold"] > attrs["allow_threshold"]:
                raise serializers.ValidationError(
                    {"clarify_threshold": "Must be less than or equal to allow_threshold."}
                )
        return attrs


class InlinePolicySerializer(serializers.Serializer):
    """Policy supplied directly by a developer, independent of any assistant."""

    allowed_topics = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    prohibited_topics = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    industry = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    response_constraints = serializers.JSONField(required=False, default=dict)
    escalation_rules = serializers.JSONField(required=False, default=dict)
    uncertainty_handling = serializers.ChoiceField(
        choices=AssistantPolicy.UncertaintyHandling.choices,
        required=False,
        default=AssistantPolicy.UncertaintyHandling.CLARIFY,
    )
    evaluator_mode = serializers.ChoiceField(
        choices=AssistantPolicy.EvaluatorMode.choices, required=False, default=AssistantPolicy.EvaluatorMode.RULES
    )
    allow_threshold = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)
    clarify_threshold = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)
    knowledge_required = serializers.BooleanField(required=False, default=False)

    def validate_allowed_topics(self, value):
        return _string_list(value, field="allowed_topics")

    def validate_prohibited_topics(self, value):
        return _string_list(value, field="prohibited_topics")

    def validate_response_constraints(self, value):
        return validate_response_constraints(value)

    def validate_escalation_rules(self, value):
        return validate_escalation_rules(value)


class PolicyEvaluationRequestSerializer(serializers.Serializer):
    assistant_id = serializers.UUIDField(required=False, allow_null=True)
    policy = InlinePolicySerializer(required=False)
    request_text = serializers.CharField(max_length=8000)
    proposed_response = serializers.CharField(
        max_length=20000, required=False, allow_blank=True, default=""
    )
    knowledge_context = serializers.CharField(
        max_length=20000,
        required=False,
        allow_blank=True,
        default="",
        help_text="Approved context used to check grounding of the proposed response.",
    )

    def validate(self, attrs):
        if not attrs.get("assistant_id") and not attrs.get("policy"):
            raise serializers.ValidationError(
                {"policy": "Provide either assistant_id or an inline policy."}
            )
        return attrs
