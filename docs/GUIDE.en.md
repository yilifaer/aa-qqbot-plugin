# aa-qqbot step-by-step installation guide

English | [简体中文](GUIDE.md)

> The short installation steps are in the [README](../README.md). This guide is for people installing the plugin for the first time: step-by-step instructions and common problems.
> The links to `API.md`, `CHANGELOG.md`, `DECISIONS.md`, `DESIGN.md`, `KOISHI_START.md`, `docs/SPEC.md` and `docs/TESTING.md` lead to documents that are only available in Chinese.

A QQ binding plugin for [Alliance Auth](https://gitlab.com/allianceauth/allianceauth) (AA):
**only people AA recognises may stay in the alliance's QQ groups.**

- Members bind their own QQ number in AA;
- The QQ bot (the Koishi plugin [koishi-plugin-aaqqbot](https://github.com/yilifaer/Koishi-AAqqbot-plugin), in a separate repository) asks AA: "May this QQ join this group? What should the group nickname be?";
- When a member leaves the alliance, an account is deactivated or a member switches main character, AA records it, and the bot learns about it the next time it fetches.

Current version: **1.1.0**. What changed is listed in [`CHANGELOG.md`](../CHANGELOG.md).
Requires Alliance Auth 5.2 to 5.5 (every version is covered by automated tests) and Python 3.10 or newer.

---

## Contents

1. [What it does](#1-what-it-does)
2. [Installation (for IT)](#2-installation-for-it)
3. [Bot keys: rotation and safekeeping](#3-bot-keys-rotation-and-safekeeping)
4. [Assigning permissions](#4-assigning-permissions)
5. [Where the pages are](#5-where-the-pages-are)
6. [Connecting the bot](#6-connecting-the-bot)
7. [Upgrading](#7-upgrading)
8. [Uninstalling](#8-uninstalling)
9. [Common problems](#9-common-problems)
10. [Security notes](#10-security-notes)
11. [Data protection](#11-data-protection)
12. [Development and testing](#12-development-and-testing)

After installing, click through every item on a Raspberry Pi test AA: [`docs/TESTING.md` (Raspberry Pi test checklist)](TESTING.md).

---

## 1. What it does

**Members** (people with the "QQ binding: member" permission):

- Everything is done in the "QQ binding" card on AA's "Services" page; no other page is needed;
- Enter your own QQ number and nickname:
  - **Existing members who are already in the group**: it takes effect right away after you submit, no verification needed;
  - **New members**: you get a verification code (for example `QQ-7KXM2P`); put it in the "verification message" when you request to join the group, and the bot approves the request automatically;
- See the groups you are allowed to join;
- Change your nickname, change QQ or unbind in the card (click the buttons at the bottom of the card; a small form expands in place).

**QQ admins** (people with the "QQ binding: manager" permission) get an extra "QQ Admin" entry in the left sidebar. Everything is done on the web front end; there is no need to go into the Django admin:

- Add, edit and disable QQ groups, either a "Fixed group" (every member can join) or a "Role group" (only people in the AA groups you pick can join);
- View all bindings, change someone's group nickname, confirm a binding, force an unbind;
- Handle "Conflict"s (the same QQ claimed by two accounts) and the QQ numbers in the "QQ numbers in a group but not bound" list under "Needs attention";
- Under "Needs attention", review the bindings made without a code in the last 30 days (or the number of days of the trusted binding window, if the "Trusted binding window (days)" in "Settings" is set longer than 30 days). If you find someone claiming another person's QQ, you can force-unbind it directly (after a force unbind, this person must use a verification code to bind the same QQ again, and only the real owner can do that);
- Change settings such as the group rules, the group nickname format, the verification code lifetime, the member list max age and the change QQ cooldown;
- View the audit log.

**The bot** (Koishi) asks AA through a signed API; the API is described in [`API.md`](../API.md).
The switch for "actually kick people out of the group" exists only on the bot's side. It cannot be turned on from AA's web pages, to prevent mistakes.

> AA's interface is English by default and the left sidebar shows "Services". You can switch to Simplified Chinese with the language selector at the bottom of the left sidebar (the option is shown as "简体中文 (zh-hans)").
> The QQ binding card and the "QQ Admin" pages follow this language: English when English is selected (the card is titled "QQ binding", the menu entry is "QQ Admin"),
> Chinese when a Chinese language is selected, and English for any other language. See the end of section 5.

### Screenshots (demo data)

The screenshots show the Simplified Chinese interface. The English pages have the same layout; English card screenshots are in [`docs/screenshots/cards/`](screenshots/cards/) (`card-en-*.png`).

| QQ binding card on the Services page | Card: before binding | Card: new member waiting for verification |
|---|---|---|
| ![Services page card](screenshots/01-services-card.png) | ![Before binding](screenshots/02-member-unbound.png) | ![Waiting for verification](screenshots/03-member-pending.png) |

| Card: bound | Card: conflict | Admin: bindings |
|---|---|---|
| ![Bound](screenshots/04-member-bound.png) | ![Conflict](screenshots/06-member-conflict.png) | ![Bindings](screenshots/07-manage-bindings.png) |

| Admin: QQ groups | Admin: needs attention | Admin: settings |
|---|---|---|
| ![QQ groups](screenshots/08-manage-groups.png) | ![Needs attention](screenshots/10-manage-pending.png) | ![Settings](screenshots/11-manage-settings.png) |

The card in the dark theme (Darkly):

| Not bound | Waiting for verification | Bound (admin) | Conflict |
|---|---|---|---|
| ![Dark: not bound](screenshots/cards/card-dark-unbound.png) | ![Dark: waiting for verification](screenshots/cards/card-dark-pending.png) | ![Dark: bound](screenshots/cards/card-dark-manager-verified.png) | ![Dark: conflict](screenshots/cards/card-dark-conflict.png) |

All screenshots are in [`docs/screenshots/`](screenshots/) (the cards in the light and dark themes are in [`docs/screenshots/cards/`](screenshots/cards/)).
The group numbers, QQ numbers and characters in the screenshots are made-up demo data.

---

## 2. Installation (for IT)

The steps below use the **default directories from AA's official installation guide** as the example:

| Item | Official default | Yours (replace if different) |
|---|---|---|
| Linux user that runs AA | `allianceserver` | |
| Python virtual environment | `/home/allianceserver/venv/auth` | |
| AA project directory (where `manage.py` is) | `/home/allianceserver/myauth` | |
| Configuration file | `/home/allianceserver/myauth/myauth/settings/local.py` | |
| AA's process group in supervisor | `myauth` | |

For AA installed with Docker, see the end of this section.

### Preparation

- A working **Alliance Auth 5.2 to 5.5**, with Redis, the celery worker and celery beat all running normally.
- `git` must be installed on the server: run `git --version`; if it says the command is not found, run `sudo apt install git` from an account with sudo rights.
- You do **not** need to install any other apt packages: the plugin depends only on AA itself (`mysqlclient` and the like were installed along with AA).
- **Back up the database first.** The official installation uses MariaDB/MySQL, for example (take the database name and user name from the `DATABASES` entry in `local.py`):

  ```bash
  mysqldump -u allianceserver -p alliance_auth > ~/aa-backup-$(date +%F).sql
  ```

  MariaDB/MySQL installed on Ubuntu can be used without a password (root uses socket authentication; `--single-transaction` does not lock the tables during the backup):

  ```bash
  sudo mysqldump --single-transaction alliance_auth > ~/aa-backup-$(date +%F).sql
  ```

### Step 0: Enter AA's virtual environment and check the versions

Log in to the server over SSH and run:

```bash
sudo su allianceserver
source /home/allianceserver/venv/auth/bin/activate
cd /home/allianceserver/myauth
```

When `(auth)` appears at the start of the command line, you are inside the virtual environment. **The `pip` and `python manage.py …` commands in steps 1–5 are all run here.**
`sudo supervisorctl …` needs an account with sudo rights (`allianceserver` usually has none); at step 6, type `exit` first to go back to your original account, then run it.

Check AA's version:

```bash
pip show allianceauth django-sri django-esi aa-qqbot | grep -E '^(Name|Version)'
```

(If the plugin has never been installed, there is an extra line `WARNING: Package(s) not found: aa-qqbot`; this is normal.)

- `allianceauth` must be **5.2 to 5.5**. If it is 4.x (or 5.0, 5.1), **do not install the plugin yet**:
  pip would upgrade the whole of AA to the latest 5.x along the way, and a major AA upgrade has its own procedure. Upgrade AA first, following AA's official documentation.
- If `allianceauth` is 5.2.x or 5.3.x and `django-sri` is `1.x`, first run `pip install "django-sri<1"` (for the reason, see [Common problems](#9-common-problems)).
- If `allianceauth` is 5.2.x to 5.4.x and `django-esi` is `10.x`, first run `pip install "django-esi<10"` (again, see [Common problems](#9-common-problems) for the reason).
- If `aa-qqbot` is shown with a **0.x** version (for example `0.2.0`): this AA had an old version of this repository installed (the one with only a QQ number + nickname).
  You can go ahead and install; the new version creates its own tables. The old version's bindings are not imported (decision #5), so everyone binds again.
  **Before step 3**, remove what the old version added to `local.py`: `"qqbot"` in `INSTALLED_APPS` (otherwise you get
  `Application labels aren't unique, duplicates: qqbot`), and the two lines `QQBOT_GROUP_CHAT` and `QQBOT_PING_GROUP`.
  After installing, clean up the old permissions and the old table using the "installed an old version before" row in [Common problems](#9-common-problems).

### Step 1: Install the plugin

```bash
pip install git+https://github.com/yilifaer/aa-qqbot-plugin.git@v1.1.0
```

- If the last line says `Successfully installed aa-qqbot-1.1.0`, it is installed (other packages may be listed too; they are dependencies AA was missing).
- If it says `Cannot find command 'git'`: install git first (see "Preparation"), or use this form, which does not need git:
  `pip install https://github.com/yilifaer/aa-qqbot-plugin/archive/refs/tags/v1.1.0.zip`
- Note: the package is named `aa-qqbot` and can only be installed from the GitHub address above. **Do not** run `pip install qqbot`:
  `qqbot` on PyPI is an unrelated project and would overwrite this plugin's code.

### Step 2: Generate the bot key

Run this on the server (inside the virtual environment) or on your own computer:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

It prints a random string of **64 characters** (on Windows, if it says python is not found, replace `python` with `py`).
In the next step you put it into `local.py`, replacing `your-secret`. The same key must later also be given to the bot (section 6).
**Do not** post it in a group chat, a chat log, a screenshot or any repository.

### Step 3: Edit `local.py`

Open the configuration file in an editor, for example:

```bash
nano /home/allianceserver/myauth/myauth/settings/local.py
```

Paste the following at the very **end** of the file (in nano, press Ctrl+O and Enter to save, then Ctrl+X to exit):

```python
# ---------- aa-qqbot ----------
INSTALLED_APPS += ["qqbot"]

# The bot API does not use AA's login; it uses signature authentication, so qqbot must be added to the "public views" list.
# Note: you must use += (append), not = (overwrite), otherwise the public pages of other plugins stop working.
# If local.py does not have APPS_WITH_PUBLIC_VIEWS yet, first write one line: APPS_WITH_PUBLIC_VIEWS = []
APPS_WITH_PUBLIC_VIEWS += ["qqbot"]

# Bot communication keys: {"key id": "key"}. Use the string generated in step 2 as the key.
# A key id may only contain English letters, digits and symbols such as - _ . (no spaces, no Chinese, at most 64 characters), for example "koishi-1".
# You can write several keys at once; each one is valid, which lets you rotate keys without downtime (see section 3).
QQBOT_API_KEYS = {
    "koishi-1": "your-secret",
}

# Reconcile once a day (re-check everyone's eligibility and group nickname, and clean up old data).
# local.py usually already starts with from .base import *, so crontab can be used directly;
# if it says crontab cannot be found, add one more line: from celery.schedules import crontab
CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {
    "task": "qqbot.tasks.reconcile",
    "schedule": crontab(minute="17", hour="4"),  # every day at 04:17 (UTC, which is 12:17 Beijing time)
}
# ---------- aa-qqbot end ----------
```

> ⚠️ Once more: `APPS_WITH_PUBLIC_VIEWS` must be **appended to**. If you miss this step, the bot's requests are treated as
> "not logged in" and redirected to the login page, so the bot cannot work; the check in step 4 reports it directly.

Optional settings (normally you do not need to change them; if you do, write them inside the block above):

| Setting | Default | Description |
|---|---|---|
| `QQBOT_API_RATE_LIMIT` | `120` | How many requests each key may make per minute; `0` means no limit |
| `QQBOT_API_MAX_SKEW` | `300` | How many seconds the bot's clock and AA's clock may differ |
| `QQBOT_API_MAX_BODY` | `262144` | Maximum size of one request in bytes (256 KB) |
| `QQBOT_AUDIT_RETENTION_DAYS` | `0` | How many days the audit log is kept; the daily reconcile deletes older entries; `0` means keep forever (since 1.1.0). The minimum is 30 days; 1–29 is treated as 30, because the change QQ cooldown reads the audit log; the restrictions recorded by deleted entries (for example, having to use a verification code after being force-unbound) lapse with them. See section 11 |

### Step 4: Check the configuration

```bash
python manage.py check
```

Look only at the messages that start with `qqbot.`. The output may also contain messages from AA itself or other plugins (for example `allianceauth.checks.A002`, which reminds you to upgrade Redis);
they have nothing to do with this plugin. **If there are no messages starting with `qqbot.`, the configuration is good.**

When there is a `qqbot.E…` error, commands such as `migrate` also refuse to run (with a `SystemCheckError`). Fix `local.py` using the table below first, then run `check` again:

| Code | Meaning | How to fix |
|---|---|---|
| `qqbot.E001` | `"qqbot"` is not in `APPS_WITH_PUBLIC_VIEWS` | **Append** it as in step 3 |
| `qqbot.E002` | `QQBOT_API_KEYS` is not set, or is not a `{...}` dictionary | Configure it as in step 3 |
| `qqbot.E003` | A key is too short (fewer than 32 characters). Most common cause: you forgot to replace `your-secret` with the real key | Put in the key generated in step 2 |
| `qqbot.E004` | A key id has a bad format (spaces, Chinese characters, or more than 64 characters), so requests the bot sends with it will always be refused | Change it to an English name like `koishi-1`, and change it on the bot's side too |
| `qqbot.W001` | The cache is not a cache shared between processes, such as Redis. AA itself requires a Redis cache, so this should hardly ever appear | Check whether `local.py` changed `CACHES` |
| `qqbot.W002` | There is no daily reconcile task in `CELERYBEAT_SCHEDULE`, so the daily reconcile does not run automatically | Add the `CELERYBEAT_SCHEDULE["qqbot_reconcile"]` lines from step 3. If you added it by hand under "Periodic tasks" in the admin, you can ignore this |
| `qqbot.W003` | `QQBOT_AUDIT_RETENTION_DAYS` is neither 0 nor an integer of at least 30 (since 1.1.0). 1–29 is treated as 30 days; a value that is not an integer (for example the string `"365"`) or is below 0 is ignored, and the audit log is kept forever | Change it to `0` (keep forever) or an integer of at least 30, for example `365`; if you do not plan to limit how long it is kept, delete the line |

### Step 5: Create the database tables

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

The output of `migrate` should contain `Applying qqbot.0001_v1_initial... OK`.
The plugin has no static files of its own; `collectstatic` is run simply because it is the usual practice for AA plugins.

### Step 6: Restart AA

```bash
exit                                  # go back to the account that has sudo rights
sudo supervisorctl restart myauth:
sudo supervisorctl status             # every line should say RUNNING
```

The `myauth:` group usually contains the celery worker and celery beat, and may also contain gunicorn. Look at the output of `status`:
if gunicorn (the web process) is on a line of its own whose name does not start with `myauth:`, restart it separately too, for example `sudo supervisorctl restart gunicorn`
(use the name shown in `status`).

### Step 7: Check that it works

1. In the virtual environment from step 0, run `python manage.py check`; there should be no messages starting with `qqbot.`.
2. On any computer that can reach AA, run this (replace the address with your AA address):

   ```bash
   curl -i -X POST https://auth.example.com/qqbot/api/v1/health/
   ```

   `401` in the first line and `{"ok": false, "error": "missing_headers", …}` on the last line → **normal** (of course a request without a signature is refused).
   - If you see `302` or a login page → `APPS_WITH_PUBLIC_VIEWS` is not set correctly (`qqbot.E001`);
   - If you see a `400` page → the address you used is not in `ALLOWED_HOSTS` (see [Common problems](#9-common-problems));
   - If you see a `404` page → the plugin is not installed or AA was not restarted.
3. Make one signed call yourself with the key on the server (the key is not displayed). Go back to the virtual environment and directory from step 0, run `python manage.py shell`,
   paste the following and press Enter twice:

   ```python
   import json, secrets
   from urllib.parse import urlparse
   from django.conf import settings
   from django.test import Client
   from qqbot.api.signing import signed_headers

   def bot(name, payload=None):
       path = f"/qqbot/api/v1/{name}/"
       body = json.dumps(payload or {}, ensure_ascii=False).encode()
       key_id, secret = next(iter(settings.QQBOT_API_KEYS.items()))
       h = signed_headers(key_id, secret, path, body, nonce=secrets.token_hex(16))
       c = Client(HTTP_HOST=urlparse(settings.SITE_URL).hostname)
       r = c.post(path, data=body, content_type="application/json", secure=settings.SITE_URL.startswith("https"), **{"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items()})
       print(r.status_code, r.content.decode()[:500])

   bot("health")
   ```

   You should see `200 {"ok": true, … "config_ok": true, "problems": []}`. Type `exit()` to leave.
   (Before that line, a few lines of AA's own log output may be printed first, for example `Registering hook ...` and `objects imported automatically`; this is normal. Look only at the last line, the one that starts with `200`.)
   (This `bot` function is a "simulated bot". You can use it for testing on the Raspberry Pi while there is no Koishi yet; see [`docs/TESTING.md`](TESTING.md).)
4. `sudo supervisorctl status` shows everything as `RUNNING`; in the Django admin `/admin/` → "Periodic tasks" there is a `qqbot_reconcile`
   (it only appears after celery beat has restarted).
5. After assigning permissions as in section 4, open "Services" in the left sidebar with an **ordinary member account** (not a superuser) and you should see the "QQ binding" card;
   a QQ admin's left sidebar has "QQ Admin".
6. A QQ admin opens "QQ Admin" → "QQ groups" and adds the first group.
7. Wait until the bot's first check has succeeded (in "QQ Admin" → "QQ groups", the group's "Member list" column shows a member count and the time of the latest report), and only then tell existing members to bind.
   Existing members who bind before that all get a verification code (since 1.0.0b5 the card tells them to click "Regenerate" to bind without a code).

### AA installed with Docker

Add `git+https://github.com/yilifaer/aa-qqbot-plugin.git@v1.1.0` to `conf/requirements.txt` and put the block from step 3 into `conf/local.py`,
then rebuild the image following AA's Docker documentation, and run `python manage.py check` and `python manage.py migrate` inside the container.

---

## 3. Bot keys: rotation and safekeeping

For how to generate a key, see section 2, step 2. Each key must be written in two places, and **the key id must be the same in both**:

1. `QQBOT_API_KEYS` in AA's `local.py`;
2. The bot's (Koishi's) plugin configuration.

The key id is not a secret, but do not make it easy to guess: add a few random characters, e.g. `koishi-7f3a` (`koishi-1` in this guide is only an example).

**Rotating a key without downtime**: first **add** the new key to `QQBOT_API_KEYS` (keep the old one for now) → restart AA → switch the bot to the new key →
once the bot works normally, delete the old key and restart AA again. For example, during the transition:

```python
QQBOT_API_KEYS = {
    "koishi-1": "old key",
    "koishi-2": "new key",
}
```

If you suspect a key has leaked, immediately follow the steps above to switch to a new one, then delete the old one. For more precautions, see [section 10](#10-security-notes).

---

## 4. Assigning permissions

The plugin has two permissions. Both are attached to a **State** or a **Group** in the Django admin (`/admin/`):

| Permission | Shown in the admin as | Suggested for |
|---|---|---|
| `qqbot.basic_access` | `QQ binding / QQ 绑定 \| general \| QQ binding: member, can bind own QQ / QQ 绑定 - 成员：可以绑定自己的 QQ` | **The Member state** |
| `qqbot.manage` | `QQ binding / QQ 绑定 \| general \| QQ binding: manager, can manage QQ groups and bindings / QQ 绑定 - 管理员：可以在前台管理 QQ 群与绑定` | Create a new group, for example "QQ Admin", and add the admins to it |

Steps:

1. **Member permission**: admin → "Authentication" → "States" → open **Member** (this opens the edit page) → in the filter box above the left "Permissions" list, type
   `QQ binding: member` (or `QQ 绑定 - 成员`; this box filters by the displayed name) → select it and click the arrow to move it to the right → save.
2. **Admin permission**: admin → "Groups" → create or open the "QQ Admin" group (this opens the edit page) → in the same filter box above the left "Permissions" list, type
   `QQ binding: manager` (or `QQ 绑定 - 管理员`), move it to the right → save.
3. **Add the QQ admins to this group**: admin → "Users" → open the admin's page → add "QQ Admin" under "Groups" → save.
   (A group created in the admin is an "internal group" by default and does not appear on AA's "Group Management" page on the front end, so you have to add it on the user's page in the admin.)

Notes:

- **Use a superuser account to change a State**: in AA 5.2 to 5.5, when a staff user who has only the `change_state` permission opens the State edit page, the permissions field is read-only.
- **Do not look in the "Authentication" → "Permissions" list page**: it only searches by permission code (AA's `PermissionAdmin` searches only `codename`),
  so search for `basic_access` / `manage`; searching for "QQ" only finds the permissions of the old qqmonitor.

Explanation:

- If you later want allies or diplomats to be able to join the groups too, just attach `basic_access` to the matching state or group in the admin; no code change is needed.
- A **superuser** can see every page, but when deciding "may this QQ join the group" being a superuser does **not** grant automatic approval:
  a superuser, like everyone else, must actually be granted `basic_access`. So when you test with a superuser account, you can see the card,
  but the top of the card says "Your account has not been given the “QQ binding: member” permission. Contact an admin; being a superuser does not count.",
  and the bot also decides `NO_ACCESS`.
  Test the member features with an ordinary member account, or make sure the state the superuser account belongs to also has the member permission.
- Deactivated accounts and accounts without a main character can never join a group.

---

## 5. Where the pages are

| Page | Address | Who can see it |
|---|---|---|
| QQ binding card (bind, verification code, group numbers, change nickname, change QQ, unbind) | "Services" in the left sidebar → the "QQ binding" card, at `/services/`. Members have no separate page and no separate menu entry; the old address `/qqbot/` redirects to this card | Members |
| QQ Admin (QQ groups, Bindings, Needs attention, Settings, Audit log) | "QQ Admin" in the left sidebar (only visible to admins who have a main character), or the "QQ Admin" button at the bottom of the QQ binding card, at `/qqbot/manage/`. "Needs attention" also lists the bindings made without a code in the last 30 days (or the number of days of the trusted binding window, if that is set longer than 30 days; since 1.1.0) for admins to review | QQ admins |
| Django admin | "QQ binding" in `/admin/` | Superusers (bindings and the audit log can only be viewed in the admin, not changed; make changes through the front end. When an AA user is deleted, their binding is deleted with it; see below) |

**Red number**: the red number next to "QQ Admin" in the sidebar and on the "QQ Admin" button on the card = the number of conflicting QQs + the number of enabled role groups with no AA group selected,
and clicking it takes you to the page that needs handling. Unbound group members and the review of bindings made without a code are not counted.

The first thing to do after installation: a QQ admin opens "QQ Admin" → "QQ groups" and adds the groups to be managed, one by one.

When you need to reconcile again immediately (for example, you just changed many permissions), run this on the server (in the virtual environment from section 2, step 0):

```bash
python manage.py qqbot_reconcile
```

Any addition, change or deletion of a QQ group makes every binding's "fingerprint" stale, and the next daily reconcile writes one `recheck` event for every binding
(a large batch when there are many people). The bot handles them merged by group, so this is normal.

**Deleting an AA user**: deleting a user in the AA admin (one at a time, or with "Delete selected users" in the "Users" list) also deletes their QQ binding
and tells the bot to re-check this QQ; "QQ Admin" → "Audit log" shows "Account deleted". **There is no need to unbind first.**

- A non-superuser needs `auth | user | Can delete user` (note: not `authentication | user | Can delete user`; AA checks the former),
  **plus the other delete permissions listed on the delete confirmation page**: for every type that is deleted along with it and is registered in the admin, Django checks the delete permission.
  Common ones are (all "Can delete …"; the first is needed by almost every user):
  - `notifications | notification`
  - `authentication | character ownership`, `authentication | ownership record`
  - `esi | token`
  - `groupmanagement | group request`

  If the top of the confirmation page says "your account doesn't have permission to delete the following types of objects: …", grant the listed delete permissions to this account
  (use a superuser account and add them under "Users" or "Groups" in the admin). A QQ binding needs no separate permission (since 1.0.0b5).

**Changing the group nickname format**: after the group nickname format is changed under "QQ Admin" → "Settings", the plugin hands "recalculate everyone's group nickname" to Celery in the background. If the message queue (the broker, usually Redis) cannot be reached, then since 1.1.0 it no longer calculates directly inside that web request (it used to): the setting is already saved, the log has a `qqbot: could not queue reconcile` error, and the group nicknames are updated at the next daily reconcile, or when you run `python manage.py qqbot_reconcile` by hand on the server. Changing the Config in the Django admin works the same way.

**Sync nicknames action**: the action "Sync nicknames for selected QQ binding accounts" in the admin "Users" list immediately recalculates the selected users' group nicknames and tells the bot,
so it is safe to use; this English name is generated by AA and cannot be changed.

**Interface language**: the QQ binding card and the "QQ Admin" pages follow each person's language in AA: any Chinese (Simplified or Traditional) shows Simplified Chinese, and every other language (English, German, Russian, etc.) shows English.
For people who have never chosen a language in AA, the browser's language decides: a Chinese browser shows Chinese, any other language shows English (many people's Windows / Chrome is English).
If a member sees English: ask them to choose "简体中文 (zh-hans)" in the language selector at the bottom of AA's left sidebar (AA remembers the choice, so it is Chinese on later logins too).
Note: setting `LANGUAGE_CODE = "zh-hans"` in `local.py` **does not solve** this. When the browser is English, Django uses the browser's language directly;
`LANGUAGE_CODE` is only used when the browser sends no language that AA supports.
What members enter and what admins set (group rules, nicknames, group names, etc.) is shown as is and is not translated; the default group rules are in Chinese, and you can change them under "Settings".
The messages returned by the bot API and the messages of `python manage.py check` do not follow the language: the API replies in Chinese, and each check message has one line in English and one in Chinese.

---

## 6. Connecting the bot

The bot is the Koishi plugin [koishi-plugin-aaqqbot](https://github.com/yilifaer/Koishi-AAqqbot-plugin) ([npm](https://www.npmjs.com/package/koishi-plugin-aaqqbot)), in a separate repository. Whoever writes or runs the bot needs these 4 things:

1. **The API address**: AA's URL plus `/qqbot/api/v1/`, for example `https://auth.example.com/qqbot/api/v1/`.
   When AA is installed under a sub-path, include the prefix, for example `https://example.com/auth/qqbot/api/v1/`.
2. **The key id**: the name in `QQBOT_API_KEYS`, for example `koishi-1`.
3. **The key**: the string generated in section 2, step 2. Hand it over in person or through a password manager; do not post it in a group chat, a chat log, a screenshot or an issue.
4. **The API documentation**: [`API.md`](../API.md) in this repository. [`KOISHI_START.md`](../KOISHI_START.md) is the kick-off brief from when the Koishi plugin was written, and is now background reading only.

The first thing the bot should do after connecting is call `health`: `ok: true` and `config_ok: true` in the reply mean the network, the key and the signature all work.

---

## 7. Upgrading

Back up the database first (see "Preparation" in section 2), then:

```bash
# In the AA virtual environment, in the myauth directory (see section 2, step 0)
pip install --upgrade --force-reinstall --no-deps git+https://github.com/yilifaer/aa-qqbot-plugin.git@v1.1.0   # when upgrading to a newer version, replace v1.1.0 with the new version number
pip freeze | grep aa-qqbot
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
exit            # go back to the account that has sudo rights
sudo supervisorctl restart myauth:
```

(If gunicorn is not in the `myauth:` group, restart it separately too; see section 2, step 6.)

Notes:

- `--force-reinstall` makes sure the latest code on GitHub is installed even if the version number has not changed (without it, pip does nothing when the version is the same, and does not report an error);
  `--no-deps` means AA and the other dependencies are left alone.
- `pip freeze | grep aa-qqbot` shows `aa-qqbot @ git+https://…@<commit>`, so you can confirm which commit is installed;
  `pip show aa-qqbot` shows the version number. What changed is listed in [`CHANGELOG.md`](../CHANGELOG.md); if it mentions new settings, add them to `local.py` as described.
- Without git, change the address to `https://github.com/yilifaer/aa-qqbot-plugin/archive/refs/tags/v1.1.0.zip`; everything else is the same.

---

## 8. Uninstalling

The order matters: **stop AA first, then delete the tables, and finally remove the block from `local.py`.** If AA is still running while the tables are deleted, the "Services" page fails for everyone.

1. Stop AA (from the account that has sudo rights):

   ```bash
   sudo supervisorctl stop myauth:
   ```

   If gunicorn is not in the `myauth:` group, stop it too (see section 2, step 6).

2. Enter the virtual environment (section 2, step 0) and delete all of the plugin's tables (bindings, group settings and the audit log are all deleted, so back up first):

   ```bash
   python manage.py migrate qqbot zero
   ```

3. Delete the scheduled task in the database. AA 5's celery beat copies `CELERYBEAT_SCHEDULE` into the database,
   so removing it only from `local.py` does not stop it: `qqbot.tasks.reconcile` is still sent every day, and the worker log keeps reporting
   "unregistered task":

   ```bash
   python manage.py shell -c "from django_celery_beat.models import PeriodicTask; print(PeriodicTask.objects.filter(name='qqbot_reconcile').delete())"
   ```

4. Remove the block you added to `local.py` during installation (if you installed with this guide's section 2, it runs from `# ---------- aa-qqbot ----------` to `# ---------- aa-qqbot end ----------`; if you installed with the README, it is the lines of README step 3).

5. Uninstall the plugin, and clean up the two leftover QQ permissions in the admin (this must be done after step 4):

   ```bash
   pip uninstall -y aa-qqbot
   python manage.py shell -c "from django.contrib.contenttypes.models import ContentType; print(ContentType.objects.filter(app_label='qqbot').delete())"
   ```

   This deletes only the plugin's 9 content types and 2 permissions (and the links of these 2 permissions to states, groups and users); the output looks like
   `(…, {'auth.Permission': 2, 'contenttypes.ContentType': 9, …})`.
   **Do not use `python manage.py remove_stale_contenttypes --include-stale-apps`**: it also deletes the permissions left behind by every other plugin that was ever uninstalled from this AA.

6. Start AA:

   ```bash
   exit
   sudo supervisorctl start myauth:
   ```

Remember to stop the plugin on the bot's side as well. If you reinstall later, AA's event numbers start from the beginning again, so tell the bot's side to reset its event cursor to zero
(see section 8 of [`API.md`](../API.md)).

---

## 9. Common problems

| What you see | Cause | What to do |
|---|---|---|
| `pip install` says `Cannot find command 'git'` | git is not installed on the server | `sudo apt install git`; or install with the `.zip` address from section 2, step 1 |
| `pip` wants to install `allianceauth-5.x`, `Django-5.x` | This AA is 4.x or 5.0/5.1 | Upgrade AA first following AA's official documentation, then install the plugin (section 2, step 0) |
| Running `pip` or `python manage.py` says the command is not found or `can't open file 'manage.py'` | You are not in the virtual environment, or not in the `myauth` directory | Follow section 2, step 0 |
| `migrate` reports a `SystemCheckError` containing `qqbot.E00…` | `local.py` is configured wrongly | Fix it with the table in section 2, step 4, then run again |
| `qqbot.E003` right after installing | You forgot to replace `your-secret` with the real key | Put in the key generated in section 2, step 2 |
| After a restart the website does not open and the log has `NameError: name 'crontab' is not defined` | `local.py` does not start with `from .base import *` | Add the line `from celery.schedules import crontab` before the aa-qqbot block |
| The log has `NameError: name 'APPS_WITH_PUBLIC_VIEWS' is not defined` | `local.py` does not have this variable | Add the line `APPS_WITH_PUBLIC_VIEWS = []` before the aa-qqbot block |
| AA does not start and the log has `ModuleNotFoundError: No module named 'httpx'` | AA 5.2 to 5.4 use `httpx`, while `django-esi` 10.x, released in 2026-10 (the version AA 5.5 requires), switched to `httpx2`. When you upgrade other packages, pip may upgrade `django-esi` to 10.x along the way | `pip install "django-esi<10"`, then restart; or upgrade AA to 5.5. The plugin's upgrade command has `--no-deps`, so it does not cause this problem |
| Every AA page fails, and the error message contains `sri_static` | AA 5.2.x / 5.3.x do not restrict the `django-sri` version, and `django-sri` 1.0, released in 2026-09, removed the `sri_static` tag that AA's pages use | `pip install "django-sri<1"`, then restart. AA 5.4 and later already include this restriction, so the easiest fix is to upgrade AA to 5.4 |
| The "Services" page has no "QQ binding" card | The account does not have the "QQ binding: member" permission, or AA was not restarted after installing | Section 4; `sudo supervisorctl restart myauth:` |
| The left sidebar has no "QQ Admin" | There is no "QQ binding: manager" permission; or the account has no main character (since 1.0.0b5 an admin without a main character does not get this menu entry, because clicking it would only send them back to the AA dashboard) | Section 4; add a character on the AA dashboard and make it the main character |
| The card and the "QQ Admin" pages are in English ("QQ binding", "QQ Admin"), but the person wants Chinese | This person chose English in AA (or chose nothing and the browser is English) | Ask them to choose "简体中文 (zh-hans)" in the language selector at the bottom of AA's left sidebar. Changing `LANGUAGE_CODE` does not help (end of section 5) |
| A superuser can see the card but it says "There are no groups you can join right now." (since 1.0.0b5 the top of the card also states the reason) | A superuser does **not** automatically become eligible to join | Give the state this account is in (for example Member) the member permission, or test with an ordinary member account |
| An existing member entered their QQ and still got a verification code | More than the "Trusted binding window (days)" in "Settings" (default 30 days) has passed since this group was added to AA; or the bot has not yet reported the full member list of this group, or the list is older than its lifetime (default 7 days) | Let the bot run one check first and only then tell existing members to bind (section 2, step 7); once the bot has reported the member list, the card tells them to click "Regenerate" to bind without a code. On a Raspberry Pi without a bot, use the "simulated bot" in [`docs/TESTING.md`](TESTING.md) |
| Deleting a user in the admin says "your account doesn't have permission to delete the following types of objects: QQ binding / QQ 绑定" | A problem of 1.0.0b4 | Upgrade to 1.0.0b5; as a temporary workaround, force-unbind under "QQ Admin" first and then delete |
| AA 5.2.0 / 5.3.0: deleting certain users in the admin (mostly people who have handled group join requests, for example group leaders and QQ admins) fails with a 500 page, and the log has `TypeError: __str__ returned non-string` | A problem in AA 5.2.0 / 5.3.0 itself (the display function of the join request record `RequestLog` returns a number), fixed in 5.3.1; the user was not deleted and the QQ binding was not touched | Upgrade AA to 5.3.1 or later; as a temporary workaround, run this on the server: `python manage.py shell -c "from django.contrib.auth.models import User; User.objects.get(username='your-username').delete()"` (replace `your-username` with the user name); the QQ binding is deleted along with it as usual and the bot is told |
| AA 5.2.0: after a member changes corporation or alliance, the ticker in the group nickname stays the old one | When AA 5.2.0 updates a character's affiliation and an ESI query fails midway, it leaves the new corporation ID with the old ticker (5.3 and later save it in one step, which fixes this); eligibility is decided by ID, so it is not affected | Upgrade AA to 5.3 or later; or in the admin "Characters" list select this character and run AA's own update-character action |
| Deleting a user in the admin says you lack permission to delete other types of objects (notifications, character ownership, ESI tokens, etc.) | A non-superuser needs every delete permission listed on the confirmation page | "Deleting an AA user" in section 5 |
| Clicking "Services" or "QQ Admin" sends you back to the dashboard, with a red message at the top: "A main character is required to perform that action. Add one below." | The account has no main character: AA 5.x redirects such users from the "Services" page and from all plugin pages back to `/dashboard/` before qqbot's code runs | Add a character on the dashboard and make it the main character. Meanwhile the member cannot unbind or change their nickname themselves; a QQ admin can force-unbind when needed |
| You changed the group nickname format and the group nicknames did not change; the log has `qqbot: could not queue reconcile` | Recalculating everyone's group nickname has to be handed to Celery in the background, and AA cannot reach the message queue (the broker, usually Redis). Since 1.1.0 it then only logs an error and does not calculate directly in the web request; the setting itself is already saved | Fix the Redis / celery connection, then run `python manage.py qqbot_reconcile` on the server, or wait for the next daily reconcile |
| A member sees "Too many different QQ numbers tried (at most 5 per hour). Please try again later." | Each AA account may try at most 5 **different** QQ numbers per hour on the services card (since 1.1.0). Retrying the same QQ does not count, and changing only the nickname of the current QQ never counts. After that, any further new QQ (whether free or already bound) gets this same sentence, so trying QQ numbers one by one to map out who is in the alliance becomes very slow; it is limited, not impossible | Wait an hour (counted from the first new QQ tried in that hour) and try again; if they really need a QQ they cannot enter, contact a QQ admin |
| The log has a `qqbot: API … refused (…) from …` warning | An API request was refused (401, 413, 429 or 503): wrong key or signature, clock difference too large, request too large, too frequent, or AA is not configured properly | Look up the error code in the brackets in section 6 of [`API.md`](../API.md). The log only writes key ids that are configured (`(none)` if none was sent, `(unknown key id)` if it is not configured), and writes at most one line per minute for the same error code and the same source address (since 1.1.0), and requests whose method is not POST (`405`, logged at INFO level) are throttled by the same rule; when AA is behind nginx or a tunnel the source address is the proxy's address, so it is one line per error code per minute, and for the real client look at the proxy headers that follow, such as `X-Forwarded-For` |
| Someone asks for their personal data to be deleted | What data the plugin keeps and for how long is in section 11 | Section 11.4: first remove every binding of this QQ and of this user (have them unbind, or force-unbind, or delete the AA user), then run `python manage.py qqbot_erase --qq <QQ> --user <username>`; add `--dry-run` first to see what would change |
| The bot receives `302` or a login page | `"qqbot"` is not in `APPS_WITH_PUBLIC_VIEWS` (`qqbot.E001`) | **Append** it in `local.py`, then restart |
| The bot receives a `400` page (Bad Request) | The address the bot uses (for example the Raspberry Pi's LAN IP) is not in `ALLOWED_HOSTS` | Have the bot use the domain in `SITE_URL`; or add `ALLOWED_HOSTS += ["192.168.x.x"]` (replace with the actual IP) at the end of `local.py` and restart |
| The bot receives `301` | The address is missing the trailing `/`, or http was used for a site that only allows https | End the address with `/` and use https |
| The bot receives a JSON response with `401` or `503` | A key or signature problem | Look at the `error` field and compare with section 6 of [`API.md`](../API.md) |
| The celery worker log keeps reporting `unregistered task qqbot.tasks.reconcile` | The worker was not restarted after installing; or, after uninstalling, the scheduled task is still in the database | Restart; for uninstalling, see section 8, step 3 |
| The daily reconcile does not seem to run at 04:17 | The time is UTC; in Beijing time it is 12:17 | Normal; to change the time, change `hour` in `crontab(...)` |
| Nothing seems to have changed after upgrading | `pip install -U` does not reinstall when the version number is unchanged; or AA was not restarted after installing | Use the command with `--force-reinstall --no-deps` from section 7, then restart. To see which version is installed, look at the small text at the bottom right of the "QQ binding" card, or at the right of the title bar of "QQ Admin" → "Settings" (since 1.0.1) |
| The error `Application labels aren't unique, duplicates: qqbot` | `local.py` adds `"qqbot"` to `INSTALLED_APPS` in two places (usually left over from an old 0.x version) | Delete the old one and keep only the block from section 2, step 3 |
| An old version (0.x) was installed before: `local.py` has `QQBOT_GROUP_CHAT`, `QQBOT_PING_GROUP`, and the database has a `qqbot_qqbinding` table | Settings and data left by the old version, which the new version no longer uses | Delete these two lines from `local.py`; after installing the new version (with `qqbot` still in `INSTALLED_APPS`) run `python manage.py shell -c "from django.apps import apps; from django.contrib.contenttypes.models import ContentType; live = {m._meta.model_name for m in apps.get_app_config('qqbot').get_models()}; print(ContentType.objects.filter(app_label='qqbot').exclude(model__in=live).delete())"` to clean up the old version's permissions (it deletes only the qqbot types that no longer exist); the old table can be deleted by running `DROP TABLE qqbot_qqbinding;` in `python manage.py dbshell` (back up first) |
| `migrate` reports `table "qqbot_…" already exists` (on MySQL, `Table 'qqbot_…' already exists`) | This AA had a development test version from before 1.0.0b1 (`1.0.0.dev0`) installed, and the tables already exist | Run `python manage.py migrate qqbot 0001_v1_initial --fake`, then run `python manage.py migrate`, then restart |
| A page fails with `no such table: qqbot_…` (on MySQL, `Table '…qqbot_…' doesn't exist`) | The tables were not created: `migrate` was not run, or a development test version from before 1.0.0b1 is installed | Use `pip show aa-qqbot` to confirm the version is 1.0.0b1 or newer (if not, upgrade as in section 7), then run `python manage.py migrate`, then restart |

---

## 10. Security notes

- The key is written only in `local.py` on the server and in the bot's configuration; AA's web pages have no place to enter a key, and should not have one.
- Before you send `local.py` to someone to ask for help, delete the lines for `QQBOT_API_KEYS`, `SECRET_KEY`, `ESI_SSO_CLIENT_SECRET` and the database password.
- If you suspect a key has leaked, switch to a new one as in section 3, then delete the old one.
- The switch for "actually kick people out of the group" exists only on the bot's side; nothing done on AA's web pages kicks anyone directly.
- A production site must use HTTPS and keep `DEBUG = False`.
- Do not put real group numbers, QQ numbers or server addresses in screenshots, issues or chat logs.
- What personal data the plugin keeps, for how long, and what to do when a deletion request arrives: see section 11.

---

## 11. Data protection

This section answers three questions: what data about people the plugin keeps, who can see it and for how long; which data leaves AA; and what to do when a "please delete my data" request arrives.
The short version is in the [README](../README.md#data-protection).

### 11.1 What is stored, who can see it, how long it is kept

| Data | Stored in | Concerns | Who can see it | How long it is kept |
|---|---|---|---|---|
| QQ number, nickname, status ("Verified" / "Trusted (already in group)": an existing member without verification), the group nickname set by an admin, time of binding | Binding | The member | The member sees their own QQ in the card (partly masked); QQ admins see the full QQ in "QQ Admin"; superusers can also view it read-only in the Django admin | Until unbind; deleted along with the AA user |
| The QQ typed when requesting a verification code (**it may be someone else's QQ**), the nickname, the hash of the code (the plain code is not stored) | Verification code request | The member, and the person whose QQ was typed | The member sees their own pending code in the card; QQ admins never see the code (neither the admin pages nor the Django admin show it), but the QQ typed appears in the audit log ("Generate verification code") | Deleted 7 days after the code expired, was used or was invalidated (only when the daily reconcile runs); deleted along with the AA user |
| The full member list the bot reports for each managed group: the QQ of everyone in the group | Group member list | Members **and group members without an AA account** | QQ admins, who see the full QQ in the "QQ numbers in a group but not bound" list under "Needs attention" | Until the bot's next full report for this group (QQ numbers not on the list are then deleted), or until the group is deleted; a disabled group gets no more reports and keeps its last list (not shown in the UI) until the group is deleted |
| The type and QQ of every change | Event outbox | The member | The bot (the `events` API) | 30 days (deleted only when the daily reconcile runs) |
| Who did what to which QQ and when: the user names and QQ of the actor and the target user; the details may contain the old and new nickname, the QQ before a change, the user names of other claimants, the user name of the winner of a conflict, the QQ of someone who used another person's verification code to apply | Audit log | Members, admins, and outsiders | QQ admins ("QQ Admin" → "Audit log" and the binding details page); superusers (Django admin, read-only) | Forever by default, and also kept after the AA user is deleted (the entry stores a copy of the user name); if `QQBOT_AUDIT_RETENTION_DAYS` is set, older entries are deleted when the daily reconcile runs |
| The plain verification code, QQ and nickname the member just typed | AA's session (stored in the database by default and sped up by the cache) | The member | Shown only to the member | Until the card finds that the code is no longer valid (expired, cancelled or used), or until the session ends; the result message of an action (with the QQ and nickname just typed) is removed the next time the member opens the Services page and is not shown after 5 minutes; if the member never returns, it stays until the session ends |
| The QQ numbers a member tried on the services card in the current hour (for the QQ try limit; up to 5, and they **may be other people's QQ numbers**); the source address of refused API requests (for the log throttle) | AA's cache (Redis) | The member, the people whose QQ was typed; whoever calls the API | Nobody | The QQ numbers expire within 1 hour; the source address after 60 seconds |
| User names, masked QQ numbers; for refused API requests also the source IP, the proxy headers and the key id | AA's log files | Members, whoever calls the API | Admins who can read AA's log files | As set by AA's logging configuration (log rotation) |

Key points:

- **Events and expired verification codes are deleted only when the daily reconcile runs** (the `qqbot_reconcile` scheduled task from section 2, step 3, or `python manage.py qqbot_reconcile` run by hand). On a site where no daily reconcile is scheduled, these two kinds of data keep piling up (`python manage.py check` reports `qqbot.W002`).
- The member list contains the QQ of **people without an AA account**: they are not in any binding, and they have no place where they can act themselves; the only way to delete them is the command in section 11.4 (`--qq` is enough). People who have left the group disappear from the list only when the bot next reports that group's full member list; a disabled group gets no more reports, so its list stays until the group is deleted.
- The QQ and user names in the audit log are in plain text. QQ admins and superusers can see them, and they are in database backups too.

### 11.2 What leaves AA

- When the bot asks about a QQ (`check`, `claim`), AA answers with the **decision and the reason**; for a bound member it also gives the **group nickname** (corporation ticker, character name, nickname).
- When the bot fetches events (`events`), it receives the change type and the QQ of every binding change (events are kept 30 days).
- The bot reports to AA the group member list (QQ numbers) and the QQ and the original verification message of join requests. AA only looks for the verification code in the original text and does not store the text.
- What the bot's side stores and for how long is decided by the bot ([koishi-plugin-aaqqbot](https://github.com/yilifaer/Koishi-AAqqbot-plugin)) itself and is outside this plugin's responsibility; when a deletion request arrives you also have to handle it there.

### 11.3 Limiting how long the audit log is kept (optional)

The audit log is kept forever by default. If you want to limit that, add a line to `local.py` (inside the block from section 2, step 3):

```python
QQBOT_AUDIT_RETENTION_DAYS = 365
```

After restarting AA, the daily reconcile deletes audit log entries older than that many days (the reconcile log has a line `… old audit log entries pruned`, and `python manage.py qqbot_reconcile` run by hand also prints how many were deleted).

- **At least 30 days**: 1–29 is treated as 30 days, because the change QQ cooldown (at most 720 hours, which is 30 days) reads the audit log. `python manage.py check` reports `qqbot.W003` for these two cases; a value that is not an integer or is below 0 is ignored, and the audit log continues to be kept forever.
- The restrictions recorded by deleted entries lapse with them: a member who was force-unbound from a QQ by an admin must use a verification code to bind this QQ again (decision #25), and this restriction holds only while the force-unbind entry still exists; after the retention period, they can go through the existing-member route without verification again. After `qqbot_erase` is run for a person (section 11.4), their own entries are anonymised and this restriction no longer recognises them either. This is a deliberate trade-off (decision #32).
- If you want to keep it forever, do not set it, or set it to `0`.

### 11.4 Handling a deletion request

After you receive a "please delete my data" request:

1. **First remove the bindings.** The command refuses to run while any binding exists for this QQ or this user, including another account's binding of the same QQ (for example the other side of a conflict, or someone who claimed it); the error lists them. Choose any one of three ways: the member unbinds in the card themselves; a QQ admin force-unbinds under "QQ Admin" → "Bindings"; or you delete the user in the AA admin (the binding is deleted with it, **no need to unbind first**). No waiting is needed afterwards: when the command deletes events it makes the bot re-check by itself (see the "Events" point below).
2. On the server (in the virtual environment from section 2, step 0), do a trial run first to see what would change:

   ```bash
   python manage.py qqbot_erase --qq 12345678 --user some-username --dry-run
   ```

3. When you are sure, run it again without `--dry-run` and type `yes` to confirm:

   ```bash
   python manage.py qqbot_erase --qq 12345678 --user some-username
   ```

The full form is `python manage.py qqbot_erase [--qq <QQ>] [--user <username>] [--dry-run] [--noinput]`. Options:

| Option | Description |
|---|---|
| `--qq` | The person's QQ number |
| `--user` | The person's AA user name. It also works after the AA user has been deleted: the audit log keeps a copy of the user name, and the command matches on that. So if a deleted account once had the same user name, its entries match too |
| `--dry-run` | Only show how many entries would change, and change nothing; it also lists the other accounts that have entries on this QQ (see below) |
| `--noinput` (`--no-input`) | Do not ask for `yes` confirmation (for use in scripts) |

Give at least one of `--qq` and `--user`; giving both is the most thorough (when you give only one, only the records that can be found from that one are handled).

The command does the following **in one transaction**, so if any step fails nothing is changed:

- Audit log entries about this person (their AA account or user name is the target, or the entry is on their QQ and has no target account): the QQ, the target account and the user name are removed and the details are cleared; the entry itself stays, and "QQ Admin" → "Audit log" shows a blank QQ and target, and "Personal data erased" in the details;
- Entries where this person is the actor (for example their own binding or nickname change): the actor is shown as `[erased]` (not "Bot / system");
- Entries on this QQ that belong to **other accounts** (for example someone who claimed the QQ without a code and was force-unbound by an admin) are kept, including the QQ and the target account; only the fields in them that name this person (other claimants, the winner of a conflict, the QQ of an applicant) are replaced by `[erased]`. They record what those accounts did, and they keep that account's force-unbind restriction and change QQ cooldown working. The trial run lists these accounts; if one of them is in fact the same person, run the command again with `--user` as well;
- In every other entry, too, only the fields that name this person are replaced by `[erased]`: the user names of other claimants (under "Other claiming accounts"), the winner of a conflict (under "Winning account"), and the QQ of an applicant who used someone else's verification code;
- All verification code requests for this QQ are deleted, whichever account made them (another account's pending code for this QQ stops working), plus all code requests of this user, and so are the event outbox entries for this QQ and the group member list entries for this QQ (if this person is still in a managed group, the bot reports their QQ again when it next reports that group's full member list);
- Events: if any event was deleted (it may have been an unbind notice the bot had not fetched yet), the command adds one "recheck all" event (without any QQ), so the bot re-checks all groups promptly;
- Cache: when `--user` names an existing AA user, that user's QQ try list is deleted from AA's cache (see the table in 11.1). Other cache entries expire on their own within an hour.

Afterwards:

- **The change QQ cooldown and the force-unbind restriction no longer recognise this person**, because both read the audit log: a QQ they were force-unbound from no longer requires a verification code when they bind it again; a change QQ cooldown they had not finished after unbinding themselves is no longer counted either.
- The command **does not** delete the AA user itself, the log files or database backups, and it does not delete the data stored on the bot's side. The logs contain only user names and masked QQ numbers, which disappear with log rotation.
- If this person is still in a managed QQ group, the bot reports their QQ again when it next reports that group's full member list, and the number is on the list again. For it to really disappear, they must leave the group first.

### 11.5 QQ numbers in logs and refused requests

- QQ numbers in the logs are masked: with 9 digits or more, the first 2 and last 2 are kept (`123456789` → `12*****89`); with 5–8 digits only the first and last digit are kept (`12345678` → `1******8`); with 4 digits or fewer everything is masked (since 1.1.0; before that, 5–8 digits showed the first and last 2). The QQ on the Services page card follows the same rule.
- When an API request is refused (401, 413, 429, 503), one WARNING line is logged with the error code, the source address, `X-Forwarded-For` / `X-Real-IP` / `CF-Connecting-IP` (each at most 100 characters, used only for troubleshooting) and the key id. Only key ids configured in `QQBOT_API_KEYS` are logged; `(none)` if none was sent, and `(unknown key id)` for anything else, so a key mistakenly put into this request header never reaches the log. At most one line per minute is logged for the same error code and the same source address (since 1.1.0). A request whose method is not POST (`405`) does not count as a refusal and only gets an INFO line, throttled by the same rule: at most one per minute for the same source address.

---

## 12. Development and testing

You need: Python 3.10+ and a Redis running on the local machine (the tests use Redis database 15 by default).

```bash
git clone https://github.com/yilifaer/aa-qqbot-plugin.git
cd aa-qqbot-plugin
python -m venv .venv && . .venv/bin/activate
pip install "allianceauth>=5.2,<6"   # the MySQL client headers must be installed first, for example
                                     # apt install default-libmysqlclient-dev build-essential pkg-config
redis-server --daemonize yes         # if Redis is not running yet
python manage.py test qqbot
```

- The test settings are in `testauth/settings.py` and use SQLite by default.
- To use a different Redis address: `QQBOT_TEST_REDIS=redis://127.0.0.1:6379/5 python manage.py test qqbot`.
- To run against MariaDB/MySQL or PostgreSQL (the concurrency tests in `qqbot/tests/test_core_concurrency.py` run only on these two databases):
  `QQBOT_TEST_DB=mysql` (or `postgres`), and give the connection details with `QQBOT_DB_HOST`, `QQBOT_DB_PORT`, `QQBOT_DB_USER`,
  `QQBOT_DB_PASSWORD` and `QQBOT_DB_NAME`. PostgreSQL also needs `pip install "psycopg[binary]"`.
- For the full matrix use [tox](https://tox.wiki/): `tox` (or `tox -e py312-aa5`, `tox -e py-aa5-mysql`);
  the GitHub Actions configuration is in `.github/workflows/tests.yml`.
- Before committing, make sure that `python manage.py makemigrations qqbot --check --dry-run` finds no new migration
  and `python -m pyflakes qqbot` reports no errors.
- **Releasing**: every change that is handed to the owner or to IT to install must raise `__version__` in `qqbot/__init__.py` (for example `1.0.0b1` → `1.0.0b2`)
  and add an entry to [`CHANGELOG.md`](../CHANGELOG.md) (decision #20). Do not rename the migration file `0001_v1_initial` (see the comment at the top of the file for why).

Development documents: [`DESIGN.md`](../DESIGN.md) (the design, for the owner), [`docs/SPEC.md`](SPEC.md) (the implementation spec),
[`DECISIONS.md`](../DECISIONS.md) (the decision log), [`API.md`](../API.md) (the bot API), [`KOISHI_START.md`](../KOISHI_START.md) (the kick-off brief for the Koishi plugin).

---

## License

MIT, see [LICENSE](../LICENSE).
