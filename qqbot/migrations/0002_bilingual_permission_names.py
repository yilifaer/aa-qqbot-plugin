"""Bilingual permission names (English first) so IT can search them in English.

Django only creates missing permissions after ``migrate``; it never renames
existing ones. Sites that already ran 1.0.0b1/b2 therefore get their two
permission rows renamed here.

权限名改为中英双语（英文在前），方便 IT 用英文搜索。
Django 在 ``migrate`` 之后只会创建缺少的权限，不会改已有权限的名字，
所以已经装过 1.0.0b1/b2 的站点在这里把这两个权限改名。
"""

from django.db import migrations

NEW_NAMES = {
    "basic_access": "QQ binding: member, can bind own QQ / QQ 绑定 - 成员：可以绑定自己的 QQ",
    "manage": (
        "QQ binding: manager, can manage QQ groups and bindings"
        " / QQ 绑定 - 管理员：可以在前台管理 QQ 群与绑定"
    ),
}
OLD_NAMES = {
    "basic_access": "QQ 绑定 - 成员：可以绑定自己的 QQ",
    "manage": "QQ 绑定 - 管理员：可以在前台管理 QQ 群与绑定",
}


def _rename(apps, names):
    Permission = apps.get_model("auth", "Permission")
    for codename, name in names.items():
        Permission.objects.filter(
            content_type__app_label="qqbot",
            content_type__model="general",
            codename=codename,
        ).update(name=name)


def forwards(apps, schema_editor):
    _rename(apps, NEW_NAMES)


def backwards(apps, schema_editor):
    _rename(apps, OLD_NAMES)


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("qqbot", "0001_v1_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="general",
            options={
                "default_permissions": (),
                "managed": False,
                "permissions": tuple(NEW_NAMES.items()),
            },
        ),
        migrations.RunPython(forwards, backwards),
    ]
