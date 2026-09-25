# 更新记录

版本号遵循 [PEP 440](https://peps.python.org/pep-0440/)（`1.0.0b1` = 1.0.0 的第 1 个测试版）。
每次交给别人安装的改动都要提升版本号（`qqbot/__init__.py` 里的 `__version__`）并在这里写一段，
否则装的人很难确认自己装的是哪一版（决定 #20）。升级方法见 [README](README.zh-CN.md#升级)。

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
