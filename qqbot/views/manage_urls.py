"""QQ manager routes (included at ``/qqbot/manage/`` in namespace ``qqbot``).

QQ 管理员页面的路由（挂在 ``/qqbot/manage/`` 下，命名空间为 ``qqbot``）。
"""

from django.urls import path

from . import manage

urlpatterns = [
    path("", manage.index, name="manage_index"),
    path("groups/", manage.groups, name="manage_groups"),
    path("groups/new/", manage.group_create, name="manage_group_create"),
    path("groups/<int:pk>/", manage.group_edit, name="manage_group_edit"),
    path("groups/<int:pk>/delete/", manage.group_delete, name="manage_group_delete"),
    path("bindings/", manage.binding_list, name="manage_bindings"),
    path("bindings/<int:pk>/", manage.binding_detail, name="manage_binding"),
    path("bindings/<int:pk>/card/", manage.binding_card, name="manage_binding_card"),
    path("bindings/<int:pk>/confirm/", manage.binding_confirm, name="manage_binding_confirm"),
    path("bindings/<int:pk>/unbind/", manage.binding_unbind, name="manage_binding_unbind"),
    path("pending/", manage.pending, name="manage_pending"),
    path("settings/", manage.settings_view, name="manage_settings"),
    path("audit/", manage.audit_list, name="manage_audit"),
]
