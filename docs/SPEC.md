# aa-qqbot 实现规格（开发者用）

> 面向写代码的人。所有者看的是 `DESIGN.md`，决定记录在 `DECISIONS.md`。
> 本文件与 `DESIGN.md` 冲突时，以本文件为准。发现本文件有漏洞，请在 PR 里提出，不要自行偏离。

## 0. 约定

- 目标：AllianceAuth 5.2–5.x、Django 5.2、Python ≥ 3.10。
- 测试：`python manage.py test qqbot`，设置在 `testauth/settings.py`。需要本机 Redis（`redis-server`），使用 DB 15。
- 用户可见文字一律中文；代码、注释、标识符用英文。
- 日志：`logger = get_extension_logger(__name__)`（来自 `allianceauth.services.hooks`）。INFO 及以上级别不记录完整 QQ 号，一律用 `mask_qq()`。
- 时间一律用 `django.utils.timezone.now()`，需要可测试的函数接收可选参数 `now=None`。
- QQ 号、群号一律经 `models.normalize_qq()` 规范化：纯 ASCII 数字，5–11 位，不以 0 开头。
- **不参考、不复制**旧 QQMonitor 的源码（clean-room）。

## 1. 文件归属

| 文件 | 负责内容 |
|---|---|
| `qqbot/models.py`、`migrations/0001_v1_initial.py`、`app_settings.py`、`apps.py`、`urls.py`、`auth_hooks.py`、`templates/qqbot/base.html`、`testauth/` | 地基（已写好）。改动须说明理由 |
| `qqbot/core/` | 领域逻辑：判定、绑定操作、群名片、事件、审计、验证码。**页面和接口只能通过 core 修改数据** |
| `qqbot/api/`、`API.md` | 机器人接口 |
| `qqbot/views/member*.py`、`templates/qqbot/member/`（卡片用的片段）、`service_hook.py`、`templates/qqbot/service_ctrl.html` | 服务页的 QQ 绑定卡片（成员的全部界面）及其 POST 视图 |
| `qqbot/views/manage*.py`、`templates/qqbot/manage/` | 管理页面 |
| `qqbot/signals.py`、`tasks.py`、`checks.py`、`admin.py`、`management/`、`README.md` | 集成、后台任务、系统检查、安装文档 |
| `qqbot/tests/test_<领域>_*.py` | 各部分自己的测试；公共测试工具放 `qqbot/tests/utils.py`（core 负责创建） |

## 2. 数据模型（见 `models.py`）

- `General`：只承载权限 `qqbot.basic_access`（成员）和 `qqbot.manage`（QQ 管理员）。
- `Config`：前台可改的单例设置，用 `Config.get_solo()` 读取。
- `QQGroup`：受管群。`kind` 为 `fixed`（固定群）或 `role`（身份组小群）。`required_groups` 只对 `role` 生效，满足任意一个即可。`last_roster_at` 记录最近一次完整名单上报的时间。
- `Binding`：一个用户一个。`status` 为 `verified` 或 `trusted`。`verified_qq` 在已验证时等于 `qq`，否则为 NULL，并带普通唯一索引（MySQL/MariaDB 不支持条件唯一约束），由 `save()` 同步、检查约束兜底；`trusted` 可以重复，重复即为冲突。
- `Lock`：128 行预建的锁行，由 `core/locks.py` 按「用户 → QQ → 数据行」的固定顺序加 `SELECT … FOR UPDATE`，所有绑定写操作都必须经过它，避免死锁和并发破坏约束。
- `BindCode`：待验证的提交。只存 HMAC，同一用户最多一条有效记录（未用、未作废、未过期）。
- `RosterEntry`：每个群最近一次完整名单中的 QQ。
- `Event`：机器人拉取的发件箱，游标为自增 `id`。
- `AuditLog`：操作记录。

## 3. core（`qqbot/core/`）

### 3.1 `core/util.py`
- `mask_qq(qq) -> str`：保留前 2 位和后 2 位，中间用 `*` 填充，例如 `12****78`；为空时返回 `""`。
- `validate_nickname(s) -> str`：去掉首尾空白后，长度 1–12 个字符，只允许中日韩统一表意文字、字母、数字、空格和 `_-.·()`，不允许连续空格。返回清理后的昵称；不合法时抛 `ValidationError`，附中文消息。

