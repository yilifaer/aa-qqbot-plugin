# QQBot Binding for Alliance Auth
# Alliance Auth 的 QQ 绑定插件

Simple AA plugin to let members submit QQ number & nickname and store with main character & corp ticket.
让成员提交 QQ 号与昵称，并与主角色与军团 ticket 一起保存。

-------------------------------------------------

## 1) Install
## 1）安装

Copy the `qqbot` folder into your AA project (beside `myauth/`).
把 `qqbot` 文件夹复制到 AA 工程目录（与 `myauth/` 同级或在 `myauth/` 内部都可，只要能在 `INSTALLED_APPS` 引用到）。

Make sure your virtualenv is activated, then install dependencies if any (none extra needed now).
确保已激活虚拟环境，目前无需额外依赖。

-------------------------------------------------

## 2) Add to settings
## 2）写入设置

Edit your `myauth/myauth/settings/local.py`.
编辑 `myauth/myauth/settings/local.py`。

INSTALLED_APPS += ["qqbot"]

# show group numbers after successful binding
QQBOT_GROUP_CHAT = "12345678"
QQBOT_PING_GROUP = "87654321"

Tip: ship `myauth/myauth/settings/local.example.py` for others to copy and fill.
建议附带 `myauth/myauth/settings/local.example.py` 让他人复制改名为 local.py 并填群号。

-------------------------------------------------

## 3) Wire up URLs
## 3）路由注册

Edit `myauth/myauth/urls.py`.
编辑 `myauth/myauth/urls.py`。

from django.urls import path, include

urlpatterns = [
    path("qqbot/", include("qqbot.urls")),
]

Visit:
访问地址：

- /qqbot/bind/  – member submit page
- /qqbot/bind/  – 成员提交页
- /qqbot/admin/ – staff-only listing
- /qqbot/admin/ – 仅 staff 可见的列表页

-------------------------------------------------

## 4) Migrate & Run
## 4）迁移并运行

cd <your aa project root>
python .\myauth\manage.py makemigrations qqbot
python .\myauth\manage.py migrate
python .\myauth\manage.py runserver

Open http://127.0.0.1:8000/qqbot/bind/ to test.
打开 http://127.0.0.1:8000/qqbot/bind/ 进行测试。

-------------------------------------------------

## 5) Permissions
## 5）权限

/qqbot/admin/ requires is_staff. Superusers are allowed by default.
/qqbot/admin/ 需 is_staff，超级用户默认可访问。

In Django Admin, set user as staff (Users -> user -> Staff status).
在 Django 管理后台把需要的用户勾为 Staff status。

-------------------------------------------------

## 6) Uninstall
## 6）卸载

- Remove from INSTALLED_APPS and URL include.
  从 INSTALLED_APPS 与路由中移除。
- Optionally drop table qqbot_qqbinding.
  如需彻底清理可删除表 qqbot_qqbinding。

-------------------------------------------------

## 7) Troubleshooting
## 7）排错

- Redis warnings in dev can be ignored (cache backend is set to ignore connection errors).
  开发环境的 Redis 报错可忽略（缓存已设置忽略连接错误）。
- If /admin/ 404, ensure default admin route still included by AA or add path("admin/", admin.site.urls) yourself.
  若 /admin/ 404，请确认 AA 的后台路由未被覆盖，或自行添加 path("admin/", admin.site.urls)。

-------------------------------------------------

## 8) License
## 8）许可协议

MIT License (see LICENSE file).
MIT 协议（见 LICENSE 文件）。
