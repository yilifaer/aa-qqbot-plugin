# aa-qqbot 分步安装说明

[English](GUIDE.en.md) | 简体中文

> 简版安装步骤见 [README](../README.zh-CN.md)。这里是给第一次安装的人看的分步说明，以及常见问题。

[Alliance Auth](https://gitlab.com/allianceauth/allianceauth)（AA）的 QQ 绑定插件：
**只让 AA 认可的人留在联盟的 QQ 群里。**

- 成员在 AA 里绑定自己的 QQ 号；
- QQ 机器人（Koishi 插件 [koishi-plugin-aaqqbot](https://github.com/yilifaer/Koishi-AAqqbot-plugin)，另一个仓库）来问 AA：「这个 QQ 能不能进这个群？群名片该叫什么？」；
- 成员离开联盟、账号被停用、换了主角色，AA 都会记下来，机器人下次来取时就知道了。

当前版本：**1.1.0**，更新内容见 [`CHANGELOG.md`](../CHANGELOG.md)。
需要 Alliance Auth 5.2～5.5（每个版本都有自动测试）、Python 3.10 及以上。

---

## 目录

1. [它能做什么](#1-它能做什么)
2. [安装（给 IT）](#2-安装给-it)
3. [机器人密钥：更换与保管](#3-机器人密钥更换与保管)
4. [分配权限](#4-分配权限)
5. [页面在哪里](#5-页面在哪里)
6. [机器人那边怎么接](#6-机器人那边怎么接)
7. [升级](#7-升级)
8. [卸载](#8-卸载)
9. [常见问题](#9-常见问题)
10. [安全须知](#10-安全须知)
11. [数据保护](#11-数据保护)
12. [开发与测试](#12-开发与测试)

装好以后在树莓派测试 AA 上逐项点一遍：[`docs/TESTING.md`（树莓派测试清单）](TESTING.md)。

---

## 1. 它能做什么

**成员**（有「QQ 绑定 - 成员」权限的人）：

- 所有操作都在 AA「服务（Services）」页的「QQ 绑定」卡片里完成，不用去别的页面；
- 填写自己的 QQ 号和昵称：
  - **已经在群里的老成员**：填完马上生效，不用验证；
  - **新成员**：会拿到一个验证码（例如 `QQ-7KXM2P`），申请入群时把验证码填在「验证信息」里，机器人看到后自动通过；
- 看到自己有资格加入的群；
- 在卡片里修改昵称、换绑、解绑（点卡片底部的按钮，就地展开小表单）。

**QQ 管理员**（有「QQ 绑定 - 管理员」权限的人）在左侧菜单多一个「QQ 管理」，全部在网页前台完成，不用进 Django 后台：

- 添加、修改、停用 QQ 群，分「固定群」（所有成员都能进）和「身份组小群」（只有指定 AA 组的人能进）；
- 查看所有绑定，修改某人的群名片，确认绑定，强制解绑；
- 处理「冲突」（同一个 QQ 被两个账号认领）和「群里有但没绑定」的 QQ；
- 在「待处理」里复核最近 30 天的免验证绑定（「设置」里的免验证过渡期设得比 30 天长时，按过渡期的天数），发现冒领别人 QQ 的可以直接强制解绑（强制解绑后，这个人再绑同一个 QQ 必须用验证码，只有真号主能用）；
- 修改入群须知、群名片格式、验证码有效期、群成员名单有效期、换绑冷却等设置；
- 查看操作记录。

**机器人**（Koishi）通过带签名的接口来问 AA，接口说明见 [`API.md`](../API.md)。
「真的把人踢出群」这个开关只在机器人那边，AA 网页上开不了，防止误操作。

> AA 默认是英文界面，左侧菜单显示为「Services」。可以在左侧菜单底部的语言选择里切换成简体中文。
> QQ 绑定卡片和「QQ 管理」页面也跟着这个语言走：选英文时显示英文（卡片叫 "QQ binding"，菜单叫 "QQ Admin"），
> 选中文时显示中文，选其他语言时显示英文。见第 5 节末尾。

### 截图（演示数据）

| 服务页里的 QQ 绑定卡片 | 卡片：绑定前 | 卡片：新成员等待验证 |
|---|---|---|
| ![服务页卡片](screenshots/01-services-card.png) | ![绑定前](screenshots/02-member-unbound.png) | ![等待验证](screenshots/03-member-pending.png) |

| 卡片：已绑定 | 卡片：冲突 | 管理：已绑定成员 |
|---|---|---|
| ![已绑定](screenshots/04-member-bound.png) | ![冲突](screenshots/06-member-conflict.png) | ![已绑定成员](screenshots/07-manage-bindings.png) |

| 管理：QQ 群 | 管理：待处理 | 管理：设置 |
|---|---|---|
| ![QQ 群](screenshots/08-manage-groups.png) | ![待处理](screenshots/10-manage-pending.png) | ![设置](screenshots/11-manage-settings.png) |

深色主题（Darkly）下的卡片：

| 未绑定 | 等待验证 | 已绑定（管理员） | 冲突 |
|---|---|---|---|
| ![深色-未绑定](screenshots/cards/card-dark-unbound.png) | ![深色-等待验证](screenshots/cards/card-dark-pending.png) | ![深色-已绑定](screenshots/cards/card-dark-manager-verified.png) | ![深色-冲突](screenshots/cards/card-dark-conflict.png) |

全部截图在 [`docs/screenshots/`](screenshots/)（浅色和深色主题的卡片在 [`docs/screenshots/cards/`](screenshots/cards/)）。
截图里的群号、QQ 号和角色都是虚构的演示数据。

---

## 2. 安装（给 IT）

下面以 **AA 官方安装文档的默认目录**为例：

| 项目 | 官方默认值 | 你们的（不一样就换掉） |
|---|---|---|
| 运行 AA 的 Linux 用户 | `allianceserver` | |
| Python 虚拟环境 | `/home/allianceserver/venv/auth` | |
| AA 项目目录（`manage.py` 所在的目录） | `/home/allianceserver/myauth` | |
| 配置文件 | `/home/allianceserver/myauth/myauth/settings/local.py` | |
| supervisor 里 AA 的进程组 | `myauth` | |

用 Docker 安装的 AA 见本节末尾。

### 准备

- 一个已经能正常使用的 **Alliance Auth 5.2～5.5**；Redis、celery worker 和 celery beat 都在正常运行。
- 服务器上要有 `git`：运行 `git --version`，如果提示找不到，用有 sudo 权限的账号执行 `sudo apt install git`。
- **不需要**额外安装其他 apt 软件包：本插件只依赖 AA 本身（`mysqlclient` 等在安装 AA 时已经装好）。
- **先备份数据库。** 官方安装方式用的是 MariaDB/MySQL，例如（数据库名、用户名以 `local.py` 里 `DATABASES` 的写法为准）：

  ```bash
  mysqldump -u allianceserver -p alliance_auth > ~/aa-backup-$(date +%F).sql
  ```

  Ubuntu 上装的 MariaDB/MySQL 可以不输密码（root 走 socket 认证；`--single-transaction` 备份时不锁表）：

  ```bash
  sudo mysqldump --single-transaction alliance_auth > ~/aa-backup-$(date +%F).sql
  ```

### 第 0 步：进入 AA 的虚拟环境，确认版本

用 SSH 登录服务器后运行：

```bash
sudo su allianceserver
source /home/allianceserver/venv/auth/bin/activate
cd /home/allianceserver/myauth
```

命令行开头出现 `(auth)` 就说明已经进入虚拟环境。**第 1–5 步的 `pip` 和 `python manage.py …` 都在这里执行。**
`sudo supervisorctl …` 需要有 sudo 权限的账号（`allianceserver` 一般没有），到第 6 步时先输入 `exit` 退回原来的账号再执行。

确认 AA 的版本：

```bash
pip show allianceauth django-sri django-esi aa-qqbot | grep -E '^(Name|Version)'
```

（没装过本插件时会多一行 `WARNING: Package(s) not found: aa-qqbot`，这是正常的。）

- `allianceauth` 必须是 **5.2～5.5**。如果是 4.x（或 5.0、5.1），**先不要装本插件**：
  pip 会顺带把整个 AA 升级到最新的 5.x，而 AA 的大版本升级有自己的步骤。请先按 AA 官方文档升级 AA。
- 如果 `allianceauth` 是 5.2.x 或 5.3.x，而 `django-sri` 是 `1.x`，先运行 `pip install "django-sri<1"`（原因见[常见问题](#9-常见问题)）。
- 如果 `allianceauth` 是 5.2.x～5.4.x，而 `django-esi` 是 `10.x`，先运行 `pip install "django-esi<10"`（原因同样见[常见问题](#9-常见问题)）。
- 如果显示了 `aa-qqbot`、版本是 **0.x**（例如 `0.2.0`）：这台 AA 装过本仓库的旧版（只有 QQ 号 + 昵称的那个版本）。
  可以直接往下装，新版会建立自己的数据表；旧版的绑定数据不会被导入（决定 #5），全员重新绑定。
  **第 3 步之前**，先把 `local.py` 里旧版加的内容删掉：`INSTALLED_APPS` 里的 `"qqbot"`（否则会报
  `Application labels aren't unique, duplicates: qqbot`），以及 `QQBOT_GROUP_CHAT`、`QQBOT_PING_GROUP` 两行。
  装好后再按[常见问题](#9-常见问题)里「以前装过旧版」一行清理旧权限和旧表。

### 第 1 步：安装插件

```bash
pip install git+https://github.com/yilifaer/aa-qqbot-plugin.git@v1.1.0
```

- 最后一行出现 `Successfully installed aa-qqbot-1.1.0` 就装好了（可能还会列出其他包，是 AA 缺的依赖）。
- 提示 `Cannot find command 'git'`：先安装 git（见「准备」），或者改用不需要 git 的写法：
  `pip install https://github.com/yilifaer/aa-qqbot-plugin/archive/refs/tags/v1.1.0.zip`
- 注意：包名是 `aa-qqbot`，只能用上面的 GitHub 地址安装。**不要**运行 `pip install qqbot`：
  PyPI 上的 `qqbot` 是另一个无关的项目，会覆盖本插件的代码。

### 第 2 步：生成机器人密钥

在服务器上（虚拟环境里）或你自己的电脑上运行：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

会打印一串 **64 个字符**的随机字符（Windows 上如果提示找不到 python，把 `python` 换成 `py`）。
下一步把它填进 `local.py`，替换掉「这里换成生成的密钥」。同一把密钥之后还要交给机器人那边（第 6 节）。
**不要**把它发到群里、聊天记录、截图或任何仓库里。

### 第 3 步：修改 `local.py`

用编辑器打开配置文件，例如：

```bash
nano /home/allianceserver/myauth/myauth/settings/local.py
```

在文件**最末尾**粘贴下面这段（nano 里按 Ctrl+O、回车保存，再按 Ctrl+X 退出）：

```python
# ---------- aa-qqbot ----------
INSTALLED_APPS += ["qqbot"]

# 机器人接口不走 AA 登录，而是用签名认证，所以要把 qqbot 加进「公开页面」名单。
# 注意：一定要用 +=（追加），不要写成 = （覆盖），否则其他插件的公开页面会失效。
# 如果 local.py 里还没有 APPS_WITH_PUBLIC_VIEWS，就先写一行：APPS_WITH_PUBLIC_VIEWS = []
APPS_WITH_PUBLIC_VIEWS += ["qqbot"]

# 机器人通信密钥：{"密钥编号": "密钥"}。密钥用第 2 步生成的那一串。
# 密钥编号只能用英文字母、数字和 - _ . 等符号（不能有空格、不能用中文，最长 64 个字符），例如 "koishi-1"。
# 可以同时写多把，每把都有效，方便不停机更换（见第 3 节）。
QQBOT_API_KEYS = {
    "koishi-1": "这里换成生成的密钥",
}

# 每天自动对账一次（重新检查所有人的资格和群名片，并清理旧数据）。
# local.py 开头一般已经有 from .base import *，crontab 可以直接用；
# 如果提示找不到 crontab，再加一行：from celery.schedules import crontab
CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {
    "task": "qqbot.tasks.reconcile",
    "schedule": crontab(minute="17", hour="4"),  # 每天 04:17（UTC 时间，即北京时间 12:17）
}
# ---------- aa-qqbot 结束 ----------
```

> ⚠️ 再强调一次：`APPS_WITH_PUBLIC_VIEWS` 要**追加**。如果漏了这一步，机器人的请求会被当成
> 「没登录」跳转到登录页，机器人就无法工作；第 4 步的检查会直接报出来。

可选设置（一般不用改；要改就写在上面那段里）：

| 设置 | 默认值 | 说明 |
|---|---|---|
| `QQBOT_API_RATE_LIMIT` | `120` | 每个密钥每分钟最多请求多少次；`0` 表示不限制 |
| `QQBOT_API_MAX_SKEW` | `300` | 机器人和 AA 的时间最多可以差多少秒 |
| `QQBOT_API_MAX_BODY` | `262144` | 一次请求最大多少字节（256 KB） |
| `QQBOT_AUDIT_RETENTION_DAYS` | `0` | 操作记录（审计日志）保留多少天，每日对账时删除更早的；`0` 表示永久保留（1.1.0 起）。最少 30 天，填 1–29 按 30 天算，因为换绑冷却要读操作记录；被删除的记录所记录的限制（例如被强制解绑后必须用验证码）也随之失效。见第 11 节 |

### 第 4 步：检查配置

```bash
python manage.py check
```

只看 `qqbot.` 开头的提示。输出里可能还有 AA 自己或其他插件的提示（例如 `allianceauth.checks.A002` 提醒升级 Redis），
和本插件无关。**没有 `qqbot.` 开头的提示，就说明配置好了。**

有 `qqbot.E…` 错误时，`migrate` 等命令也会拒绝运行（报 `SystemCheckError`），请先按下表改好 `local.py`，再重新运行 `check`：

| 代码 | 意思 | 怎么改 |
|---|---|---|
| `qqbot.E001` | `APPS_WITH_PUBLIC_VIEWS` 里没有 `"qqbot"` | 按第 3 步**追加** |
| `qqbot.E002` | 没配 `QQBOT_API_KEYS`，或者格式不是 `{...}` 字典 | 按第 3 步配置 |
| `qqbot.E003` | 有密钥太短（少于 32 个字符）。最常见的原因：忘了把「这里换成生成的密钥」换成真正的密钥 | 填入第 2 步生成的密钥 |
| `qqbot.E004` | 有密钥编号格式不对（有空格、中文，或超过 64 个字符），机器人用它发的请求会一直被拒绝 | 改成 `koishi-1` 这样的英文名字，机器人那边同步修改 |
| `qqbot.W001` | 缓存不是 Redis 这类多进程共享的缓存。AA 本身就要求 Redis 缓存，所以基本不会出现 | 检查 `local.py` 有没有改过 `CACHES` |
| `qqbot.W002` | `CELERYBEAT_SCHEDULE` 里没有每日对账任务，每日对账不会自动运行 | 按第 3 步加上 `CELERYBEAT_SCHEDULE["qqbot_reconcile"]` 那几行。如果是在后台「Periodic tasks」里手动添加的，可以忽略 |
| `qqbot.W003` | `QQBOT_AUDIT_RETENTION_DAYS` 既不是 0，也不是不小于 30 的整数（1.1.0 起）。填 1–29 会按 30 天处理；不是整数（例如写成了字符串 `"365"`）或小于 0 时被忽略，操作记录永久保留 | 改成 `0`（永久保留）或不小于 30 的整数，例如 `365`；不打算限制保留时间就删掉这一行 |

### 第 5 步：建立数据表

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

`migrate` 的输出里应该有 `Applying qqbot.0001_v1_initial... OK`。
本插件没有自己的静态文件，`collectstatic` 只是按 AA 插件的惯例顺手跑一下。

### 第 6 步：重启 AA

```bash
exit                                  # 退回有 sudo 权限的账号
sudo supervisorctl restart myauth:
sudo supervisorctl status             # 每一行都应该是 RUNNING
```

`myauth:` 这个组里一般有 celery worker、celery beat，也可能有 gunicorn。看 `status` 的输出：
如果 gunicorn（网页进程）是单独的一行、名字不以 `myauth:` 开头，还要单独重启它，例如 `sudo supervisorctl restart gunicorn`
（名字以 `status` 里显示的为准）。

### 第 7 步：确认装好了

1. 在第 0 步的虚拟环境里运行 `python manage.py check`，没有 `qqbot.` 开头的提示。
2. 在任何能访问 AA 的电脑上运行（把网址换成你们的 AA 网址）：

   ```bash
   curl -i -X POST https://auth.example.com/qqbot/api/v1/health/
   ```

   第一行里有 `401`，最后一行是 `{"ok": false, "error": "missing_headers", …}` → **正常**（没签名当然被拒绝）。
   - 看到 `302` 或一个登录网页 → `APPS_WITH_PUBLIC_VIEWS` 没加好（`qqbot.E001`）；
   - 看到 `400` 网页 → 用的地址不在 `ALLOWED_HOSTS` 里（见[常见问题](#9-常见问题)）；
   - 看到 `404` 网页 → 插件没装上或 AA 没重启。
3. 用服务器上的密钥自己签名调用一次（密钥不会显示出来）。回到第 0 步的虚拟环境和目录，运行 `python manage.py shell`，
   粘贴下面这段后按两次回车：

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

   应该看到 `200 {"ok": true, … "config_ok": true, "problems": []}`。输入 `exit()` 退出。
   （在这一行之前，可能会先打印几行 AA 自己的日志，例如 `Registering hook ...`、`objects imported automatically`，属于正常现象，只看最后以 `200` 开头的那一行。）
   （这个 `bot` 函数就是「模拟机器人」，树莓派上还没有 Koishi 时可以用它测试，见 [`docs/TESTING.md`](TESTING.md)。）
4. `sudo supervisorctl status` 全部是 `RUNNING`；Django 后台 `/admin/` →「Periodic tasks」里有 `qqbot_reconcile`
   （celery beat 重启后才会出现）。
5. 按第 4 节分配权限后，用一个**普通成员账号**（不要用超级管理员）打开左侧菜单「服务（Services）」，能看到「QQ 绑定」卡片；
   QQ 管理员的左侧菜单有「QQ 管理」。
6. QQ 管理员打开「QQ 管理」→「QQ 群」，加上第一个群。
7. 等机器人第一次巡检成功——「QQ 管理」→「QQ 群」里这个群的「名单」一栏有人数和最近的上报时间——再通知老成员去绑定。
   在这之前绑定的老成员都会拿到验证码（1.0.0b5 起卡片会提示他们点「重新生成」直接免验证）。

### 用 Docker 安装的 AA

把 `git+https://github.com/yilifaer/aa-qqbot-plugin.git@v1.1.0` 加进 `conf/requirements.txt`，第 3 步那段写进 `conf/local.py`，
然后按 AA 的 Docker 文档重新构建镜像，并在容器里执行 `python manage.py check` 和 `python manage.py migrate`。

---

## 3. 机器人密钥：更换与保管

密钥的生成方法见第 2 节第 2 步。每把密钥要同时写在两个地方，**密钥编号也要一致**：

1. AA 的 `local.py` 里的 `QQBOT_API_KEYS`；
2. 机器人（Koishi）的插件配置。

密钥编号不是秘密，但也不要让人容易猜到。

**不停机换密钥**：先在 `QQBOT_API_KEYS` 里**加上**新密钥（旧的先留着）→ 重启 AA → 机器人改用新密钥 →
确认机器人正常后，再把旧密钥删掉并重启 AA。例如过渡期间：

```python
QQBOT_API_KEYS = {
    "koishi-1": "旧密钥",
    "koishi-2": "新密钥",
}
```

怀疑密钥泄露时，马上按上面的步骤换一把新的，再删掉旧的。更多注意事项见[第 10 节](#10-安全须知)。

---

## 4. 分配权限

插件有两个权限，都在 Django 后台（`/admin/`）里挂到**状态（State）**或**组（Group）**上：

| 权限 | 在后台里显示为 | 建议挂给 |
|---|---|---|
| `qqbot.basic_access` | `QQ binding / QQ 绑定 \| general \| QQ binding: member, can bind own QQ / QQ 绑定 - 成员：可以绑定自己的 QQ` | **Member 状态** |
| `qqbot.manage` | `QQ binding / QQ 绑定 \| general \| QQ binding: manager, can manage QQ groups and bindings / QQ 绑定 - 管理员：可以在前台管理 QQ 群与绑定` | 新建一个组，例如「QQ 管理」，把管理员加进去 |

操作步骤：

1. **成员权限**：后台 →「Authentication」→「States」→ 点开 **Member**（进入编辑页）→ 在「Permissions」左边框上方的过滤框里输入
   `QQ binding: member`（或 `QQ 绑定 - 成员`；这个框按显示名过滤）→ 选中后点箭头移到右边 → 保存。
2. **管理员权限**：后台 →「Groups」→ 新建或点开「QQ 管理」组（进入编辑页）→ 同样在「Permissions」左边框上方的过滤框里输入
   `QQ binding: manager`（或 `QQ 绑定 - 管理员`），移到右边 → 保存。
3. **把 QQ 管理员加进这个组**：后台 →「Users」→ 点开该管理员 →「Groups」里加上「QQ 管理」→ 保存。
   （在后台新建的组默认是「内部组」，AA 前台的「组管理」页面里看不到它，所以要在后台用户页里加。）

注意：

- **要用超级管理员账号改 State**：AA 5.2～5.5 里只有 `change_state` 权限的 staff 打开 State 编辑页时，权限一栏是只读的。
- **不要去「Authentication」→「Permissions」列表页里找**：那里只按权限代码搜索（AA 的 `PermissionAdmin` 只搜 `codename`），
  要搜 `basic_access` / `manage`；搜「QQ」只会搜到旧版 qqmonitor 的权限。

说明：

- 以后想让盟友、外交官也能进群，只要在后台把 `basic_access` 再挂到对应的状态或组上，不用改代码。
- **超级管理员**虽然能看到所有页面，但判断「能不能进群」时**不会**因为是超级管理员就自动放行，
  和普通人一样要真的被授予 `basic_access`。所以用超级管理员账号测试时，卡片能看到，
  但卡片上方会直接提示「你的账号没有被授予「QQ 绑定 - 成员」权限，请联系管理员；超级管理员身份不算。」，
  机器人也会判定为 `NO_ACCESS`。
  测试成员功能请用普通成员账号，或者确认超级管理员账号所在的状态也挂了成员权限。
- 被停用的账号、没有主角色的账号一律不能进群。

---

## 5. 页面在哪里

| 页面 | 地址 | 谁能看 |
|---|---|---|
| QQ 绑定卡片（绑定、验证码、群号、改昵称、换绑、解绑） | 左侧菜单「服务（Services）」→「QQ 绑定」卡片，地址 `/services/`。成员没有单独的页面，也没有单独的菜单项；旧地址 `/qqbot/` 会自动跳到这张卡片 | 成员 |
| QQ 管理（群、已绑定成员、待处理、设置、操作记录） | 左侧菜单「QQ 管理」（只有有主角色的管理员看得到），或 QQ 绑定卡片底部的「QQ 管理」按钮，地址 `/qqbot/manage/`。「待处理」里还有最近 30 天的免验证绑定（过渡期设得比 30 天长时按过渡期的天数，1.1.0 起），给管理员复核 | QQ 管理员 |
| Django 后台 | `/admin/` 里的「QQ 绑定」 | 超级管理员（绑定和操作记录在后台只能看不能改，改动请走前台；删除 AA 用户时绑定会跟着删除，见下） |

**红色数字**：侧边栏「QQ 管理」旁边和卡片上「QQ 管理」按钮上的红色数字 = 冲突的 QQ 数 + 没选 AA 组的启用中身份组小群数，
点进去就是要处理的页面。未绑定的群成员和免验证复核不算在里面。

装好之后的第一件事：QQ 管理员打开「QQ 管理」→「QQ 群」，把要管理的群一个个加进去。

需要立刻重新对账（比如刚改了很多权限）时，可以在服务器上（第 2 节第 0 步的虚拟环境里）手动运行：

```bash
python manage.py qqbot_reconcile
```

任何 QQ 群的新增、修改、删除都会让所有绑定的「指纹」过期，下一次每日对账会给每个绑定各写一条 `recheck` 事件
（人多时是一大批）。机器人按群合并处理，属于正常现象。

**删除 AA 用户**：在 AA 后台删除用户（单个删，或「Users」列表的「Delete selected users」）会一并删除他的 QQ 绑定，
并通知机器人复查这个 QQ；「QQ 管理」→「操作记录」里显示「账号已删除」。**不需要先解绑。**

- 非超级管理员需要 `auth | user | Can delete user`（注意：不是 `authentication | user | Can delete user`，AA 检查的是前者），
  **外加删除确认页列出的其他删除权限**：Django 会对每个跟着被删、并且在后台注册过的类型检查删除权限。
  常见的有（都是 Can delete …；第一条几乎每个用户都需要）：
  - `notifications | notification`
  - `authentication | character ownership`、`authentication | ownership record`
  - `esi | token`
  - `groupmanagement | group request`

  确认页顶部如果提示「你的账号没有权限删除以下类型的对象：…」，就照着把列出的删除权限补给这个账号
  （用超级管理员账号在后台「用户」或「组」里加）。QQ 绑定不需要单独授权（1.0.0b5 起）。

**改群名片格式**：在「QQ 管理」→「设置」里改了群名片格式后，插件把「重新计算所有人的群名片」交给 Celery 后台去做。连不上消息队列（broker，一般是 Redis）时，1.1.0 起不会再在这次网页请求里直接计算（原来会）：设置已经保存，日志里会有一条 `qqbot: could not queue reconcile` 错误，群名片等下一次每日对账，或者在服务器上手动运行 `python manage.py qqbot_reconcile` 时再更新。在 Django 后台改 Config 也一样。

**同步昵称动作**：后台「Users」列表的动作「Sync nicknames for selected QQ binding accounts」会立刻重算所选用户的群名片并通知机器人，
可以放心用；这个英文名字是 AA 生成的，改不了。

**界面语言**：QQ 绑定卡片和「QQ 管理」页面跟随每个人在 AA 里的语言：任何中文（简体、繁体）都显示简体中文，其他语言（英语、德语、俄语等）都显示英文。
没在 AA 里选过语言的人，按浏览器的语言算：浏览器是中文就显示中文，其他语言显示英文（很多人的 Windows / Chrome 是英文的）。
如果有成员看到的是英文：让他在 AA 左侧菜单底部的语言选择里选「简体中文」（AA 会记住这个选择，以后登录也是中文）。
注意：在 `local.py` 里设 `LANGUAGE_CODE = "zh-hans"` **解决不了**这个问题。浏览器是英文时，Django 会直接用浏览器的语言，
`LANGUAGE_CODE` 只在浏览器没有发送任何 AA 支持的语言时才用得上。
成员填写和管理员设置的内容（入群须知、昵称、群名称等）按原样显示，不会翻译；默认的入群须知是中文，可以在「设置」里改。
机器人接口返回的提示文字和 `python manage.py check` 的检查信息不跟语言走：接口是中文，检查信息是英文和中文各一行。

---

## 6. 机器人那边怎么接

机器人是 Koishi 插件 [koishi-plugin-aaqqbot](https://github.com/yilifaer/Koishi-AAqqbot-plugin)（[npm](https://www.npmjs.com/package/koishi-plugin-aaqqbot)），在另一个仓库。写机器人或管理机器人的人需要这 4 样东西：

1. **接口地址**：AA 的网址加 `/qqbot/api/v1/`，例如 `https://auth.example.com/qqbot/api/v1/`。
   AA 装在子路径下时要带上前缀，例如 `https://example.com/auth/qqbot/api/v1/`。
2. **密钥编号**：`QQBOT_API_KEYS` 里的名字，例如 `koishi-1`。
3. **密钥**：第 2 节第 2 步生成的那一串。请当面给，或者用密码管理器交接；不要发在群里、聊天记录、截图或 issue 里。
4. **接口文档**：本仓库的 [`API.md`](../API.md)。[`KOISHI_START.md`](../KOISHI_START.md) 是当初写 Koishi 插件时的开工说明，现在只作背景参考。

机器人第一次连上后应该先调用 `health`：返回 `ok: true`、`config_ok: true` 就说明网络、密钥和签名都通了。

---

## 7. 升级

先备份数据库（见第 2 节「准备」），然后：

```bash
# 在 AA 虚拟环境里、myauth 目录下（见第 2 节第 0 步）
pip install --upgrade --force-reinstall --no-deps git+https://github.com/yilifaer/aa-qqbot-plugin.git@v1.1.0   # 升级到新版本时把 v1.1.0 换成新版本号
pip freeze | grep aa-qqbot
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
exit            # 退回有 sudo 权限的账号
sudo supervisorctl restart myauth:
```

（gunicorn 不在 `myauth:` 组里时，也要单独重启它，见第 2 节第 6 步。）

说明：

- `--force-reinstall` 保证即使版本号没变，也会装上 GitHub 上最新的代码（不加的话，版本号相同时 pip 什么都不做，也不报错）；
  `--no-deps` 表示不去动 AA 和其他依赖。
- `pip freeze | grep aa-qqbot` 会显示 `aa-qqbot @ git+https://…@<提交号>`，可以确认装的是哪一次提交；
  `pip show aa-qqbot` 显示版本号。更新内容见 [`CHANGELOG.md`](../CHANGELOG.md)，里面如果提到新的设置项，按说明加进 `local.py`。
- 不用 git 时，把地址换成 `https://github.com/yilifaer/aa-qqbot-plugin/archive/refs/tags/v1.1.0.zip`，其余相同。

---

## 8. 卸载

顺序很重要：**先停 AA，再删数据表，最后从 `local.py` 里去掉。** 删数据表时 AA 如果还在运行，「服务」页会对所有人报错。

1. 停止 AA（在有 sudo 权限的账号下）：

   ```bash
   sudo supervisorctl stop myauth:
   ```

   如果 gunicorn 不在 `myauth:` 组里，也把它停掉（见第 2 节第 6 步）。

2. 进入虚拟环境（第 2 节第 0 步），删除本插件的所有数据表（绑定、群配置、操作记录都会被删除，请先备份）：

   ```bash
   python manage.py migrate qqbot zero
   ```

3. 删掉数据库里的定时任务。AA 5 的 celery beat 会把 `CELERYBEAT_SCHEDULE` 抄进数据库，
   只从 `local.py` 删掉并不会让它停下，以后每天仍会发出 `qqbot.tasks.reconcile`，worker 日志里会一直报
   「unregistered task」：

   ```bash
   python manage.py shell -c "from django_celery_beat.models import PeriodicTask; print(PeriodicTask.objects.filter(name='qqbot_reconcile').delete())"
   ```

4. 从 `local.py` 里删掉安装时加的那段（按本文第 2 节安装的，是从 `# ---------- aa-qqbot ----------` 到 `# ---------- aa-qqbot 结束 ----------`；按 README 安装的，是 README 第 3 步那几行）。

5. 卸载插件，并清理后台里残留的两个 QQ 权限（一定要在第 4 步之后做）：

   ```bash
   pip uninstall -y aa-qqbot
   python manage.py shell -c "from django.contrib.contenttypes.models import ContentType; print(ContentType.objects.filter(app_label='qqbot').delete())"
   ```

   只删除本插件的 9 个内容类型、2 个权限（以及这 2 个权限挂在状态、组、用户上的关联），输出类似
   `(…, {'auth.Permission': 2, 'contenttypes.ContentType': 9, …})`。
   **不要用 `python manage.py remove_stale_contenttypes --include-stale-apps`**：它会把这台 AA 上所有已卸载插件留下的权限一起删掉。

6. 启动 AA：

   ```bash
   exit
   sudo supervisorctl start myauth:
   ```

记得同时停掉机器人那边的插件。以后如果重新安装，AA 的事件编号会从头开始，要告诉机器人那边把事件游标清零
（见 [`API.md`](../API.md) 第 8 节）。

---

## 9. 常见问题

| 看到的情况 | 原因 | 怎么办 |
|---|---|---|
| `pip install` 提示 `Cannot find command 'git'` | 服务器没装 git | `sudo apt install git`；或用第 2 节第 1 步的 `.zip` 地址安装 |
| `pip` 说要安装 `allianceauth-5.x`、`Django-5.x` | 这台 AA 是 4.x 或 5.0/5.1 | 先按 AA 官方文档升级 AA，再装本插件（第 2 节第 0 步） |
| 运行 `pip` 或 `python manage.py` 提示找不到命令或 `can't open file 'manage.py'` | 没有进入虚拟环境，或者不在 `myauth` 目录 | 按第 2 节第 0 步操作 |
| `migrate` 报 `SystemCheckError`，里面有 `qqbot.E00…` | `local.py` 配置不对 | 按第 2 节第 4 步的表格改好，再重新运行 |
| 刚装完就有 `qqbot.E003` | 忘了把「这里换成生成的密钥」换成真正的密钥 | 填入第 2 节第 2 步生成的密钥 |
| 重启后网页打不开，日志里有 `NameError: name 'crontab' is not defined` | `local.py` 开头没有 `from .base import *` | 在 aa-qqbot 那段前面加一行 `from celery.schedules import crontab` |
| 日志里有 `NameError: name 'APPS_WITH_PUBLIC_VIEWS' is not defined` | `local.py` 里没有这个变量 | 在 aa-qqbot 那段前面加一行 `APPS_WITH_PUBLIC_VIEWS = []` |
| AA 起不来，日志里有 `ModuleNotFoundError: No module named 'httpx'` | AA 5.2～5.4 用的是 `httpx`，而 2026-10 发布的 `django-esi` 10.x（AA 5.5 要求的版本）改用了 `httpx2`。升级别的包时 pip 可能顺带把 `django-esi` 升到了 10.x | `pip install "django-esi<10"`，然后重启；或者把 AA 升到 5.5。本插件的升级命令带 `--no-deps`，不会引起这个问题 |
| 所有 AA 页面都报错，错误信息里有 `sri_static` | AA 5.2.x / 5.3.x 没有限制 `django-sri` 的版本，而 2026-09 发布的 `django-sri` 1.0 删掉了 AA 页面要用的 `sri_static` 标签 | `pip install "django-sri<1"`，然后重启。AA 5.4 及以上已自带这个限制，最简单的办法是把 AA 升到 5.4 |
| 「服务」页没有「QQ 绑定」卡片 | 账号没有「QQ 绑定 - 成员」权限，或者装完没重启 | 第 4 节；`sudo supervisorctl restart myauth:` |
| 左侧菜单没有「QQ 管理」 | 没有「QQ 绑定 - 管理员」权限；或者账号没有主角色（1.0.0b5 起没有主角色的管理员不显示这个菜单，因为点进去也会被 AA 跳回首页） | 第 4 节；在 AA 首页添加角色并设为主角色 |
| 卡片和「QQ 管理」页面是英文（"QQ binding"、"QQ Admin"） | 这个人在 AA 里选的是英文（或者没选、浏览器是英文） | 让他在 AA 左侧菜单底部的语言选择里选「简体中文」。改 `LANGUAGE_CODE` 没用（第 5 节末尾） |
| 超级管理员能看到卡片，但显示「目前没有你可以加入的群」（1.0.0b5 起卡片上方会直接提示原因） | 超级管理员**不会**自动获得入群资格 | 让这个账号所在的状态（例如 Member）带上成员权限，或者用普通成员账号测试 |
| 老成员填了 QQ 也拿到验证码 | 这个群添加到 AA 已经超过「老成员免验证过渡期」（默认 30 天）；或者机器人还没上报过这个群的完整名单，或名单已超过有效期（默认 7 天） | 先让机器人巡检一次再通知老成员绑定（第 2 节第 7 步）；机器人上报名单之后，卡片会提示他点「重新生成」直接免验证。树莓派上还没有机器人时，用 [`docs/TESTING.md`](TESTING.md) 里的「模拟机器人」 |
| 后台删用户时提示「你的账号没有权限删除以下类型的对象：QQ binding / QQ 绑定」 | 1.0.0b4 的问题 | 升级到 1.0.0b5；临时办法：先在「QQ 管理」里强制解绑再删 |
| AA 5.2.0 / 5.3.0：后台删某些用户（多半是处理过入组申请的人，例如组长、QQ 管理员）时页面报 500，日志里有 `TypeError: __str__ returned non-string` | AA 5.2.0 / 5.3.0 自己的问题（入组申请记录 `RequestLog` 的显示函数返回了数字），5.3.1 已修复；用户没有被删掉，QQ 绑定也没动 | 把 AA 升到 5.3.1 或更高；临时办法：在服务器上运行 `python manage.py shell -c "from django.contrib.auth.models import User; User.objects.get(username='这里换成用户名').delete()"`，QQ 绑定会照常一起删除并通知机器人 |
| AA 5.2.0：成员换了军团或联盟后，群名片里的简称一直是旧的 | AA 5.2.0 更新角色归属时，如果中途查询 ESI 出错，会留下新的军团编号和旧的简称（5.3 起改成一次保存，已修复）；入群资格按编号判断，不受影响 | 把 AA 升到 5.3 或更高；或者在后台「Characters」列表里选中这个角色，执行 AA 自带的更新角色动作 |
| 后台删用户时提示没有权限删除其他类型的对象（通知、角色归属、ESI token 等） | 非超级管理员要有确认页列出的每一种删除权限 | 第 5 节「删除 AA 用户」 |
| 点「服务」或「QQ 管理」被跳回首页，顶部红字「只有主要角色才能执行这个操作。在下面添加一个」 | 账号没设主角色：AA 5.x 在 qqbot 的代码运行之前就把这样的用户从「服务」页和所有插件页面跳回 `/dashboard/` | 在首页添加角色并设为主角色。这期间成员自己不能解绑、改昵称，需要时由 QQ 管理员强制解绑 |
| 改了群名片格式，群名片没变；日志里有 `qqbot: could not queue reconcile` | 重新计算所有人的群名片要交给 Celery 后台，而 AA 连不上消息队列（broker，一般是 Redis）。1.1.0 起这时只记错误日志，不会在网页请求里直接算；设置本身已经保存 | 修好 Redis / celery 的连接，然后在服务器上运行 `python manage.py qqbot_reconcile`，或者等下一次每日对账 |
| 成员看到「尝试太频繁，请稍后再试」（英文 "Too many attempts. Please try again later."） | 填别人已经绑定的 QQ 时会提示「该 QQ 已被绑定」，同一个成员每小时最多被这样告知 5 次（1.1.0 起）；达到上限后，他再填任何别的 QQ（不管空闲还是已被绑定）都得到这同一句话，这样没人能靠一个个试 QQ 摸清联盟里有哪些人。填自己当前的 QQ 不受影响 | 等一小时（从第一次被告知「已被绑定」算起）再试；确实需要这个 QQ 的话，联系 QQ 管理员 |
| 日志里有 `qqbot: API … refused (…) from …` 警告 | 接口请求被拒绝了（401、413、429 或 503）：密钥或签名不对、时间差太大、请求太大、太频繁，或 AA 这边没配好 | 对照 [`API.md`](../API.md) 第 6 节看括号里的错误代码。日志里只会写已配置过的密钥编号（没带写 `(none)`，没配置过的写 `(unknown key id)`），同一个错误代码、同一个来源地址每分钟最多记一行（1.1.0 起）；AA 在 nginx 或隧道后面时来源地址是代理的地址，那就是每个错误代码每分钟一行，真正的客户端看后面的 `X-Forwarded-For` 等代理头 |
| 有人要求删除他的个人数据 | 插件保存了哪些数据、保留多久，见第 11 节 | 第 11.4 节：先让他解绑（或强制解绑、删除 AA 用户），再运行 `python manage.py qqbot_erase --qq <QQ> --user <用户名>`；先加 `--dry-run` 看看会改动什么 |
| 机器人收到 `302` 或登录网页 | `APPS_WITH_PUBLIC_VIEWS` 里没有 `"qqbot"`（`qqbot.E001`） | 在 `local.py` 里**追加**，然后重启 |
| 机器人收到 `400` 网页（Bad Request） | 机器人访问用的地址（例如树莓派的局域网 IP）不在 `ALLOWED_HOSTS` 里 | 机器人用 `SITE_URL` 里的域名访问；或者在 `local.py` 末尾加 `ALLOWED_HOSTS += ["192.168.x.x"]`（换成实际 IP）后重启 |
| 机器人收到 `301` | 网址末尾少了 `/`，或者用 http 访问了只允许 https 的站 | 网址以 `/` 结尾，用 https |
| 机器人收到 `401` 或 `503` 的 JSON | 密钥或签名问题 | 看 `error` 字段，对照 [`API.md`](../API.md) 第 6 节 |
| celery worker 日志一直报 `unregistered task qqbot.tasks.reconcile` | 装完没重启 worker；或者卸载后数据库里的定时任务还在 | 重启；卸载时见第 8 节第 3 步 |
| 每日对账好像没在 04:17 运行 | 时间是 UTC，北京时间是 12:17 | 正常；想改时间就改 `crontab(...)` 里的 `hour` |
| 升级后好像没变化 | 版本号没变时 `pip install -U` 不会重新安装；或者装完没重启 AA | 用第 7 节带 `--force-reinstall --no-deps` 的命令，然后重启。装的是哪一版，看「QQ 绑定」卡片最下面靠右的小字，或「QQ 管理」→「设置」标题栏右边（1.0.1 起） |
| 报错 `Application labels aren't unique, duplicates: qqbot` | `local.py` 里有两处把 `"qqbot"` 加进 `INSTALLED_APPS`（通常是旧版 0.x 留下的） | 删掉旧的那一处，只保留第 2 节第 3 步那一段 |
| 以前装过旧版（0.x）：`local.py` 里有 `QQBOT_GROUP_CHAT`、`QQBOT_PING_GROUP`，数据库里有 `qqbot_qqbinding` 表 | 旧版留下的设置和数据，新版不再使用 | 删掉 `local.py` 里这两行；装好新版后（`qqbot` 还在 `INSTALLED_APPS` 里）运行 `python manage.py shell -c "from django.apps import apps; from django.contrib.contenttypes.models import ContentType; live = {m._meta.model_name for m in apps.get_app_config('qqbot').get_models()}; print(ContentType.objects.filter(app_label='qqbot').exclude(model__in=live).delete())"` 清理旧版的权限（只删 qqbot 里已经不存在的类型）；旧表可以在 `python manage.py dbshell` 里执行 `DROP TABLE qqbot_qqbinding;` 删除（先备份） |
| `migrate` 报 `table "qqbot_…" already exists`（MySQL 是 `Table 'qqbot_…' already exists`） | 这台 AA 装过 1.0.0b1 之前的开发测试版（`1.0.0.dev0`），数据表已经建好了 | 运行 `python manage.py migrate qqbot 0001_v1_initial --fake`，再运行 `python manage.py migrate`，然后重启 |
| 页面报错 `no such table: qqbot_…`（MySQL 是 `Table '…qqbot_…' doesn't exist`） | 数据表没建好：没运行 `migrate`，或者装的是 1.0.0b1 之前的开发测试版 | 用 `pip show aa-qqbot` 确认版本是 1.0.0b1 或更新（不是就按第 7 节升级），再运行 `python manage.py migrate`，然后重启 |

---

## 10. 安全须知

- 密钥只写在服务器的 `local.py` 和机器人的配置里；AA 网页里没有、也不应该有填写密钥的地方。
- 把 `local.py` 发给别人求助之前，先删掉 `QQBOT_API_KEYS`、`SECRET_KEY`、`ESI_SSO_CLIENT_SECRET` 和数据库密码那几行。
- 怀疑密钥泄露时，按第 3 节换一把新的，再删掉旧的。
- 「真的把人踢出群」的开关只在机器人那边；AA 网页上的任何操作都不会直接踢人。
- 正式环境必须用 HTTPS，并保持 `DEBUG = False`。
- 截图、issue、聊天记录里不要出现真实的群号、QQ 号、服务器地址。
- 插件保存了哪些个人数据、保留多久、收到删除请求怎么办，见第 11 节。

---

## 11. 数据保护

本节回答三件事：插件保存了哪些关于人的数据、谁能看到、保留多久；哪些数据会离开 AA；收到「请删除我的数据」时怎么处理。
简版见 [README](../README.zh-CN.md#数据保护)。

### 11.1 保存了什么、谁能看到、保留多久

| 数据 | 存在哪里 | 涉及谁 | 谁能看到 | 保留多久 |
|---|---|---|---|---|
| QQ 号、昵称、状态（已验证 / 老成员免验证）、管理员指定的群名片、绑定时间 | 绑定 | 成员 | 成员本人在卡片里看到自己的 QQ（部分打码）；QQ 管理员在「QQ 管理」里看到完整 QQ；超级管理员还能在 Django 后台只读查看 | 解绑为止；删除 AA 用户时一并删除 |
| 申请验证码时填的 QQ（**可能是别人的 QQ**）、昵称、验证码的哈希（不存明文） | 验证码申请 | 成员，以及被填写了 QQ 的人 | 只有成员本人能在卡片里看到自己待验证的验证码；管理员页面和 Django 后台都不显示 | 验证码过期、被使用或被作废 7 天后删除（只在每日对账运行时）；删除 AA 用户时一并删除 |
| 机器人上报的每个受管群的完整成员名单：群里所有人的 QQ | 群成员名单 | 成员**和没有 AA 账号的群成员** | QQ 管理员，在「待处理」的「未绑定」列表里看到完整 QQ | 到机器人下一次上报这个群的完整名单为止（名单里没有的 QQ 随即删除），或者这个群被删除为止 |
| 每次变更的类型和 QQ | 事件发件箱 | 成员 | 机器人（`events` 接口） | 30 天（只在每日对账运行时删除） |
| 谁在什么时候对哪个 QQ 做了什么：操作者和目标用户的用户名、QQ；详情里可能有昵称的新旧值、换绑前的 QQ、其他认领者的用户名、拿别人验证码来申请的人的 QQ | 操作记录（审计日志） | 成员、管理员，以及外人 | QQ 管理员（「QQ 管理」→「操作记录」和绑定详情页）；超级管理员（Django 后台，只读） | 默认永久，删除 AA 用户后也保留（记录里另存了一份用户名）；设了 `QQBOT_AUDIT_RETENTION_DAYS` 就在每日对账时删除更早的 |
| 成员刚填写的验证码明文、QQ、昵称 | AA 的 session（默认存数据库，并用缓存加速） | 成员 | 只显示给成员本人 | 到卡片发现验证码已经失效（过期、取消或用掉）为止，或者 session 结束为止；操作结果提示最多保留 5 分钟 |
| 用户名、打码的 QQ 号；被拒绝的接口请求还有来源 IP、代理头和密钥编号 | AA 的日志文件 | 成员、调用接口的人 | 能读 AA 日志文件的管理员 | 按 AA 的日志配置（日志轮转） |

要点：

- **事件和过期的验证码只在每日对账运行时才会删除**（第 2 节第 3 步的 `qqbot_reconcile` 定时任务，或手动运行 `python manage.py qqbot_reconcile`）。没有安排每日对账的站点，这两种数据会一直累积（`python manage.py check` 会报 `qqbot.W002`）。
- 群成员名单里有**没有 AA 账号的人**的 QQ：他们不在任何绑定里，也没有自己能操作的地方；要删除只能用第 11.4 节的命令（`--qq` 就够了）。机器人下一次上报该群的完整名单时，已经退群的人才会从名单里消失。
- 操作记录里的 QQ 和用户名是明文。QQ 管理员和超级管理员能看到，数据库备份里也有。

### 11.2 哪些数据离开 AA

- 机器人问到某个 QQ（`check`、`claim`）时，AA 回答**判定结果和原因**；已绑定成员还会给出**群名片**（军团简称、角色名、昵称）。
- 机器人取事件（`events`）时，拿到变更类型和 QQ。
- 机器人向 AA 上报群成员名单（QQ）和入群申请的 QQ 与验证信息原文。AA 只从原文里找验证码，不保存原文。
- 机器人那边保存了什么、保存多久，由机器人（[koishi-plugin-aaqqbot](https://github.com/yilifaer/Koishi-AAqqbot-plugin)）自己决定，不在本插件管辖范围；收到删除请求时也要去那边处理。

### 11.3 限制操作记录的保留时间（可选）

操作记录默认永久保留。想限制的话，在 `local.py` 里加一行（第 2 节第 3 步那一段里）：

```python
QQBOT_AUDIT_RETENTION_DAYS = 365
```

重启 AA 后，每日对账会删除早于这么多天的操作记录（对账日志里有一行 `… old audit log entries pruned`）。

- **最少 30 天**：填 1–29 按 30 天处理，因为换绑冷却（最长 720 小时，也就是 30 天）要读操作记录。`python manage.py check` 会为这两种情况报 `qqbot.W003`；不是整数或小于 0 的值会被忽略，操作记录继续永久保留。
- 被删除的记录所记录的限制也跟着失效：被管理员强制解绑过某个 QQ 的成员，再绑这个 QQ 必须用验证码（决定 #25），这条限制只在那条强制解绑的记录还在时有效；过了保留期，他可以再次走老成员免验证。这是有意的取舍（决定 #32）。
- 想永久保留就不要设置，或者设成 `0`。

### 11.4 处理删除请求

收到「请删除我的数据」后：

1. **先去掉绑定。** 命令在这个 QQ 或这个用户还有绑定时拒绝执行（这样机器人已经收到「失去资格」的通知）。三种办法任选其一：成员自己在卡片里解绑；QQ 管理员在「QQ 管理」→「已绑定成员」里强制解绑；或者在 AA 后台删除这个用户（绑定会跟着一起删除，**不需要先解绑**）。
2. 在服务器上（第 2 节第 0 步的虚拟环境里）先试运行，看看会改动什么：

   ```bash
   python manage.py qqbot_erase --qq 12345678 --user some-username --dry-run
   ```

3. 确认无误后去掉 `--dry-run` 再运行，输入 `yes` 确认：

   ```bash
   python manage.py qqbot_erase --qq 12345678 --user some-username
   ```

选项：

| 选项 | 说明 |
|---|---|
| `--qq` | 此人的 QQ 号 |
| `--user` | 此人的 AA 用户名。AA 用户已经被删除后也能用：操作记录里另存了一份用户名，命令按它匹配 |
| `--dry-run` | 只显示会改动多少条，不做任何修改 |
| `--noinput`（`--no-input`） | 不要求输入 `yes` 确认（脚本里用） |

`--qq` 和 `--user` 至少给一个；两个都给最彻底（只给一个时，只会处理能凭这一个找到的记录）。

命令在**一个事务里**做这些事，任何一步出错都不会改动数据：

- 关于此人的操作记录（QQ、目标用户是他）：去掉 QQ、目标用户和用户名，详情整个换成 `{"erased": true}`；记录本身留下，「QQ 管理」→「操作记录」里看到的是空白的 QQ 和目标；
- 此人作为操作者的记录（例如他自己的绑定、改昵称）：去掉操作者；
- 其他记录详情里出现的他的 QQ 或用户名（例如别人认领同一个 QQ 时「其他认领者」里的名字）：换成 `[erased]`；
- 删除他的验证码申请、事件、群成员名单记录。

做完之后：

- **换绑冷却和强制解绑限制都不再认识这个人**，因为这两项都是读操作记录的：他被强制解绑过的 QQ，再绑时不再要求验证码；他自己解绑后还没走完的换绑冷却也不再计算。
- 命令**不会**删除 AA 用户本身、日志文件、数据库备份，也不会删除机器人那边保存的数据。日志里只有用户名和打码的 QQ 号，随日志轮转清除。
- 如果此人还在受管的 QQ 群里，机器人下一次上报该群的完整名单时，会再次报告他的 QQ，名单里又会有这个号。要让它真正消失，需要他先退出群。

### 11.5 日志里的 QQ 号和被拒绝的请求

- 日志里的 QQ 号都是打码的：9 位及以上保留前 2 位和后 2 位（`123456789` → `12*****89`），5–8 位只保留第一位和最后一位（`12345678` → `1******8`），4 位及以下全部打码（1.1.0 起；原来 5–8 位会露出前后各 2 位）。服务页卡片上的 QQ 也用同样的规则。
- 接口请求被拒绝（401、413、429、503）时记一行 WARNING，内容有错误代码、来源地址、`X-Forwarded-For` / `X-Real-IP` / `CF-Connecting-IP`（各最多 100 个字符，只用于排查）和密钥编号。密钥编号只记 `QQBOT_API_KEYS` 里配置过的；没带写 `(none)`，其他一律写 `(unknown key id)`，所以误填进这个请求头的密钥不会进日志。同一个错误代码、同一个来源地址每分钟最多记一行（1.1.0 起）。

---

## 12. 开发与测试

需要：Python 3.10+、本机运行的 Redis（测试默认用 Redis 的第 15 号库）。

```bash
git clone https://github.com/yilifaer/aa-qqbot-plugin.git
cd aa-qqbot-plugin
python -m venv .venv && . .venv/bin/activate
pip install "allianceauth>=5.2,<6"   # 需要先装好 MySQL 客户端头文件，例如
                                     # apt install default-libmysqlclient-dev build-essential pkg-config
redis-server --daemonize yes         # 如果还没运行 Redis
python manage.py test qqbot
```

- 测试设置在 `testauth/settings.py`，默认用 SQLite。
- 换 Redis 地址：`QQBOT_TEST_REDIS=redis://127.0.0.1:6379/5 python manage.py test qqbot`。
- 用 MariaDB/MySQL 或 PostgreSQL 跑（并发测试 `qqbot/tests/test_core_concurrency.py` 只在这两种数据库上运行）：
  `QQBOT_TEST_DB=mysql`（或 `postgres`），连接信息用 `QQBOT_DB_HOST`、`QQBOT_DB_PORT`、`QQBOT_DB_USER`、
  `QQBOT_DB_PASSWORD`、`QQBOT_DB_NAME` 指定。PostgreSQL 还需要 `pip install "psycopg[binary]"`。
- 完整矩阵用 [tox](https://tox.wiki/)：`tox`（或 `tox -e py312-aa5`、`tox -e py-aa5-mysql`）；
  GitHub Actions 的配置在 `.github/workflows/tests.yml`。
- 提交前请确认：`python manage.py makemigrations qqbot --check --dry-run` 没有新迁移，
  `python -m pyflakes qqbot` 没有报错。
- **发版**：每次交给所有者或 IT 安装的改动，都要提升 `qqbot/__init__.py` 里的 `__version__`（例如 `1.0.0b1` → `1.0.0b2`），
  并在 [`CHANGELOG.md`](../CHANGELOG.md) 里写一段（决定 #20）。`0001_v1_initial` 这个迁移文件不要改名（原因见文件开头的注释）。

开发文档：[`DESIGN.md`](../DESIGN.md)（给所有者看的设计）、[`docs/SPEC.md`](SPEC.md)（实现规格）、
[`DECISIONS.md`](../DECISIONS.md)（决定记录）、[`API.md`](../API.md)（机器人接口）、[`KOISHI_START.md`](../KOISHI_START.md)（Koishi 插件开工说明）。

---

## License

MIT，见 [LICENSE](../LICENSE)。