### 3.2 `core/codes.py`
- 格式：`QQ-` 加 6 个字符，字符集 `23456789ABCDEFGHJKMNPQRSTUVWXYZ`（去掉易混字符）。生成用 `secrets`。
- `hash_code(code) -> str`：先规范化（大写，把 `QQ` 后面缺失的连字符补上），再计算 `hmac_sha256(settings.SECRET_KEY, "qqbot-code:" + code)` 的十六进制。
- `extract_code(text) -> str | None`：在任意文字（入群申请备注）里找 `QQ-?[验证码字符]{6}`，不区分大小写，可以前后有别的文字，取第一个匹配，返回规范化后的验证码。匹配前先做 NFKC 规范化（全角字母、数字、`－` 转半角），并把常见破折号（`‐‑‒–—―−ー`，连续多个算一个）当作 `-`。

### 3.3 `core/audit.py`
- `log(action, *, actor=None, qq="", target_user=None, **detail) -> AuditLog`：同时快照 `actor_name` 和 `target_name`（用户名）。`actor=None` 表示机器人或系统。

### 3.4 `core/access.py`（资格判定的基础）
- `has_base_access(user) -> bool`：`user.is_active`、有主角色、并且通过**用户自身权限、所在组权限或所在 State 的权限**拿到了 `qqbot.basic_access`。**不使用** `user.has_perm`（超级管理员会自动通过）。
- `base_access_user_ids(user_ids) -> set[int]`：批量版本，只要求权限条件，用常数次查询完成。`is_active` 和主角色由调用方另外检查。
- `user_group_ids(user_ids) -> dict[int, set[int]]`：批量取用户所属的 AA 组 id。

### 3.5 `core/eligibility.py`
```python
ALLOW, DENY, REVIEW = "allow", "deny", "review"
# reasons
OK, NOT_BOUND, PENDING_VERIFY, CONFLICT, USER_INACTIVE, NO_MAIN, NO_ACCESS, \
    GROUP_ROLE_MISSING, GROUP_MISCONFIGURED = ...

@dataclass(frozen=True)
class Decision:
    qq: str
    decision: str
    reason: str
    card: str | None   # 只在 allow 时给出

def evaluate(group: QQGroup, qqs: Iterable[str], now=None) -> dict[str, Decision]
def evaluate_binding(binding: Binding, groups=None) -> dict[int, Decision]   # 按 group.pk
def groups_for_user(user) -> list[QQGroup]   # 该用户当前有资格进的有效群（给服务卡片用）
# 有有效验证码（待验证）时，按用户层面的规则列出可申请的群；否则按绑定判定；冲突时返回 []
def evaluate_many(groups, qqs, now=None, config=None) -> dict[str, dict[int, Decision]]
```
对群 G 中一个 QQ 的判定规则，**按顺序**执行，先命中的生效：
1. 该 QQ 没有绑定：如果有这个 QQ 的有效 `BindCode`，返回 `deny/PENDING_VERIFY`；否则返回 `deny/NOT_BOUND`。
2. 有 `verified` 绑定时，只看这一条（同号的 `trusted` 忽略）。否则，若同一 QQ 有 ≥2 条 `trusted` 绑定，返回 `review/CONFLICT`。
3. 取唯一的那条绑定对应的用户 U：
   - `not U.is_active` → `deny/USER_INACTIVE`
   - 没有主角色 → `deny/NO_MAIN`
   - 不满足 `has_base_access` 的权限条件 → `deny/NO_ACCESS`
   - G 是 `role` 类型且没有配置任何 `required_groups` → `review/GROUP_MISCONFIGURED`
   - G 是 `role` 类型且 U 不在其中任何一个组 → `deny/GROUP_ROLE_MISSING`
   - 以上都不满足 → `allow/OK`，附上群名片
- `evaluate` 必须用**常数次查询**完成，与 QQ 数量无关（对大的 `IN` 列表按 500 分块）。在测试里用 `assertNumQueries` 证明：10 个和 300 个 QQ 的查询次数相同。

