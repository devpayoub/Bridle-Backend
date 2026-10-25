"""Background task tests: retention, audit purge, usage roll-up."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.tasks import purge_expired_audit_logs
from apps.billing.models import UsageSummary
from apps.conversations.models import Conversation, Message
from apps.conversations.tasks import purge_expired_conversations
from apps.usage.models import UsageRecord
from apps.usage.tasks import roll_up_usage_summaries


@pytest.mark.django_db
class TestConversationRetention:
    def test_expired_conversations_are_anonymised(self, organization, active_assistant):
        expired = Conversation.objects.create(
            organization=organization,
            assistant=active_assistant,
            retention_expires_at=timezone.now() - timedelta(days=1),
        )
        Message.objects.create(conversation=expired, role=Message.Role.USER, content="secret question")
        Message.objects.create(conversation=expired, role=Message.Role.ASSISTANT, content="secret answer")

        fresh = Conversation.objects.create(
            organization=organization,
            assistant=active_assistant,
            retention_expires_at=timezone.now() + timedelta(days=30),
        )
        Message.objects.create(conversation=fresh, role=Message.Role.USER, content="keep me")

        processed = purge_expired_conversations()

        assert processed == 1
        expired.refresh_from_db()
        assert expired.status == Conversation.Status.ANONYMIZED
        assert all(m.content == "[redacted]" for m in expired.messages.all())
        assert fresh.messages.first().content == "keep me"

    def test_task_is_idempotent(self, organization, active_assistant):
        Conversation.objects.create(
            organization=organization,
            assistant=active_assistant,
            retention_expires_at=timezone.now() - timedelta(days=1),
        )
        assert purge_expired_conversations() == 1
        assert purge_expired_conversations() == 0


@pytest.mark.django_db
class TestAuditRetention:
    def test_old_entries_are_deleted(self, organization):
        old = AuditLog.objects.create(action=AuditLog.Action.ADMIN_ACCESS, organization=organization)
        AuditLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=900))
        recent = AuditLog.objects.create(action=AuditLog.Action.ADMIN_ACCESS, organization=organization)

        purge_expired_audit_logs()

        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert AuditLog.objects.filter(pk=recent.pk).exists()


@pytest.mark.django_db
class TestUsageRollUp:
    def test_summary_matches_usage_records(self, organization, assistant):
        UsageRecord.objects.create(
            organization=organization, assistant=assistant, kind=UsageRecord.Kind.CHAT
        )
        UsageRecord.objects.create(
            organization=organization, assistant=assistant, kind=UsageRecord.Kind.CHAT
        )
        UsageRecord.objects.create(
            organization=organization, assistant=assistant, kind=UsageRecord.Kind.POLICY_EVALUATION
        )

        updated = roll_up_usage_summaries()
        assert updated >= 2

        chat_summary = UsageSummary.objects.get(organization=organization, metric="chat_requests")
        policy_summary = UsageSummary.objects.get(
            organization=organization, metric="policy_evaluations"
        )
        assert chat_summary.quantity == 2
        assert policy_summary.quantity == 1

    def test_roll_up_is_idempotent(self, organization):
        UsageRecord.objects.create(organization=organization, kind=UsageRecord.Kind.CHAT)
        roll_up_usage_summaries()
        roll_up_usage_summaries()
        assert UsageSummary.objects.filter(organization=organization, metric="chat_requests").count() == 1
