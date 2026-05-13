# aa-qqbot

Alliance Auth 的 QQ 绑定插件 / AllianceAuth plugin for QQ binding

让成员提交 QQ 号、昵称，并与主角色和军团 ticket 一起保存。绑定页会显示 IGCCN 群号。

---

## 功能 / Features

- ✅ 用户绑定页（侧边栏菜单入口）
- ✅ 自动从 AA 主角色读取 character_id / character_name / corp ticker
- ✅ 管理员列表页（基于 Django 权限）
- ✅ Django Admin 集成
- ✅ JSON API 接口（外部 bot 调用）
- ✅ 兼容 Alliance Auth v4 / v5

---

## 安装 / Install

```bash
pip install git+https://github.com/yilifaer/aa-qqbot-plugin.git
```

或在 `requirements.txt` 中加入：

```
git+https://github.com/yilifaer/aa-qqbot-plugin.git
```

---

## 配置 / Configuration

编辑 `myauth/myauth/settings/local.py`：

```python
INSTALLED_APPS += [
    "qqbot",
]

# 可选：绑定成功后显示的 QQ 群号
QQBOT_GROUP_CHAT = "12345678"   # IGCCN-QQ 聊天群
QQBOT_PING_GROUP = "87654321"   # IGCCN-Ping 群
```

> 💡 不需要手动改 `urls.py`——插件通过 AA 的 `UrlHook` 自动注册。

---

## 迁移数据库 / Migrate

```bash
python manage.py migrate qqbot
python manage.py collectstatic --noinput
```

重启 gunicorn 和 celery worker。

---

## 访问页面 / Access

- **`/qqbot/`** — 用户绑定页（所有已登录用户可见，侧边栏菜单"QQ 绑定"）
- **`/qqbot/manage/`** — 管理员列表页（需 `qqbot.view_qqbinding` 权限）
- **`/qqbot/api/bind/`** — JSON API 接口（POST/GET 提交 qq、nickname、ticket）
- **`/qqbot/ping/`** — 健康检查（返回 `pong`）

---

## 权限 / Permissions

`/qqbot/manage/` 需要 `qqbot.view_qqbinding` 权限。在 Django Admin → 用户管理中授予对应用户/组。

---

## 卸载 / Uninstall

```python
# 从 INSTALLED_APPS 移除 "qqbot"
```

```bash
pip uninstall aa-qqbot
python manage.py migrate qqbot zero  # 可选：删除数据库表
```

---

## License

MIT — see [LICENSE](LICENSE)