### 3.6 `core/cards.py`
- `render_card(binding, config=None) -> str`：`card_override` 不为空时直接用它；否则用 `config.card_format` 格式化，可用占位符为 `corp_ticker`、`alliance_ticker`、`character_name`、`nickname`，取值来自主角色（`EveCharacter.corporation_ticker`、`alliance_ticker`、`character_name`）。格式串有问题（未知占位符或语法错误）时回退到 `Config.DEFAULT_CARD_FORMAT`，并写 warning 日志。
- `preview_card(user, nickname, config=None) -> str`：给页面预览用，不需要已有绑定。
- 结果按 **UTF-8 60 字节**截断：先缩短角色名，仍然超长再截断整串，不能切断多字节字符。常量 `CARD_MAX_BYTES = 60`。
- 结果去掉首尾空白，并压缩连续空白。
- `full_card(user, nickname, config=None)`：截断前的自动名片；`is_shortened(binding, config=None)`：自动名片是否被缩短过（管理员指定的名片不算）。服务卡片用它们按 DESIGN §6 提示「角色名已自动缩短」。

### 3.7 `core/roster.py`
- `update_roster(group, qqs, now=None)`：在一个事务里整体替换该群的名单：删除不在列表里的，新增列表里有的（`bulk_create(ignore_conflicts=True)`），更新 `seen_at`，设置 `group.last_roster_at=now`。无效 QQ 静默丢弃。
- `in_fresh_roster(qq, now=None) -> bool`：该 QQ 出现在某个**有效群**的名单里，并且该群的 `last_roster_at >= now - Config.roster_max_age_days`。
- `unbound_roster(group=None)`：有效群名单中没有任何绑定的 QQ（给管理页「待处理」用）。

### 3.8 `core/events.py`
- `emit(kind, qq="")`。
- `emit_groups_changed()`：写一条 `groups` 加一条 `recheck_all`。
- `refresh_binding(binding) -> bool`：计算 `fingerprint = sha256(按群 pk 排序的判定)[:32] + sha256(群名片)[:32]`。判定部分变化时写 `recheck`，名片部分变化时写 `card`，然后保存 fingerprint。第一次计算（fingerprint 为空）也写事件。
- `refresh_user(user_or_id)`：用户有绑定就调用 `refresh_binding`，否则什么都不做。
- `refresh_all() -> int`：遍历所有绑定，返回写出的事件数（给每日对账用）。
- `prune(now=None)`：删除 30 天前的事件、7 天前已过期、已用或已作废的验证码。
- `poll(after, limit, now=None) -> EventPage(events, last_id, has_more)`：给 `events` 接口用。**只返回创建超过 `EVENT_VISIBILITY_DELAY`（10 秒）的事件**，遇到第一条更新的事件就停止，避免游标越过还没提交的事务写出的事件。
- `refresh_all()` 每批 500 个绑定调用 `eligibility.evaluate_many`，查询次数不随绑定数量增长。

### 3.9 `core/bindings.py`（所有写操作，全部在 `transaction.atomic()` 中完成）
返回值用 dataclass，字段 `ok: bool`、`outcome: str`（机器可读），以及成功或失败时的中文 `message`。

- `submit(user, qq, nickname, now=None) -> SubmitResult`
  - QQ 或昵称不合法 → `outcome="invalid"`。
  - QQ 与自己当前绑定相同 → 只更新昵称（`nickname_updated` 或 `unchanged`），写审计 `NICKNAME` 并写 `card` 事件。
  - QQ 已被**别人** `verified` → `outcome="taken"`（提示联系 QQ 管理员）。
  - 用户已有绑定，且 `qq_changed_at` 还在 `Config.rebind_cooldown_hours` 冷却期内 → `outcome="cooldown"`，附剩余时间。**成员自己解绑后冷却继续有效**：`unbind` 把被删绑定的 `qq_changed_at` 记进审计 `UNBIND` 的 `detail`，没有绑定时取冷却窗口内最近一条 `UNBIND` 的这个时间判断；重新提交刚解绑的同一个 QQ 不算换号；管理员强制解绑（`FORCE_UNBIND`）不延续冷却。
  - `in_fresh_roster(qq)` → 先检查免验证绑定的频率限制（每个用户每小时最多 5 次，缓存计数，与验证码计数分开），超出返回 `outcome="rate_limited"`；否则 **老成员免验证**：新建或替换为 `trusted` 绑定，`verified_via=""`，`verified_at=None`，清空 `card_override`（换号时），设置 `qq_changed_at=now`，作废该用户所有有效验证码。换号时对旧 QQ 写 `recheck` 事件、写审计 `REBIND`，否则写 `BIND`。存在其他 `trusted` 同号绑定时写审计 `CONFLICT`。对新 QQ 调用 `refresh_binding`。返回 `outcome="trusted"` 或 `"conflict"`。
  - 否则 → **待验证**：作废旧验证码，生成新验证码，写审计 `CODE`，返回 `outcome="pending"`，并在结果里带上**明文验证码**和过期时间（明文只出现这一次，页面可以存在会话里）。现有绑定保持不变，等验证码被使用才替换。
  - 频率限制：每个用户每小时最多生成 5 个验证码（用缓存计数），超出返回 `outcome="rate_limited"`。
