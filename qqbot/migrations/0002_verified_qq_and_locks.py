"""Portable "one owner per verified QQ" guarantee and lock rows.

* ``Binding.verified_qq`` (unique, NULL unless verified) replaces the
  conditional unique constraint ``qqbot_unique_verified_qq``, which Django
  silently skips on MySQL/MariaDB (no partial indexes there).
* ``Lock`` rows back ``qqbot.core.locks`` (per-user / per-QQ serialization).
"""

from django.db import migrations, models
from django.db.models import F, Q

# Must match USER_STRIPES + QQ_STRIPES in qqbot/core/locks.py.
LOCK_ROWS = 128


def backfill_verified_qq(apps, schema_editor):
    Binding = apps.get_model("qqbot", "Binding")
    seen = set()
    for b in Binding.objects.filter(status="verified").order_by("verified_at", "pk"):
        if b.qq in seen:
            # Only possible on MySQL/MariaDB, where the old constraint did not
            # exist: keep the first owner, turn the others into a conflict
            # for managers to resolve (conflicts never get anybody kicked).
            Binding.objects.filter(pk=b.pk).update(status="trusted", verified_via="")
            continue
        seen.add(b.qq)
        Binding.objects.filter(pk=b.pk).update(verified_qq=b.qq)


def create_lock_rows(apps, schema_editor):
    Lock = apps.get_model("qqbot", "Lock")
    Lock.objects.bulk_create([Lock(pk=k) for k in range(LOCK_ROWS)], ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("qqbot", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="binding",
            name="verified_qq",
            field=models.CharField(
                blank=True, editable=False, max_length=11, null=True, unique=True
            ),
        ),
        migrations.RunPython(backfill_verified_qq, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="binding",
            name="qqbot_unique_verified_qq",
        ),
        migrations.AddConstraint(
            model_name="binding",
            constraint=models.CheckConstraint(
                condition=(
                    Q(status="verified", verified_qq=F("qq"))
                    | (~Q(status="verified") & Q(verified_qq__isnull=True))
                ),
                name="qqbot_verified_qq_in_sync",
            ),
        ),
        migrations.CreateModel(
            name="Lock",
            fields=[
                ("id", models.PositiveIntegerField(primary_key=True, serialize=False)),
            ],
            options={
                "default_permissions": (),
            },
        ),
        migrations.RunPython(create_lock_rows, migrations.RunPython.noop),
    ]
