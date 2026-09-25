# aa-qqbot

[中文](#中文) | [English](#english)

## 中文

Alliance Auth 的 QQ 群成员管理插件。成员在 AA 的「服务」页绑定 QQ，QQ 机器人（Koishi，另一个项目）通过接口向 AA 查询谁可以留在群里。

- 已经在群里的老成员：填 QQ 号就生效
- 新成员：拿到验证码，申请入群时填在「验证信息」里
- QQ 管理员在前台管理群、绑定和冲突（侧边栏「QQ 管理」）

<img src="docs/screenshots/cards/card-dark-manager-verified.png" width="320" alt="服务页的 QQ 绑定卡片">

### 环境要求

- Alliance Auth 5.2 及以上（5.x）
- Python 3.10+

### 安装

以下命令都在 AA 的虚拟环境里、`myauth` 目录下执行。

1. 安装插件：

```bash
pip install git+https://github.com/yilifaer/aa-qqbot-plugin.git
```

2. 在 `local.py` 末尾加上：

```python
from celery.schedules import crontab

INSTALLED_APPS += ["qqbot"]
APPS_WITH_PUBLIC_VIEWS += ["qqbot"]  # 机器人接口需要；用 +=，不要覆盖

QQBOT_API_KEYS = {
    "koishi-1": "换成你的密钥",
}

CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {
    "task": "qqbot.tasks.reconcile",
    "schedule": crontab(minute="17", hour="4"),
}
```

密钥用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成。
如果 `local.py` 里还没有 `APPS_WITH_PUBLIC_VIEWS`，先加一行 `APPS_WITH_PUBLIC_VIEWS = []`。

3. 检查配置，然后建表：

```bash
python manage.py check
python manage.py migrate
```

`check` 没有 `qqbot.` 开头的提示就说明配置对了。

4. 重启 AA（例如 `sudo supervisorctl restart myauth:`）。

5. 在 Django 后台分配权限：
   - `QQ 绑定 - 成员`（`qqbot.basic_access`）→ 挂到 Member 状态
   - `QQ 绑定 - 管理员`（`qqbot.manage`）→ 挂到 QQ 管理员所在的组

完成后，成员在「服务」页能看到「QQ 绑定」卡片，QQ 管理员的侧边栏会出现「QQ 管理」。

### 升级

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/yilifaer/aa-qqbot-plugin.git
python manage.py migrate
```

然后重启 AA。更新内容见 [CHANGELOG.md](CHANGELOG.md)。

### 卸载

先停掉 AA，然后执行：

```bash
python manage.py migrate qqbot zero
python manage.py shell -c "from django_celery_beat.models import PeriodicTask; PeriodicTask.objects.filter(name='qqbot_reconcile').delete()"
pip uninstall aa-qqbot
```

最后删掉 `local.py` 里加的那几行，再启动 AA。

### 其他

- 机器人对接：[API.md](API.md)；写 Koishi 插件前先看 [KOISHI_START.md](KOISHI_START.md)。机器人需要接口地址（`https://你的AA网址/qqbot/api/v1/`）、密钥编号和密钥。
- 分步安装说明和常见问题：[docs/GUIDE.md](docs/GUIDE.md)
- 装过本仓库 0.x 旧版的：先删掉 `local.py` 里旧的 `"qqbot"`、`QQBOT_GROUP_CHAT` 和 `QQBOT_PING_GROUP`。
- AA 5.2/5.3 页面报 `sri_static` 错误：执行 `pip install "django-sri<1"` 后重启。
- 超级管理员不会自动获得入群资格，测试时请用普通成员账号。

---

## English

A QQ group membership plugin for Alliance Auth. Members bind their QQ number on AA's Services page. A QQ bot (Koishi, separate project) asks AA through an API who may stay in each group.

- Members already in a group: enter the QQ number and it takes effect
- New members: get a one-time code and put it in the QQ join request
- QQ managers handle groups, bindings and conflicts in the front end (sidebar "QQ 管理")

### Requirements

- Alliance Auth 5.2 or later (5.x)
- Python 3.10+

### Installation

Run everything inside AA's virtual environment, in the `myauth` directory.

1. Install the plugin:

```bash
pip install git+https://github.com/yilifaer/aa-qqbot-plugin.git
```

2. Add this to the end of `local.py`:

```python
from celery.schedules import crontab

INSTALLED_APPS += ["qqbot"]
APPS_WITH_PUBLIC_VIEWS += ["qqbot"]  # required by the bot API; append, do not overwrite

QQBOT_API_KEYS = {
    "koishi-1": "your-secret",
}

CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {
    "task": "qqbot.tasks.reconcile",
    "schedule": crontab(minute="17", hour="4"),
}
```

Generate the secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
If `local.py` has no `APPS_WITH_PUBLIC_VIEWS` yet, add `APPS_WITH_PUBLIC_VIEWS = []` first.

3. Check the configuration, then migrate:

```bash
python manage.py check
python manage.py migrate
```

The configuration is fine when `check` prints no messages starting with `qqbot.`.

4. Restart AA (e.g. `sudo supervisorctl restart myauth:`).

5. Assign permissions in the Django admin:
   - `qqbot.basic_access` → the Member state
   - `qqbot.manage` → your QQ managers' group

Members then see the "QQ 绑定" card on the Services page, and QQ managers get "QQ 管理" in the sidebar.

### Upgrade

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/yilifaer/aa-qqbot-plugin.git
python manage.py migrate
```

Then restart AA. See [CHANGELOG.md](CHANGELOG.md) for what changed.

### Uninstall

Stop AA first, then run:

```bash
python manage.py migrate qqbot zero
python manage.py shell -c "from django_celery_beat.models import PeriodicTask; PeriodicTask.objects.filter(name='qqbot_reconcile').delete()"
pip uninstall aa-qqbot
```

Finally, remove the lines you added to `local.py` and start AA.

### More

- Bot integration: [API.md](API.md). The bot needs the API URL (`https://your-aa-site/qqbot/api/v1/`), the key id and the secret.
- Step-by-step guide and troubleshooting (Chinese): [docs/GUIDE.md](docs/GUIDE.md)
- Upgrading from 0.x of this repo: first remove the old `"qqbot"`, `QQBOT_GROUP_CHAT` and `QQBOT_PING_GROUP` from `local.py`.
- On AA 5.2/5.3, if pages fail with a `sri_static` error, run `pip install "django-sri<1"` and restart.
- Superusers do not get group access automatically. Test with a normal member account.

## License

MIT