- `live_code(user, now=None) -> BindCode | None`。
- `cancel_code(user)`：作废有效验证码。
- `claim(qq, text, now=None) -> ClaimResult`（给机器人接口用）
  - `outcome` 取值：`no_code`、`code_invalid`（找不到）、`code_expired`、`code_used`（已用或已作废）、`qq_mismatch`、`claimed`。
  - 取验证码时加 `select_for_update`，把「标记已用」做成条件更新（`filter(pk=…, used_at__isnull=True, invalidated_at__isnull=True).update(used_at=now)`，要求影响行数 == 1），确保并发时只成功一次。
  - `qq_mismatch`：作废这个验证码（防止被抢用或暴力尝试），写审计 `CLAIM_FAILED`。
  - 成功：删除该 QQ 的其他所有绑定（其他用户的 `verified` 表示被接管，`trusted` 表示冲突解决），每条都写审计并记录 `detail`。然后把本用户的绑定设为 `verified`，`verified_via=code`，`verified_at=now`，昵称取验证码上的，换号时设置 `qq_changed_at`。对旧 QQ 写 `recheck`，写审计 `VERIFY`，并调用 `refresh_binding`。
- `confirm(binding, actor, expected_qq=None)`：管理员确认。`expected_qq` 是管理员页面上显示的 QQ；加锁后绑定的 QQ 已经不是它（成员换绑时绑定行的 pk 不变）→ `outcome="qq_changed"`，什么都不改。管理页的确认表单必须带上隐藏字段 `qq`。同号已有别人的 `verified` 时拒绝。否则设为 `verified`（`via=manager`），删除同号其他 `trusted` 绑定（审计 `CONFLICT_RESOLVED`），写审计 `CONFIRM`，并刷新。
- `unbind(user, actor=None, forced=False, expected_qq=None)`：删除绑定，作废验证码，写 `recheck` 事件，写审计 `UNBIND` 或 `FORCE_UNBIND`（`detail` 含 `status` 和 `qq_changed_at`）。`expected_qq` 同 `confirm`（管理员强制解绑时使用），不一致时返回 `qq_changed`。
- `cooldown_ends(qq_changed_at, now=None, config=None)`：冷却结束时间（已结束为 `None`），给服务卡片的换绑、解绑提示用。
- 已知限制：号主还在群里时，冲突的 QQ 在新鲜名单里，任何一方提交都只会得到 `trusted`/`conflict`，拿不到验证码；因此这类冲突只能由管理员确认或强制解绑来解决，管理页不能引导成员「用验证码胜出」。
- `set_nickname(user, nickname)`、`set_card_override(binding, card, actor)`（空串表示清除；按 60 字节校验）：写审计并刷新。
- `conflicts() -> list[tuple[qq, list[Binding]]]`：没有 `verified`、且 `trusted` 绑定数 ≥ 2 的 QQ。
- `on_user_deleted(user)`：在 `pre_delete` 时调用：写 `recheck` 事件和审计 `USER_DELETED`（快照 QQ）。

### 3.10 `qqbot/tests/utils.py`
- `create_member(username, *, corp_ticker="IGC", character_name=None, state_perm=True, active=True)`：用 `AuthUtils` 创建带主角色的用户，把 `basic_access` 挂到用户的 state 上（与生产配置一致）。
- `create_group(group_id, kind="fixed", required=(), **kw)`、`bind(user, qq, status="verified", nickname="n")`、`put_in_roster(group, qqs)`。

