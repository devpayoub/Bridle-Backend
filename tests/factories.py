"""Test factories (factory_boy) for domain objects."""
from __future__ import annotations

import factory

from apps.accounts.models import User
from apps.assistants.models import Assistant
from apps.knowledge.models import KnowledgeBase
from apps.organizations.models import Organization, OrganizationMembership

TEST_PASSWORD = "Str0ng-Passw0rd!2026"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("email",)

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = "Test User"

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        password = kwargs.pop("password", TEST_PASSWORD)
        return model_class.objects.create_user(*args, password=password, **kwargs)


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Organization {n}")
    slug = factory.Sequence(lambda n: f"organization-{n}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrganizationMembership

    organization = factory.SubFactory(OrganizationFactory)
    user = factory.SubFactory(UserFactory)
    role = OrganizationMembership.Role.MEMBER


class AssistantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Assistant

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Assistant {n}")
    slug = factory.Sequence(lambda n: f"assistant-{n}")
    domain_type = Assistant.DomainType.HOTEL
    allowed_topics = factory.LazyFunction(
        lambda: [
            "check-in times|checkin|arrival time",
            "room service",
            "reservations|booking",
            "pet policy|pets|dogs|cats",
        ]
    )
    prohibited_topics = factory.LazyFunction(lambda: ["medical advice", "legal advice"])
    system_instructions = "Answer politely and concisely using approved hotel information."
    fallback_message = "I can only help with questions about our hotel."
    model_provider = "mock"
    model_name = "mock-model-1"
    status = Assistant.Status.DRAFT


class KnowledgeBaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = KnowledgeBase

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Knowledge base {n}")
    slug = factory.Sequence(lambda n: f"knowledge-base-{n}")
