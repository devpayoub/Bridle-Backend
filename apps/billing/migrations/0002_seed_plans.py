"""Seed the default subscription plans."""
from django.db import migrations


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    from apps.billing.defaults import DEFAULT_PLANS

    for spec in DEFAULT_PLANS:
        Plan.objects.update_or_create(code=spec["code"], defaults=spec)


def unseed_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    from apps.billing.defaults import DEFAULT_PLANS

    Plan.objects.filter(code__in=[spec["code"] for spec in DEFAULT_PLANS]).delete()


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]

    operations = [migrations.RunPython(seed_plans, unseed_plans)]