## 4. 机器人接口（`qqbot/api/`）

- 路由（在 `urls.py` 的嵌套 include 里）：`/qqbot/api/v1/{health,groups,check,claim,events}/`，URL 名为 `api_<name>`，视图函数在 `qqbot.api.views`，函数名与端点名相同（`auth_hooks.PUBLIC_VIEWS` 依赖这些名字）。
- 只接受 `POST`，请求和响应都是 `Content-Type: application/json`，`csrf_exempt`。**任何情况**都返回 JSON，包括视图内部未捕获的异常（返回 500 `internal_error`）。
- 请求头：`X-QQBot-Key`（key id，可打印 ASCII、无空格、1–64 字符）、`X-QQBot-Timestamp`（Unix 秒，整数，1–16 位数字：毫秒时间戳能通过格式检查，得到 `stale_timestamp`）、`X-QQBot-Nonce`（16–64 位 `[A-Za-z0-9_-]`）、`X-QQBot-Signature`（小写十六进制）。
- 签名：`hex(HMAC-SHA256(secret, "POST\n" + request.path + "\n" + timestamp + "\n" + nonce + "\n" + hex(sha256(raw_body))))`。
- 校验顺序：没配置密钥 → 503 `misconfigured`；请求头缺失或格式错 → 401 `missing_headers`；未知 key → 401 `unknown_key`；时间戳偏差超过 `api_max_skew()` → 401 `stale_timestamp`；签名错 → 401 `bad_signature`（`hmac.compare_digest`）；请求体过大 → 413 `too_large`；nonce 用过 → 401 `replayed_nonce`（`cache.add("qqbot:nonce:"+key+":"+nonce, 1, 2*skew+60)`，**签名通过后**才记录 nonce）；超过每分钟限速 → 429 `rate_limited`；JSON 或字段不合法（包括嵌套过深导致的 `RecursionError`）→ 400 `bad_request`。
- 错误格式：`{"ok": false, "error": "<code>", "message": "<中文说明，告诉运维该怎么做>"}`。
- 成功格式：`{"ok": true, "server_time": "<ISO8601>", ...}`。
- 端点：
  - `health {}` → `{version, config_ok, problems: [...]}`，其中 `problems` 为 system check 能发现的问题的简短代码。
  - `groups {}` → `{groups: [{group_id, name, kind}]}`，只含有效群。
  - `check {group_id, qqs: [..≤3000], full_roster: bool}` → `{group_id, results: [{qq, decision, reason, card}]}`。未知或无效的群 → 404 `unknown_group`。`full_roster=true` 表示 `qqs` 是该群的完整成员名单，调用 `update_roster`。结果中无效的 QQ 原样返回，`decision=review, reason=BAD_QQ`；只原样返回整数和 64 字符以内、可打印、能编码为 UTF-8 的字符串，其他值返回 `null`（一个坏项不能让整批请求失败）。
  - `claim {qq, text, group_id?}` → `{claimed: bool, outcome, result: {qq, decision, reason, card} | null}`。带了 `group_id` 时给出该群的判定。
  - `events {after: int ≥ 0, limit?: 1..500 默认 200}` → `{events: [{id, kind, qq, created_at}], last_id, has_more}`，调用 `core.events.poll`；事件最多延迟约 10 秒可见（`API.md` 要写明）。
- `API.md`（中文 + 字段表）：完整契约、错误码表、**签名测试向量**（固定 secret、时间戳、nonce、请求体，给出预期签名，并写一个测试确保向量与实现一致），以及给 Koishi 端的实现要点：`redirect: 'manual'`、只接受 200、三态处理、`review` 永不处置、昵称和名片要转义、入群申请一律走 `claim` 并只看 `result.decision`、事件按群合并成批量 `check`、大规模 `deny` 的熔断（一轮移出人数超过阈值时不处置、等人工确认）、子路径部署时签名路径带前缀。

## 5. 成员界面：服务页的「QQ 绑定」卡片（决定 #18）

