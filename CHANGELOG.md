# 更新记录

版本号遵循 [PEP 440](https://peps.python.org/pep-0440/)（`1.0.0b1` = 1.0.0 的第 1 个测试版）。
每次交给别人安装的改动都要提升版本号（`qqbot/__init__.py` 里的 `__version__`）并在这里写一段，
否则装的人很难确认自己装的是哪一版（决定 #20）。升级方法见 [README](README.zh-CN.md#升级)。

## [1.0.0b5] - 2026-09-26

- 「QQ 管理」→「待处理」新增「最近 7 天的免验证绑定」：按时间倒序列出最近 7 天里用「老成员免验证」绑定的人
  （主角色、AA 账号、QQ、群名片、所在的群），每行可以「确认归他」（变成已验证，从列表消失）或「强制解绑」。
  过渡期内有人冒领别人的 QQ 时，管理员能及时发现。不计入「待处理」的数字；冲突仍然单独列在上面（决定 #22）。
- 删除 AA 用户后，如果写给机器人的事件出错，只记日志，不再让删除用户的页面报 500（用户已经删掉了，每天的对账会补上）。
  修改群名片格式后安排后台重算时出错，也同样只记日志。
- 没有数据库改动，不需要新的迁移（照常运行 `python manage.py migrate` 也没关系）。

## [1.0.0b4] - 2026-09-25

- 「老成员免验证」加了过渡期：只在每个群添加到 AA 之后的前 30 天内有效（「设置」里的「老成员免验证过渡期（天）」，
  填 0 关闭），之后所有人都要用验证码。修复了「任何成员都能不填验证码认领别人 QQ」的问题（决定 #21）。
  新迁移 `0004_trusted_window` 只加一个设置项。
- 删除 AA 用户时，通知机器人的事件改为在删除提交之后才写，批量删除很多用户时机器人不会漏掉。
- 同一个群同时收到两份完整名单时按顺序处理，不再把两份名单合在一起。
- 升级后请照常运行 `python manage.py migrate`。

## [1.0.0b3] - 2026-09-25

- 两个权限的名字改成英中双语（英文在前）：`QQ binding: member ... / QQ 绑定 - 成员 ...`、
  `QQ binding: manager ... / QQ 绑定 - 管理员 ...`；后台里的应用名跟随语言（"QQ binding" / 「QQ 绑定」）。
  IT 在后台权限选择框里搜 `QQ binding: member` / `QQ binding: manager` 就能找到。新的迁移 `0002_bilingual_permission_names`
  会把已经装过 1.0.0b1/b2 的站点里的权限一起改名（Django 本身不会改已有权限的名字）。
- 界面跟随用户在 AA 里选的语言：选中文（简体或繁体）的人看到的和以前完全一样；选其他任何语言的人看到英文的
  「QQ 绑定」卡片（"QQ binding"）、侧边栏菜单（"QQ Admin"）和「QQ 管理」各页面（包括提示信息、表单和操作记录）。
  没选过语言的人按浏览器语言显示：浏览器是英文就是英文，这时在 AA 的语言菜单里选「简体中文」即可（改 `LANGUAGE_CODE` 没用）。中文翻译在 `qqbot/locale/zh_Hans/`（`.po` 和编译好的 `.mo` 都随包发布）。
  不翻译的：机器人接口返回的提示文字（仍是中文）、成员和管理员填写的内容、默认的入群须知。
  新的迁移 `0003_i18n_labels` 只更新字段和选项的显示名称，不改数据表结构。
- 系统检查 `qqbot.E001`–`E004`、`W001`、`W002` 的信息和提示改成英中双语（英文一行、中文一行）；
  `qqbot_reconcile` 命令的说明和输出也是双语。
- README：生成密钥单独成一步，命令都放在可以一键复制的代码框里；英文 README 里写英文界面名称（"QQ binding"、"QQ Admin"），
  并说明界面语言跟随 AA 的语言设置。
- 升级后请照常运行 `python manage.py migrate`。

## [1.0.0b2] - 2026-09-25

只改了文档和注释，功能没有变化。

- README 分成英文 `README.md` 和中文 `README.zh-CN.md`，顶部可以互相切换，内容精简成 5 步安装。
  原来的分步说明和常见问题移到 [`docs/GUIDE.md`](docs/GUIDE.md)。
- 插件源代码里的注释和说明文字都加上了中文（英文保留）。
- 系统检查 `qqbot.E002` 的提示直接给出生成密钥的命令，不再指向 README 的某一节。

## [1.0.0b1] - 2026-09-25（第一个公开测试版）

全新重写，与旧版 aa-qqbot 0.x / QQMonitor 不兼容：数据库从头建表，旧的绑定数据不导入，全员重新绑定（决定 #2、#5）。

### 新增

- 「服务」页的「QQ 绑定」卡片：成员的所有操作（绑定、验证码、群号、改昵称、换绑、解绑）都在卡片里完成（决定 #18）。
- 两种绑定方式：已在群里的老成员填 QQ 即生效（依据机器人上报的完整名单）；新成员用验证码入群（决定 #13、#14）。
- 冲突标记：同一个 QQ 被两个账号以「老成员免验证」认领时不处置，由 QQ 管理员在「待处理」里决定（决定 #14、#17）。
- 「QQ 管理」前台页面（只对 QQ 管理员显示）：QQ 群、已绑定成员、待处理、设置、操作记录。
- 两个权限：`qqbot.basic_access`（成员）、`qqbot.manage`（QQ 管理员）。
- 机器人接口 v1：`health`、`groups`、`check`、`claim`、`events`，HMAC-SHA256 签名、防重放、限速，见 [`API.md`](API.md)。
- 群名片自动计算（UTF-8 不超过 60 字节），QQ 管理员可以给个人单独指定。
- 每日对账任务 `qqbot.tasks.reconcile` 和命令 `python manage.py qqbot_reconcile`。
- 系统检查 `qqbot.E001`–`qqbot.E004`、`qqbot.W001`，以及 `qqbot.W002`（`local.py` 里漏了每日对账任务时提醒）。
- 支持 Alliance Auth 5.2–5.x、Python 3.10–3.13、MariaDB/MySQL/PostgreSQL/SQLite；兼容 AA 的深色主题（Darkly）。
- 文档：给 IT 的安装、检查、升级、卸载步骤和常见问题（[docs/GUIDE.md](docs/GUIDE.md)）；树莓派测试清单（[`docs/TESTING.md`](docs/TESTING.md)）；
  Koishi 插件开工说明（[`KOISHI_START.md`](KOISHI_START.md)）。

### 安装和打包

- 第一个数据库迁移命名为 `0001_v1_initial`（不是 `0001_initial`）：旧版 0.x 用的也是 `qqbot` 这个名字，
  而且已经有 `0001_initial`。如果沿用这个名字，装过旧版的 AA 执行 `migrate` 时会显示「No migrations to apply」，
  新数据表一张都不会建，「服务」页随即对所有人报错。
  - 装过本仓库 1.0.0b1 之前的开发测试版（`1.0.0.dev0`）的测试机，升级后 `migrate` 会报「table already exists」，
    处理方法见 [docs/GUIDE.md](docs/GUIDE.md)「常见问题」。
- 从 GitHub 升级改用 `pip install --upgrade --force-reinstall --no-deps …`：版本号不变时，普通的 `pip install -U` 什么都不做，也不报错。
- 打包元数据改用新的许可证写法（`license = "MIT"`，需要 setuptools 77 或更新，从 GitHub 安装时 pip 会自动下载）。
  旧写法 setuptools 已经提示弃用，计划 2027-02 之后不再支持。
- **没有**把 `django-sri` 加进依赖：AA 5.2.x/5.3.x 没有限制它的版本，而 `django-sri` 1.0 删掉了 AA 页面要用的 `sri_static` 标签。
  这是 AA 本身的问题，AA 5.4.0 起已经自己限制为 `django-sri<1`；本插件如果也写死 `django-sri<1`，
  等以后的 AA 5.x 需要 `django-sri` 1.x 时，安装本插件反而会让 pip 失败或把 AA 降级。
  用 AA 5.2/5.3 的站点请按 [docs/GUIDE.md](docs/GUIDE.md)「常见问题」手动运行 `pip install "django-sri<1"`。

### 已知限制

- QQ 群名片的实际长度上限还没实测，暂按 60 字节截断。
- 号主还在群里时产生的冲突，只能由 QQ 管理员确认或强制解绑来解决。
- 机器人（Koishi）插件还在开发。没有机器人时，「老成员免验证」不会生效，验证码也无法被使用
  （测试时可以用 [`docs/TESTING.md`](docs/TESTING.md) 里的「模拟机器人」）。
- 每日对账的时间写在 `local.py` 里，按 UTC 计算（默认 04:17 UTC，即北京时间 12:17）。
