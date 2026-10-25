"""Organization and membership API tests."""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.billing.models import Subscription
from apps.organizations.models import Organization, OrganizationMembership
from tests.factories import UserFactory

ORGS_URL = "/api/v1/organizations/"


def members_url(org_id) -> str:
    return f"/api/v1/organizations/{org_id}/members/"


def member_url(org_id, user_id) -> str:
    return f"/api/v1/organizations/{org_id}/members/{user_id}/"


@pytest.mark.django_db
class TestOrganizationCrud:
    def test_create_organization_makes_owner_and_subscription(self, auth_client, user):
        response = auth_client.post(ORGS_URL, {"name": "Beach Resort"}, format="json")
        assert response.status_code == 201
        organization = Organization.objects.get(id=response.data["id"])
        membership = OrganizationMembership.objects.get(organization=organization, user=user)
        assert membership.role == OrganizationMembership.Role.OWNER
        assert Subscription.objects.filter(organization=organization).exists()
        assert response.data["role"] == "OWNER"
        assert response.data["plan_code"] == "free"

    def test_list_only_returns_own_organizations(self, auth_client, organization, other_organization):
        response = auth_client.get(ORGS_URL)
        assert response.status_code == 200
        ids = {row["id"] for row in response.data["results"]}
        assert str(organization.id) in ids
        assert str(other_organization.id) not in ids

    def test_retrieve_own_organization(self, auth_client, organization):
        response = auth_client.get(f"{ORGS_URL}{organization.id}/")
        assert response.status_code == 200
        assert response.data["name"] == organization.name

    def test_retrieve_foreign_organization_is_404(self, auth_client, other_organization):
        response = auth_client.get(f"{ORGS_URL}{other_organization.id}/")
        assert response.status_code == 404

    def test_update_organization(self, auth_client, organization):
        response = auth_client.patch(
            f"{ORGS_URL}{organization.id}/", {"name": "Renamed Hotel"}, format="json"
        )
        assert response.status_code == 200
        organization.refresh_from_db()
        assert organization.name == "Renamed Hotel"

    def test_api_key_cannot_create_organization(self, api_key_client):
        response = api_key_client.post(ORGS_URL, {"name": "Nope"}, format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestMemberships:
    def test_owner_can_add_existing_user(self, auth_client, organization):
        new_user = UserFactory(email="dev@example.com")
        response = auth_client.post(
            members_url(organization.id), {"email": new_user.email, "role": "DEVELOPER"}, format="json"
        )
        assert response.status_code == 201
        assert response.data["role"] == "DEVELOPER"
        assert OrganizationMembership.objects.filter(organization=organization, user=new_user).exists()

    def test_unknown_email_rejected(self, auth_client, organization):
        response = auth_client.post(
            members_url(organization.id), {"email": "ghost@example.com", "role": "MEMBER"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "user_not_found"

    def test_duplicate_member_rejected(self, auth_client, organization, user):
        response = auth_client.post(
            members_url(organization.id), {"email": user.email, "role": "MEMBER"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "already_member"

    def test_cannot_assign_owner_via_member_create(self, auth_client, organization):
        new_user = UserFactory(email="wannabe@example.com")
        response = auth_client.post(
            members_url(organization.id), {"email": new_user.email, "role": "OWNER"}, format="json"
        )
        assert response.status_code == 400

    def test_read_only_member_cannot_add_members(self, organization, membership_factory):
        membership, read_only_user = membership_factory(organization, role="READ_ONLY")
        client = APIClient()
        client.force_authenticate(user=read_only_user)
        client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))

        response = client.post(
            members_url(organization.id), {"email": "x@example.com", "role": "MEMBER"}, format="json"
        )
        assert response.status_code == 403

    def test_last_owner_cannot_be_demoted(self, auth_client, organization, user):
        response = auth_client.patch(
            member_url(organization.id, user.id), {"role": "ADMIN"}, format="json"
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "last_owner"

    def test_owner_can_change_role(self, auth_client, organization, membership_factory):
        membership, member_user = membership_factory(organization, role="MEMBER")
        response = auth_client.patch(
            member_url(organization.id, member_user.id), {"role": "DEVELOPER"}, format="json"
        )
        assert response.status_code == 200
        assert response.data["role"] == "DEVELOPER"

    def test_remove_member(self, auth_client, organization, membership_factory):
        membership, member_user = membership_factory(organization, role="MEMBER")
        response = auth_client.delete(member_url(organization.id, member_user.id))
        assert response.status_code == 204
        assert not OrganizationMembership.objects.filter(pk=membership.pk).exists()

    def test_member_limit_enforced(self, auth_client, organization):
        # Free plan allows 3 members including the owner.
        UserFactory(email="m1@example.com")
        UserFactory(email="m2@example.com")
        auth_client.post(members_url(organization.id), {"email": "m1@example.com", "role": "MEMBER"}, format="json")
        auth_client.post(members_url(organization.id), {"email": "m2@example.com", "role": "MEMBER"}, format="json")
        UserFactory(email="m3@example.com")
        response = auth_client.post(
            members_url(organization.id), {"email": "m3@example.com", "role": "MEMBER"}, format="json"
        )
        assert response.status_code == 403
        assert response.data["error"]["code"] == "entitlement_exceeded"