- 成员**没有单独的页面**，所有操作都在 AA 服务页（`/services/`，URL 名 `services:services`）的卡片里完成，样子见 `DESIGN.md` §4.1、§4.2。
- `service_hook.QQBotService`：`name="qq"`，`title` 为「QQ 绑定」，`access_perm="qqbot.basic_access"`，`service_active_for_user = has_perm`。`render_services_ctrl(request)` 用 `views.member.card_context(request)` 的结果（加上 `service_name`）渲染 `qqbot/service_ctrl.html`。`validate_user`、`delete_user` 等回调**什么都不做**（真正的正确性由 signals 和每日对账保证）；`update_groups`、`update_all_groups` **不要覆盖**（AA 的用户后台会为覆盖了它们的服务加一个无用的「Sync groups」操作）。`sync_nickname(user)` 调用 `signals.schedule_refresh_user(user.pk)`（AA 在 pre_save 里、自己的事务中调用它，此时新数据还没写入），外层包 try/except。
- `card_context(request, now=None)`：所有规则仍在 core，这里只取数据。`view` 取 `no_main` / `unbound` / `pending`（有有效验证码，包括换绑中）/ `bound`；另有 `status`（`member_status`）、`badge_label`/`badge_class`（未启用 `text-bg-warning`，同 AA 自己的 Disabled；待验证蓝；已启用绿；冲突 / 已被占用红。有绑定时标签显示现在绑定的状态，换绑中也一样；冲突 / 已被占用的红标签在已绑定和待验证两种视图里都配有 `#qqbot-problem` 红色说明）、`groups`（`groups_for_user` 按固定群、身份组小群分组；只在待验证、或已绑定且没有冲突时查询）、`rules_text`、群名片前缀拆分与长度提示、会话里的验证码（与有效验证码的哈希一致才显示）、`minutes_left`、`card`/`card_shortened`、`cooldown_ends`/`cooldown_left`（还要等多久，`core.bindings.format_remaining`；卡片只显示剩余时间，不显示钟点：AA 按 `TIME_ZONE`（通常 UTC）显示时间，成员看的是北京时间）/`cooldown_hours`、`is_manager`（`has_perm("qqbot.manage")`）。每次打开服务页都会执行：查询次数固定，不随群数量增长（有测试）。会话里的验证码已失效时从会话删除，并在卡片里提示一次（`#qqbot-stale`）、把上次填的内容预填回表单：未绑定时预填绑定表单，换绑时展开「换绑」小表单（`open_panel="rebind"`）并预填新 QQ。
- 操作结果（`result`）：POST 视图把 `{level, text, ok, panel, qq, nickname, at}` 存在会话 `qqbot_result` 里，`card_context` 取出后删除（只显示一次；超过 `RESULT_MAX_AGE` 秒、或格式不对的丢弃），卡片在正文最上面用 alert 显示（`#qqbot-result`，success / info / warning / danger）。**不用 Django messages**：AA 把它们显示在整排服务卡片的上方，卡片不在第一排时（手机、较窄的笔记本），跳到 `#qqbot` 后提示条在屏幕外。操作失败（`ok` 为假）时，`panel` 指明刚才用的表单：未绑定视图预填绑定表单的 QQ 和昵称；已绑定视图展开对应的小表单（`open_panel` 为 `nickname` / `rebind` / `unbind`，加 `show` 类，切换按钮 `aria-expanded="true"`），并预填刚才填的昵称或新 QQ。填过的内容只显示在本人的卡片里，照常转义。
- 卡片模板 `qqbot/service_ctrl.html`：独立的 `<div class="card mx-2 mb-3 …" id="qqbot">`（不继承 `services_ctrl_base.html`，那个模板把文字居中、宽度也放不下表单），宽 `26rem`、`max-width: calc(100% - 1rem)`；标题栏（QQ 图标 + 标题 + 状态徽章）、正文、页脚按钮。卡片内所有 DOM id 以 `qqbot-` 开头且唯一。
  - 未绑定：一句说明；表单 POST 到 `member_submit`：群名片前缀（`[ticker] 角色名 - `，只读）+ 昵称输入框的 input-group、QQ 输入框、「绑定」按钮、小字提示；下面用小字显示入群须知（`Config.rules_text`，**转义后**把换行转成 `<br>`）。
  - 待验证：验证码（大号等宽字体）或「验证码只在生成它的浏览器里显示，请点重新生成」、剩余分钟数、3 步说明、可申请的群；页脚「重新生成」（POST `member_submit`，`regenerate=1`）和「取消」（POST `member_code_cancel`）。
  - 已绑定：打码的 QQ 与状态、群名片、可加入的群（冲突 / 已被占用时不显示群号，改为红色提示「请联系 QQ 管理员」）、入群须知；页脚「改昵称」「换绑」「解绑」用 Bootstrap collapse 在卡片里展开小表单（`data-bs-parent`，一次只开一个）；没有 JavaScript 时 `<noscript>` 样式把三个小表单全部显示、隐藏切换按钮。解绑表单必须勾选 `confirm` 复选框（`required`）。
  - 管理员（`qqbot.manage`）在页脚多一个「QQ 管理」按钮，链接 `manage_index`。
  - 主题（卡片和管理页、`base.html` 都适用）：只用 Bootstrap 组件类（alert、badge、btn、list-group、form）和 `text-body-secondary`。AA 的 darkly 主题没有设置 `data-bs-theme="dark"`，Bootstrap 根变量仍是浅色值：`bg-body-tertiary`、`bg-body-secondary`、`text-*-emphasis` 会变成浅底或深褐色字；它的 secondary 色 `#444` 与卡片标题栏、页脚同色。所以禁止这些类以及 `bg-light`、`bg-white`、`table-light`、`alert-light`、`text-dark`、`btn-light`、`*-secondary`（`text-body-secondary` 除外）、`btn-outline-secondary`、`text-bg-light` 和固定颜色；有测试检查 `qqbot/templates/qqbot/` 下的所有模板（包括 `{% if %}` 里写的类）。管理页的中性按钮（「清除」「返回」「恢复自动群名片」）用 `btn-info`，提示用 `text-danger` / `text-bg-warning` / `text-bg-info` / `text-bg-primary`。
