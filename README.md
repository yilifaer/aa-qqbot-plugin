# aa-qqbot

English | [简体中文](README.zh-CN.md)

A QQ group membership plugin for Alliance Auth. Members bind their QQ number on AA's Services page. A QQ bot (Koishi, a separate project) asks AA's API who may join and stay in each group.

- Members already in a group: enter your QQ number, no code needed
- New members: get a one-time code and put it in the QQ join request
- QQ managers handle groups, bindings and conflicts on the "QQ 管理" page in the sidebar

<img src="docs/screenshots/cards/card-dark-manager-verified.png" width="320" alt="QQ binding card on the Services page">

## Requirements

- Alliance Auth 5.x (5.2 or later)
- Python 3.10+

## Installation

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

5. Assign permissions in the Django admin. The permission picker shows Chinese names, so search for those:
   - `QQ 绑定 - 成员` (`qqbot.basic_access`) → the Member state
   - `QQ 绑定 - 管理员` (`qqbot.manage`) → your QQ managers' group

Members then see the "QQ 绑定" card on the Services page, and QQ managers get "QQ 管理" in the sidebar. A QQ manager then adds the groups under "QQ 管理" → "QQ 群".

## Upgrade

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/yilifaer/aa-qqbot-plugin.git
python manage.py migrate
```

Then restart AA. See [CHANGELOG.md](CHANGELOG.md) for what changed.

## Uninstall

Stop AA, then run (this deletes all bindings and the audit log):

```bash
python manage.py migrate qqbot zero
python manage.py shell -c "from django_celery_beat.models import PeriodicTask; PeriodicTask.objects.filter(name='qqbot_reconcile').delete()"
```

Remove the lines you added to `local.py`, then:

```bash
pip uninstall aa-qqbot
python manage.py remove_stale_contenttypes --include-stale-apps
```

Start AA.

## More

- Bot integration: [API.md](API.md); read [KOISHI_START.md](KOISHI_START.md) before writing the Koishi plugin (both in Chinese). The bot needs the API URL (`https://your-aa-site/qqbot/api/v1/`), the key id and the secret.
- Step-by-step guide and troubleshooting (Chinese): [docs/GUIDE.md](docs/GUIDE.md)
- If this site ran 0.x of this plugin: before step 2, remove the old `"qqbot"`, `QQBOT_GROUP_CHAT` and `QQBOT_PING_GROUP` from `local.py`. Old bindings are not imported; everyone binds again.
- On AA 5.2/5.3, if every page fails with an error mentioning `sri_static`, run `pip install "django-sri<1"` and restart.
- Superusers do not get group access automatically. Test with a normal member account.

## License

MIT