- `member_urls.urlpatterns`（URL 名不变，旧链接继续可用）：`""` → `my_qq`（登录 + 权限保护，只重定向到服务页卡片）、`submit/`、`code/cancel/`、`nickname/`（只接受 POST）、`unbind/`（POST 且勾选 `confirm` 才解绑；没勾选时提示「请先勾选「我确认要解除绑定」……」；GET 重定向回卡片）。
- 所有视图：`@login_required` + `@permission_required("qqbot.basic_access", raise_exception=True)`，修改操作只接受 POST。操作完成后一律重定向到 `reverse("services:services") + "#qqbot"`，结果显示在卡片里（见上面的 `result`），不写 Django messages。卡片设 `scroll-margin-top: 1rem`（AA 的内容栏本身在顶栏下面，不需要更多）。没有主角色时 POST 只重定向，卡片本身会说明。
- 菜单项（`auth_hooks.QQBotMenuItem`）：文字「QQ 管理」，链接 `manage_index`，**只对有 `qqbot.manage` 的人显示**；普通成员没有菜单项（用 AA 自带的「服务」菜单）。`base.html` 只给管理页用：没有「我的 QQ」标签；有 `basic_access` 的管理员在顶部看到回到服务页卡片的小链接。
- QQ 输入框不能设比 `SubmitForm` 更短的 `maxlength`（浏览器会静默截掉粘贴内容的末位，变成另一个合法 QQ）。

## 6. 管理页面

- `manage_urls.urlpatterns`，URL 名称：`manage_index`（重定向到 `manage_bindings`）、`manage_groups`、`manage_group_create`、`manage_group_edit <pk>`、`manage_group_delete <pk>`（确认后 POST）、`manage_bindings`（搜索：用户名、角色名、QQ、军团简称；筛选：状态、冲突；每页 50 条）、`manage_binding <pk>`（详情：各群判定表、名片预览；操作：设置或清除名片、确认、强制解绑）、`manage_pending`（冲突列表加各群未绑定的 QQ）、`manage_settings`（Config 表单）、`manage_audit`（分页，可按 QQ 筛选）。
- 所有视图：`@login_required` + `@permission_required("qqbot.manage", raise_exception=True)`，修改操作只接受 POST。管理员可以看到完整 QQ 号。
- 群表单规则：`role` 类型必须至少选择一个 `required_groups`；群号经 `normalize_qq` 规范化且唯一。保存或删除后调用 `emit_groups_changed()` 并写审计 `GROUP`。设置保存后写审计 `CONFIG`，群名片格式变化时调用 `refresh_all()`（数量大时交给 Celery 异步执行）。
- 所有修改只能通过 core 完成。

## 7. 集成

- `signals.py`：每个接收器只做 `transaction.on_commit(lambda: _safe(core.events.refresh_user, user_id))`，接收器本身整体包 try/except 并记录日志，**永远不向外抛异常**。需要覆盖：
  - AA 的 `state_changed`
  - `User` 的 `pre_save`/`post_save`，检测 `is_active` 变化
  - `User.groups` 的 `m2m_changed`
  - `User.user_permissions` 的 `m2m_changed`
  - `UserProfile` 的 `post_save`（主角色变化）
  - `EveCharacter` 的 `post_save`（作为某人主角色时，名字、军团或联盟变化）
  - `User` 的 `pre_delete` → `bindings.on_user_deleted`
  - `Group.permissions` 或 `State.permissions` 的 `m2m_changed` 涉及 `basic_access` 时，以及任何 `Group` 的成员变化波及身份组小群时 → 写一条 `recheck_all`
- `tasks.py`：`@shared_task(base=QueueOnce, once={"graceful": True, "unlock_before_run": True}) def reconcile()` 调用 `refresh_all()` 和 `prune()`（`unlock_before_run`：运行中再次排队不会被静默丢弃，名片格式在对账途中被修改时会再跑一次）。README 给出 IT 需要加的 `CELERYBEAT_SCHEDULE` 条目（每天一次）。
- `checks.py`（`@register(deploy=False)`）：
  - `qqbot.E001`：`APPS_WITH_PUBLIC_VIEWS` 缺少 `"qqbot"`（提示：追加，不要覆盖）
  - `qqbot.E002`：`QQBOT_API_KEYS` 为空或不是 dict
  - `qqbot.E003`：有密钥短于 32 个字符
  - `qqbot.E004`：有 key id 不符合 `signing.KEY_ID_RE`（有空格、非 ASCII、超过 64 字符），用它的请求永远是 `missing_headers`
  - `qqbot.W001`：缓存后端不是 Redis 一类的共享缓存（nonce 防重放需要跨进程共享）
  - `qqbot.W002`：`CELERYBEAT_SCHEDULE` 里没有 `task == "qqbot.tasks.reconcile"` 的条目（条目名不限；在后台手动添加定时任务的站点可以忽略这条警告）
  - health 接口复用同一套检查逻辑（纯函数 `problems() -> list[str]`）
- `admin.py`：注册 `QQGroup`、`Binding`（只读，改动走前台）、`AuditLog`（只读）、`Config`。
- `management/commands/qqbot_reconcile.py`：手动执行对账。
- `README.md`（中文）：功能、给 IT 的安装步骤（`pip install`；`local.py` 中 `INSTALLED_APPS += ["qqbot"]`、`APPS_WITH_PUBLIC_VIEWS += ["qqbot"]`、`QQBOT_API_KEYS`、`CELERYBEAT_SCHEDULE`；`migrate`；重启）、权限怎么分配、如何生成密钥（`python -c "import secrets; print(secrets.token_urlsafe(48))"`）、升级与卸载（卸载时要删掉数据库里的 `qqbot_reconcile` 定时任务，AA 5 的 beat 把它存在 django_celery_beat 表里）。

## 8. 安全底线（任何部分都必须遵守）

1. 除 `qqbot.api.views` 里的 5 个视图外，所有视图都有显式的登录和权限装饰器。有测试遍历 URL 表逐个验证：匿名访问得到 302 到登录页；有登录但没权限得到 403。
2. API 视图匿名、无签名请求得到 **401 JSON**，不是 302。
3. 模板里不对外部数据使用 `|safe`（昵称、名片、角色名、群名、说明、入群须知都是外部数据）。
4. 修改操作只接受 POST 并校验 CSRF（API 除外）。
5. 不向成员（服务页卡片）泄露别人的 QQ 或角色信息。
6. API 响应中不包含 AA 用户 id、角色 id 等内部标识。
