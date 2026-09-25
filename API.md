# aa-qqbot 机器人接口（API v1）

> 给 **Koishi 机器人插件**（另一个仓库）的作者看的完整接口契约。
> AA 端的实现在本仓库 `qqbot/api/`；实现规格见 `docs/SPEC.md` 第 4 节。
> 本文件和实现不一致时，以本仓库的测试为准，并请提 issue。

---

## 1. 概述

- 通信**只有一个方向**：机器人主动调用 AA。AA 不会连接机器人。
- 一共 5 个接口，全部是 `POST`，请求体和响应体都是 JSON（UTF-8）：

| 接口 | 路径 | 什么时候调用 |
|---|---|---|
| `health` | `/qqbot/api/v1/health/` | 启动时、状态命令 |
| `groups` | `/qqbot/api/v1/groups/` | 启动时、定期（例如每小时），收到 `groups` 事件时 |
| `check` | `/qqbot/api/v1/check/` | 定时巡检（带完整名单）、新人入群、处理事件 |
| `claim` | `/qqbot/api/v1/claim/` | **每一个**入群申请（不管验证信息里有没有验证码，见第 5.4 节） |
| `events` | `/qqbot/api/v1/events/` | 每 60 秒 |

- 完整地址 = AA 的网址 + 路径，例如 `https://auth.example.com/qqbot/api/v1/check/`。
  **路径末尾的 `/` 不能省**，否则会被重定向（见第 6 节）。
- 所有请求都要**签名**（第 2 节）。
- 响应里只有机器人做决定需要的信息，不包含 AA 内部的用户编号、角色编号等。

### 1.1 最重要的三条规则

1. **`review` 永不处置**：判定为 `review` 的 QQ，机器人只报告，绝不踢人、绝不拒绝。
2. **拿不到明确答案就当「无法判断」**：网络错误、超时、非 200 状态码、响应不是 JSON、JSON 里 `ok` 不是 `true`——
   一律当作「无法判断」（unknown），**不做任何处置**。
3. **不要跟随重定向**：`fetch` 要写 `redirect: 'manual'`。AA 配置有误时会把请求重定向到登录页（302），
   跟随过去会拿到一个网页，绝不能把它当作成功。

---

## 2. 认证（签名）

### 2.1 请求头

| 请求头 | 内容 | 格式 |
|---|---|---|
| `Content-Type` | `application/json` | |
| `X-QQBot-Key` | 密钥编号（AA 的 `QQBOT_API_KEYS` 里的键） | 可打印 ASCII，不含空格，1–64 个字符 |
| `X-QQBot-Timestamp` | 当前 Unix 时间，**单位秒**，整数 | 只含数字（最多 16 位），例如 `1767225600`。误传毫秒会得到 `stale_timestamp` |
| `X-QQBot-Nonce` | 随机数，每个请求都不同 | 16–64 个字符，只能是 `A-Z a-z 0-9 _ -` |
| `X-QQBot-Signature` | 签名 | 64 位**小写**十六进制 |

建议随机数用 `crypto.randomBytes(16).toString('hex')`（32 个字符）。

### 2.2 待签名字符串

五段用换行符 `\n`（单个 LF，不是 CRLF）连接，**末尾没有换行**：

```
POST
<路径>
<时间戳>
<随机数>
<请求体的 SHA-256，小写十六进制>
```

- 第 1 行固定为大写的 `POST`。
- 路径：实际请求的 URL 路径，例如 `/qqbot/api/v1/check/`，**不含**域名、端口和 `?` 后的查询串，末尾带 `/`。
  （如果 AA 部署在子路径下，例如 `https://example.com/auth/`，路径就要带上这个前缀：`/auth/qqbot/api/v1/check/`。
  最稳妥的做法是：先拼出完整网址，再取它的路径部分来签名，见第 2.6 节的参考实现。）
- 时间戳、随机数：与请求头里的**完全相同**的字符串。
- 请求体哈希：对**实际发送的原始字节**计算 SHA-256。空请求体的哈希是
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

签名：

```
signature = hex( HMAC-SHA256( key = 密钥（UTF-8 字节）, message = 待签名字符串（UTF-8 字节） ) )
```

> **常见错误**：先 `JSON.stringify` 一次用来签名，发送时 HTTP 库又自己序列化了一次（空格、键顺序、转义不同），
> 导致签名对不上。正确做法：序列化**一次**得到字符串，用这个字符串签名，再把**同一个字符串**作为请求体发送。

### 2.3 AA 的校验顺序

AA 按下面的顺序检查，第一个不通过的就返回对应错误（错误码见第 6 节）：

1. 请求方法不是 `POST` → `405 method_not_allowed`
2. AA 没有配置任何密钥 → `503 misconfigured`
3. 请求头缺失或格式不对 → `401 missing_headers`
4. 密钥编号不存在 → `401 unknown_key`
5. 时间戳与 AA 服务器时间相差超过 **300 秒**（前后都算，可由 IT 调整） → `401 stale_timestamp`
6. 签名不对 → `401 bad_signature`
7. 请求体超过上限（默认 256 KB） → `413 too_large`
8. 随机数用过（同一个密钥编号下，约 11 分钟内） → `401 replayed_nonce`
   （只有签名正确的请求才会记录随机数）
9. 超过每分钟请求上限（每个密钥编号默认 120 次/分钟） → `429 rate_limited`
10. 请求体不是 JSON 对象，或字段不合法 → `400 bad_request`

请求体为空时按 `{}` 处理（`health`、`groups` 可以不带请求体，但仍然要签名）。

### 2.4 换密钥（不停机）

AA 的 `QQBOT_API_KEYS` 可以同时配置多把密钥，例如 `{"koishi-2025": "...", "koishi-2026": "..."}`，每把都有效。
换密钥的步骤：IT 在 AA 上**加上**新密钥 → 机器人改用新密钥 → IT 删除旧密钥。

### 2.5 签名测试样例

实现签名后，请先用下面两个样例核对，结果必须**逐字相同**。本仓库的测试
（`qqbot/tests/test_api_signing.py`）保证这些数值与 AA 的实现一致。

公共参数：

| 项目 | 值 |
|---|---|
| 密钥 | `koishi-test-vector-secret-0123456789ABCDEFGHIJ` |
| 密钥编号 | `koishi-1` |
| 时间戳 | `1767225600` |

**样例 1**（纯 ASCII 请求体）

| 项目 | 值 |
|---|---|
| 路径 | `/qqbot/api/v1/check/` |
| 随机数 | `Zq3vN8xK2mP5tR7w` |
| 请求体（原样，没有空格和换行） | `{"group_id":"123456789","qqs":["10001","20002"],"full_roster":false}` |
| 请求体 SHA-256 | `e5eaf15236bbfb41e46f96a6413ab601de021f05391bb63bbd331da3057315cd` |
| 待签名字符串 | `POST\n/qqbot/api/v1/check/\n1767225600\nZq3vN8xK2mP5tR7w\ne5eaf15236bbfb41e46f96a6413ab601de021f05391bb63bbd331da3057315cd` |
| **签名** | `4babcb45e2477890050b37e838f2efc239b550fccae9c8b5cb7127ddf36767e4` |

**样例 2**（请求体含中文，检验 UTF-8 编码）

| 项目 | 值 |
|---|---|
| 路径 | `/qqbot/api/v1/claim/` |
| 随机数 | `Nonce_For-Vector-2` |
| 请求体（原样） | `{"qq":"10001","text":"我是凯拉 QQ-ABC234"}` |
| 请求体字节（十六进制，共 46 字节） | `7b227171223a223130303031222c2274657874223a22e68891e698afe587afe68b892051512d414243323334227d` |
| 请求体 SHA-256 | `0420addccba85a2add4a1260989df656a91b169a103a3ce56dc05e6c5e1e6e68` |
| **签名** | `8ac11ec05b15b17b7449676b2f2bb3c7395bd2578ac396389e4af3854af11be2` |

（`\n` 表示一个换行符。）

### 2.6 参考实现（Node.js）

```js
import { createHash, createHmac, randomBytes } from 'node:crypto'

export function sign(secret, path, timestamp, nonce, bodyText) {
  const bodyHash = createHash('sha256').update(bodyText, 'utf8').digest('hex')
  const message = ['POST', path, timestamp, nonce, bodyHash].join('\n')
  return createHmac('sha256', secret).update(message, 'utf8').digest('hex')
}

// 调用一个接口。返回 { state: 'ok', data } 或 { state: 'unknown', retry, error }。
// baseUrl 是 AA 的网址，末尾不带 '/'，例如 'https://auth.example.com'；
// AA 装在子路径下时把前缀也写上，例如 'https://example.com/auth'。
export async function callApi({ baseUrl, keyId, secret }, name, payload = {}) {
  const url = new URL(`${baseUrl}/qqbot/api/v1/${name}/`)
  const path = url.pathname                         // 签名用实际请求的路径（含子路径前缀）
  const bodyText = JSON.stringify(payload)          // 只序列化一次
  const timestamp = Math.floor(Date.now() / 1000).toString()   // 秒，不是毫秒
  const nonce = randomBytes(16).toString('hex')
  let res
  try {
    res = await fetch(url, {
      method: 'POST',
      redirect: 'manual',                           // 绝不跟随重定向
      headers: {
        'Content-Type': 'application/json',
        'X-QQBot-Key': keyId,
        'X-QQBot-Timestamp': timestamp,
        'X-QQBot-Nonce': nonce,
        'X-QQBot-Signature': sign(secret, path, timestamp, nonce, bodyText),
      },
      body: bodyText,                               // 发送与签名相同的字符串
      signal: AbortSignal.timeout(30_000),
    })
  } catch (e) {
    return { state: 'unknown', retry: true, error: 'network' }       // 网络错误 / 超时
  }
  let data = null
  try { data = await res.json() } catch { /* 不是 JSON */ }
  if (res.status === 200 && data && data.ok === true) return { state: 'ok', data }
  return {
    state: 'unknown',
    retry: res.status >= 500,                       // 只有 5xx 值得自动重试
    status: res.status,
    error: data?.error ?? `http_${res.status}`,
    message: data?.message,
  }
}
```

> 如果改用 Koishi 的 `ctx.http`，请确认：不跟随重定向；请求体按原样发送（传入已经序列化好的字符串，
> 不要让它再序列化一次）；非 2xx 时能拿到状态码和响应体。

---

## 3. 响应格式与三态处理

成功（HTTP 200）：

```json
{"ok": true, "server_time": "2026-09-25T08:00:00.123456+00:00", "...": "各接口自己的字段"}
```

失败（HTTP 4xx/5xx）：

```json
{"ok": false, "error": "stale_timestamp", "message": "请求的时间戳与 AA 服务器时间相差太大……"}
```

`message` 是给运维看的中文说明，告诉他该怎么修，可以原样写进机器人日志或报告给管理员。
`server_time` 是 AA 服务器的当前时间，可以用来发现机器人电脑的时钟偏差。

机器人对每个 QQ 的处理结果只有三种状态：

| 状态 | 来源 | 机器人怎么做 |
|---|---|---|
| **allow** | HTTP 200 且 `decision = "allow"` | 批准申请 / 保留；按 `card` 同步群名片 |
| **deny** | HTTP 200 且 `decision = "deny"` | 按机器人那边的模式：只报告；或（打开了「真踢人」开关的群）拒绝申请 / 移出 |
| **不处置** | `decision = "review"`；**或**任何拿不到明确答案的情况：网络错误、超时、非 200、非 JSON、`ok` 不是 `true`、结果里找不到这个 QQ | 什么都不做，只记录 / 报告；入群申请保持待处理，等人工 |

**重试策略：**

- 只对 **网络错误、超时、HTTP 5xx** 自动重试，用递增间隔（例如 5 秒、30 秒、2 分钟），重试几次后放弃，等下一轮。
- **每次重试都要重新生成时间戳和随机数、重新签名**（同一个随机数第二次会被拒绝）。
- `429`：按响应头 `Retry-After`（秒）等待后再继续，不要立刻重试。
- 其他 4xx（`401`、`400`、`404`、`413`）说明配置或代码有问题，重试没有用：记录 `message` 并报告给管理员。
- `claim` 不是幂等的：第一次其实已经成功但网络断了，重试会得到 `code_used`。这没关系——
  带上 `group_id` 时，按 `result.decision` 处理即可（第 5.4 节），不要只看 `claimed`。

---

## 4. 判定（`decision`）与原因（`reason`）

| decision | 含义 | 机器人怎么做 |
|---|---|---|
| `allow` | 有资格 | 批准 / 保留；同步群名片 |
| `deny` | 明确没有资格 | 只报告，或（开了踢人开关的群）拒绝 / 移出 |
| `review` | 需要人工处理 | **永不处置**，只报告给 QQ 管理员 |

| reason | decision | 含义 | 建议给管理员 / 申请人的说明 |
|---|---|---|---|
| `OK` | allow | 有资格 | — |
| `NOT_BOUND` | deny | 这个 QQ 没有绑定任何 AA 账号 | 请先登录 AA，在「QQ 绑定」页面绑定 QQ |
| `PENDING_VERIFY` | deny | 已在 AA 提交，但还没用验证码验证 | 请把 AA 上显示的验证码填进入群申请的「验证信息」 |
| `USER_INACTIVE` | deny | 绑定的 AA 账号已停用 | 请联系 QQ 管理员 |
| `NO_MAIN` | deny | AA 账号没有设置主角色 | 请在 AA 设置主角色 |
| `NO_ACCESS` | deny | AA 账号没有成员资格（例如不是联盟成员） | 请联系 QQ 管理员 |
| `GROUP_ROLE_MISSING` | deny | 身份组小群：不在要求的 AA 组里 | 请联系 QQ 管理员 |
| `CONFLICT` | review | 同一个 QQ 被两个 AA 账号认领（都是「老成员免验证」） | 只报告；QQ 管理员在 AA「待处理」里处理 |
| `GROUP_MISCONFIGURED` | review | 身份组小群没有配置任何 AA 组 | 只报告；QQ 管理员在 AA 里修正这个群的设置 |
| `BAD_QQ` | review | 请求里的这一项不是合法 QQ 号（5–11 位数字，不以 0 开头） | 只报告；检查机器人传的数据 |

以后可能增加新的 `reason`。遇到不认识的 `reason` 时，按它的 `decision` 处理；
遇到不认识的 `decision`，一律当作「不处置」。

`card` 只在 `allow` 时有值，其余情况为 `null`。

---

## 5. 接口

以下示例只列出请求体和成功响应；请求头见第 2 节。

### 5.1 `health`

检查 AA 端配置。启动时调用一次，状态命令里也可以调用。

请求：`{}`（或空请求体）

响应：

```json
{
  "ok": true,
  "server_time": "2026-09-25T08:00:00.123456+00:00",
  "version": "1.0.0",
  "config_ok": true,
  "problems": []
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | string | AA 插件版本 |
| `config_ok` | bool | 没有错误级别（`E`）的问题时为 `true` |
| `problems` | string[] | AA 系统检查发现的问题代码，见下表 |

| 问题代码 | 含义 | 怎么修（告诉 IT） |
|---|---|---|
| `qqbot.E001` | `APPS_WITH_PUBLIC_VIEWS` 里没有 `"qqbot"` | 在 `local.py` **追加** `APPS_WITH_PUBLIC_VIEWS += ["qqbot"]`（不要整行覆盖） |
| `qqbot.E002` | `QQBOT_API_KEYS` 为空或格式不对 | 在 `local.py` 配置 `QQBOT_API_KEYS = {"编号": "密钥"}` |
| `qqbot.E003` | 有密钥短于 32 个字符 | 换一把更长的密钥 |
| `qqbot.E004` | 有密钥编号不符合第 2.1 节的格式（空格、中文、超过 64 个字符） | 用这个编号发的请求会一直得到 `missing_headers`；把编号改成 `koishi-1` 这样的英文名字，两边同步修改 |
| `qqbot.W001` | 缓存不是 Redis 一类的共享缓存 | 防重放需要跨进程共享的缓存；AA 默认就是 Redis，一般不会出现 |

### 5.2 `groups`

受管群列表（只含**启用中**的群）。机器人只管理这张表里的群，其他群一律不动。

请求：`{}`

响应：

```json
{
  "ok": true,
  "server_time": "2026-09-25T08:00:00.123456+00:00",
  "groups": [
    {"group_id": "123456789", "name": "联盟聊天群", "kind": "fixed"},
    {"group_id": "987654321", "name": "旗舰群", "kind": "role"}
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `group_id` | string | 群号 |
| `name` | string | AA 上填写的群名称（外部输入，显示时要转义） |
| `kind` | string | `fixed` 固定群；`role` 身份组小群。机器人对两者的处理方式相同 |

建议：启动时调用；之后每小时调用一次；收到 `groups` 事件时立即调用。
如果调用失败，**继续使用上一次成功拿到的列表**，不要当成「没有群」。

### 5.3 `check`

对「一个群 + 一组 QQ」逐个给出判定和应有的群名片。

请求：

```json
{"group_id": "123456789", "qqs": ["10001", "20002", "abc"], "full_roster": false}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `group_id` | string 或整数 | 是 | 群号，必须是 `groups` 里的群 |
| `qqs` | (string 或整数)[] | 是 | 要判定的 QQ，**最多 3000 个**；可以为空列表 |
| `full_roster` | bool | 否，默认 `false` | `true` 表示 `qqs` 是这个群**完整的**成员名单（见第 7 节） |

响应：

```json
{
  "ok": true,
  "server_time": "2026-09-25T08:00:00.123456+00:00",
  "group_id": "123456789",
  "results": [
    {"qq": "10001", "decision": "allow", "reason": "OK", "card": "[IGC] Kaela Voss - 凯拉"},
    {"qq": "20002", "decision": "deny", "reason": "NOT_BOUND", "card": null},
    {"qq": "abc", "decision": "review", "reason": "BAD_QQ", "card": null}
  ],
  "roster": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `group_id` | string | 群号（规范化后） |
| `results` | object[] | 与请求的 `qqs` **一一对应、顺序相同**（重复的 QQ 也会重复出现） |
| `results[].qq` | string / 整数 / null | 规范化后的 QQ（整数会变成字符串，全角数字会变成半角）；`BAD_QQ` 时原样返回请求里的值——但只限整数和 64 个字符以内的可打印字符串，其他值（例如含控制字符或无法编码的字符）返回 `null`，按位置对应即可 |
| `results[].decision` / `reason` / `card` | | 见第 4 节 |
| `roster` | object 或 null | 只有 `full_roster: true` 时有值：`{"added": 新增数, "removed": 移除数, "total": 名单总数}` |

错误：
- 群不存在或已停用 → `404 unknown_group`（请重新拉取 `groups`）。
- 字段不合法、超过 3000 个、`full_roster: true` 但名单里没有一个合法 QQ → `400 bad_request`。

### 5.4 `claim`

机器人收到**任何**入群申请时调用（验证信息里没有验证码也调用）。
AA 会自己从 `text` 里找出验证码，机器人把验证信息**原文**传过来即可，**不要**自己先判断有没有验证码：
AA 接受的写法比 `QQ-XXXXXX` 宽得多——不区分大小写、`-` 可以省略（`qq7k3f9p`）、全角字母数字和各种破折号
（`ＱＱ－７Ｋ３Ｆ９Ｐ`、`QQ—7K3F9P`）都算，前后可以有别的文字。机器人自己用正则判断很容易漏掉，
而漏掉的验证码不会被使用，申请人会被判成 `PENDING_VERIFY`。没有验证码时 `claim` 返回 `no_code`，
`result` 与 `check` 的结果完全相同，所以统一走 `claim` 不会有任何损失。

（仅供参考，机器人不需要实现：AA 先把全角字符转成半角、把破折号类字符转成 `-`，再用
`QQ-?[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}`（不区分大小写）查找。）

请求：

```json
{"qq": "10001", "text": "我是凯拉，验证码 QQ-ABC234", "group_id": "123456789"}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `qq` | string 或整数 | 是 | 申请人的 QQ（**必须**来自 QQ 平台的事件数据，不能来自申请人填写的文字） |
| `text` | string | 是 | 入群申请的验证信息原文，最长 2000 个字符；可以是空字符串 |
| `group_id` | string 或整数 | 否 | 申请加入的群。带上时响应里会给出这个 QQ 在该群的判定 |

响应：

```json
{
  "ok": true,
  "server_time": "2026-09-25T08:00:00.123456+00:00",
  "claimed": true,
  "outcome": "claimed",
  "message": "验证成功。",
  "result": {"qq": "10001", "decision": "allow", "reason": "OK", "card": "[IGC] Kaela Voss - 凯拉"}
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `claimed` | bool | 这次调用是否用验证码完成了验证 |
| `outcome` | string | 见下表 |
| `message` | string | 中文说明，可以转告申请人（要转义） |
| `result` | object 或 null | 带了 `group_id` 时，为**验证之后**该 QQ 在该群的判定（字段同 `check` 的一项）；否则为 `null` |

| outcome | 含义 | 机器人怎么做 |
|---|---|---|
| `claimed` | 验证成功，QQ 已绑定 | 按 `result.decision` 处理（通常是批准） |
| `no_code` | 验证信息里没有验证码 | 按 `result.decision` 处理（与 `check` 的结果相同） |
| `code_invalid` | 验证码不存在（打错了） | 按 `result.decision` 处理；可以提示申请人核对验证码 |
| `code_expired` | 验证码已过期 | 按 `result.decision` 处理；提示申请人在 AA 上重新生成 |
| `code_used` | 验证码已用过或已作废 | 按 `result.decision` 处理（可能是网络重试，其实已经成功了） |
| `qq_mismatch` | 验证码是给另一个 QQ 的；该验证码已被作废 | 按 `result.decision` 处理；提示申请人在 AA 上重新生成，并确认填写的 QQ 正确 |

**入群申请统一按 `result.decision` 处理，不看 `outcome`**（所以调用 `claim` 时请总是带上 `group_id`）：
`allow` → 批准；`deny` → 按模式拒绝或保持待处理；`review` 或拿不到结果 → 保持待处理、报告管理员。
`outcome` 和 `message` 只用来决定给申请人看什么提示。例如 `qq_mismatch` 时，如果申请人自己的 QQ
本来就已验证、有资格，`result.decision` 仍然是 `allow`，应当批准。

错误：`group_id` 对应的群不存在或已停用 → `404 unknown_group`（此时验证码**不会**被使用）；
`qq` 不合法、`text` 不是字符串或太长 → `400 bad_request`。

### 5.5 `events`

拉取 AA 这边发生的变化（发件箱）。游标是事件的 `id`，只增不减。

请求：

```json
{"after": 1041, "limit": 200}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `after` | 整数 ≥ 0 | 是 | 上次响应里的 `last_id`；第一次用 `0` |
| `limit` | 整数 1–500 | 否，默认 200 | 本次最多返回多少条 |

响应：

```json
{
  "ok": true,
  "server_time": "2026-09-25T08:00:00.123456+00:00",
  "events": [
    {"id": 1042, "kind": "recheck", "qq": "10001", "created_at": "2026-09-25T07:59:40.000000+00:00"},
    {"id": 1043, "kind": "card", "qq": "10001", "created_at": "2026-09-25T07:59:40.000000+00:00"},
    {"id": 1044, "kind": "recheck_all", "qq": "", "created_at": "2026-09-25T07:59:45.000000+00:00"}
  ],
  "last_id": 1044,
  "has_more": false
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `events[].id` | 整数 | 事件编号（递增） |
| `events[].kind` | string | 见下表 |
| `events[].qq` | string | 相关的 QQ；`recheck_all`、`groups` 时为空字符串 |
| `events[].created_at` | string | 事件时间（ISO 8601） |
| `last_id` | 整数 | 下次请求的 `after`；没有新事件时等于这次的 `after` |
| `has_more` | bool | `true` 表示还有已经可以拿的事件，应**立即**再拉一页 |

| kind | 含义 | 机器人怎么做 |
|---|---|---|
| `recheck` | 这个 QQ 的资格可能变了（停用、降级、退组、解绑、换绑后的旧号……） | 对它所在的每个受管群调用 `check`（不带 `full_roster`），按结果处理 |
| `card` | 这个 QQ 的群名片变了 | 对它所在的受管群调用 `check`，按 `card` 同步名片 |
| `recheck_all` | 大范围变化（例如权限配置改了） | 尽快对所有受管群做一次完整巡检（第 7 节） |
| `groups` | 受管群列表变了 | 重新调用 `groups`（后面通常紧跟一条 `recheck_all`） |

不认识的 `kind` 直接跳过。同一个 QQ 可能连续出现多条事件，处理是幂等的，可以合并。

**批量处理，不要一条事件发一次 `check`**：AA 上一个操作可能一次写出上千条事件（例如 QQ 管理员改了群名片格式，
每个绑定都会有一条 `card`；改了某个状态的权限，这个状态里的每个人都会有一条 `recheck`）。
一页事件（或连续几页）拉完后，先把里面的 QQ 去重，再**按群合并**：对每个受管群只发**一次** `check`，
`qqs` 里放这个群里所有相关的 QQ（最多 3000 个，更多就分几次）。一页里出现 `recheck_all` 时，
直接做一次完整巡检，这一页的其他事件就不用单独处理了。逐条调用很快就会超过每分钟的请求上限（第 10 节）。

---

## 6. 错误码

| HTTP | `error` | 含义 | 机器人怎么做 / 怎么修 |
|---|---|---|---|
| 400 | `bad_request` | JSON 不合法（包括嵌套过深）或字段不对，`message` 会说明哪个字段 | 修代码；不要重试 |
| 401 | `missing_headers` | 缺少签名请求头或格式不对 | 检查四个 `X-QQBot-*` 请求头（第 2.1 节）；密钥编号不能有空格或中文（AA 的 `health` 会报 `qqbot.E004`） |
| 401 | `unknown_key` | 密钥编号不存在 | 机器人的密钥编号与 AA 的 `QQBOT_API_KEYS` 不一致；换密钥时两边都要改 |
| 401 | `stale_timestamp` | 时间戳偏差超过 300 秒 | 校准机器人电脑的时间（开启 NTP 自动对时）；确认时间戳单位是**秒**不是毫秒 |
| 401 | `bad_signature` | 签名不对 | 用第 2.5 节的测试样例核对算法；检查密钥、路径（末尾 `/`）、请求体是否与签名时完全一致 |
| 401 | `replayed_nonce` | 随机数已经用过 | 每个请求（包括重试）都要生成新的随机数并重新签名 |
| 404 | `unknown_group` | 群不存在或已停用 | 重新拉取 `groups`；不要处置这个群里的任何人 |
| 405 | `method_not_allowed` | 不是 POST | 修代码 |
| 413 | `too_large` | 请求体太大 | 减少一次发送的内容（`check` 最多 3000 个 QQ） |
| 429 | `rate_limited` | 请求太频繁（每个密钥默认 120 次/分钟） | 按 `Retry-After` 等待；检查是不是逐条事件调用了 `check`（第 5.5 节）或有死循环 |
| 500 | `internal_error` | AA 内部出错（已记日志） | 当作「无法判断」，稍后重试；多次出现请联系 IT 查看 AA 日志 |
| 503 | `misconfigured` | AA 没有配置机器人密钥 | 当作「无法判断」，**绝不踢人**；联系 IT 配置 `QQBOT_API_KEYS` |

下面这些**不是** AA 插件返回的 JSON，而是部署或网关的问题。统统当作「无法判断」：

| 看到的情况 | 最可能的原因 | 怎么修 |
|---|---|---|
| **302** 重定向到 `/account/login/` 之类 | AA 的 `local.py` 里**缺少** `APPS_WITH_PUBLIC_VIEWS += ["qqbot"]`，AA 把机器人当成未登录用户 | 联系 IT 追加这一行后重启 AA |
| 301 / 308 重定向 | 路径末尾少了 `/`；或者用了 `http://`，服务器要求 `https://` | 用 `https://`，路径以 `/` 结尾 |
| 404 网页 | 路径写错、AA 上没装 / 没启用插件，或插件版本太旧 | 检查网址；让 IT 确认插件已安装 |
| 403 网页 | 网关、防火墙或 Cloudflare 拦截 | 让 IT 放行机器人的请求 |
| 413 网页 | nginx 的 `client_max_body_size` 太小 | 让 IT 调大（至少 1 MB） |
| 502 / 504 网页 | AA 正在重启或过载 | 稍后重试 |
| 响应不是 JSON | 以上任何一种 | 记录状态码，报告管理员 |

---

## 7. 巡检与 `full_roster`

**巡检**是主渠道：机器人定期把每个受管群的**完整成员名单**一次性交给 AA：

```json
{"group_id": "123456789", "qqs": ["10001", "10002", "..."], "full_roster": true}
```

`full_roster: true` 的含义：

- `qqs` 是这个群**此刻全部**成员（包括群主、管理员、机器人自己），AA 用它**整体替换**该群的名单；
  不在列表里的 QQ 会从名单中删除。只传了一部分成员时**不要**设 `full_roster: true`。
- AA 用这份名单实现「**老成员免验证**」：成员在 AA 上填写 QQ 时，如果这个 QQ 出现在某个受管群最近一次完整名单里，
  就直接生效，不需要验证码。
- 名单有有效期（默认 **7 天**，QQ 管理员可以在 AA 设置里改）。超过这个时间没有上报完整名单的群，
  就不能再用于免验证，成员只能走验证码流程。所以**每个群至少每隔几天要成功巡检一次**。
- 名单里的不合法号码会被忽略（响应里对应项为 `BAD_QQ`）。
- 名单为空（或没有一个合法 QQ）时 AA 返回 `400`，防止误把名单清空。

一个群最多 3000 人，一次请求就能发完。巡检之外的 `check` 调用（入群申请、处理事件）**不要**带 `full_roster: true`。

巡检拿到结果后：`allow` 的同步名片；`deny` 的按模式报告或处置；`review` 的只报告。
**建议机器人永不处置：机器人自己、群主、群管理员**（在机器人那边加白名单）。

### 7.1 防止大规模误踢（熔断，必须实现）

AA 这边的一次误操作，就可能让一个群里**所有人**同时变成 `deny`，例如：管理员把一个大群改成了身份组小群，
或者选错了要求的 AA 组（全员 `GROUP_ROLE_MISSING`）；有人在后台把 `basic_access` 从 Member 状态上拿掉了
（全员 `NO_ACCESS`）；管理员账号被盗。AA 随后会发 `recheck_all`，机器人马上巡检，拿到的就是一片 `deny`。
「真踢人」开关只能拦住开关**没打开**的群，所以打开了开关的群必须由机器人自己熔断：

- 一次巡检（或一批事件处理）里，某个群**将要移出的人数**超过阈值时——建议「超过 5 人**或**超过该群人数的 10%」，
  以较小者为准——**这一轮对这个群一个人都不移出**，改为只报告，并通知 QQ 管理员人工确认
  （例如在管理群里发消息，由管理员用命令确认后再执行）。
- 在熔断状态下，入群申请的 `deny` 也不要自动拒绝，保持待处理。
- 另外对踢人设一个总速率上限（例如每个群每小时最多移出 10 人），超过的留到下一轮。
- 阈值做成机器人的配置项，默认值宁小勿大。

这样即使 AA 被误配置，最坏的结果也只是「报告了一大串 `deny`」，不会真的把人踢光。

---

## 8. 事件轮询

- 每 **60 秒**调用一次 `events`；`has_more` 为 `true` 时立即再拉下一页，直到为 `false`。
- **事件最多延迟约 10 秒才可见**：AA 只返回创建超过 10 秒的事件，并且遇到第一条还不够 10 秒的就停下，
  这样游标不会越过还没提交完成的事件。所以 AA 上刚发生的变化，机器人在下一次（或再下一次）轮询时才能看到，属于正常现象。
- **游标要持久化**：处理完一页事件后，把 `last_id` 保存到磁盘或数据库（例如 Koishi 的数据库），
  重启后从保存的值继续。先处理、后保存：宁可重复处理（处理是幂等的），也不要漏掉。
- 游标丢失时：从 `after: 0` 开始把所有页拉完（只取 `last_id`，不处理），然后做一次完整巡检，再按正常流程继续。
  AA 保留 30 天内的事件。
- 事件只是「快速通道」，AA 每天还会做一次对账，补上可能漏掉的变化；再加上每 6 小时一次的巡检，偶尔漏掉一条事件也会被纠正。

---

## 9. 群名片同步

- `card` 是 AA 算好的完整群名片，例如 `[IGC] Kaela Voss - 凯拉`。只在 `decision = "allow"` 时有值；
  `null` 时**不要改**这个人的名片。
- AA 已经把名片截断到 **UTF-8 不超过 60 字节**，而且不会切断多字节字符。机器人不要再改动内容；
  只有当前名片与 `card` 不同时才修改，避免频繁调用 QQ 接口。
- 如果 QQ 拒绝了这个名片（例如实际上限比 60 字节短），只记录并报告，**不要**反复重试。实测到的上限请告诉 AA 维护者。
- 名片、昵称、角色名、群名称、`message` 都是**外部输入**：写进 Koishi 消息时必须转义
  （例如用 `h.escape()` 或 `h.text()`），不要直接拼进消息元素，防止被当成 `<at>`、`<img>` 等元素解析。
  调用改名片的接口时按纯文本传递。

---

## 10. 建议频率

| 调用 | 频率 | 说明 |
|---|---|---|
| 巡检（`check` + `full_roster: true`） | 每个群每 **6 小时**一次；收到 `recheck_all` 时尽快一次 | 多个群错开时间，不要同时发 |
| `events` | 每 **60 秒**一次 | `has_more` 时连续拉取 |
| `groups` | 启动时、每小时、收到 `groups` 事件时 | |
| `health` | 启动时、状态命令 | |
| `claim` / 单个 `check` | 有入群申请或新人入群时 | |

请求超时建议 30 秒（大群巡检的 `check` 最慢）。默认限速是每个密钥每分钟 120 次（IT 可以用 AA 的
`QQBOT_API_RATE_LIMIT` 设置调整）。按上面的频率、并且**按群批量处理事件**（第 5.5 节）时远远用不到；
逐条事件调用 `check` 则会在 AA 批量操作后很快超限。

---

## 11. Koishi 端实现清单

- [ ] 签名与第 2.5 节两个测试样例的结果完全一致（写成单元测试）。
- [ ] 请求体只序列化一次，签名和发送用同一个字符串；`Content-Type: application/json`。
- [ ] `fetch` 使用 `redirect: 'manual'`；只接受 HTTP 200 且 `ok === true` 的响应。
- [ ] 网络错误、超时、非 200、非 JSON、找不到结果 → 「无法判断」，不做任何处置。
- [ ] `review` 永不处置；不认识的 `decision` 也不处置。
- [ ] 只对网络错误、超时、5xx 自动重试；每次重试重新生成时间戳、随机数和签名；`429` 按 `Retry-After` 等待。
- [ ] 「真踢人」开关只在机器人这边，默认关闭（只报告）。
- [ ] 只管理 `groups` 返回的群；拉取失败时沿用上一次的列表。
- [ ] 巡检每 6 小时一次，发送完整名单并设 `full_roster: true`。
- [ ] 入群申请：**一律**调用 `claim`（带 `group_id`，验证信息原文照传），只按 `result.decision` 处理。
- [ ] `events` 每 60 秒轮询，游标持久化，先处理后保存；一页事件按群合并成一次 `check`，不要逐条调用。
- [ ] 熔断（第 7.1 节）：一轮要移出的人数超过阈值时，这个群这一轮一个都不移出，只报告并等管理员确认；踢人有速率上限。
- [ ] AA 装在子路径下时，签名用的路径带上前缀（按第 2.6 节从完整网址取路径）。
- [ ] 名片只在 `allow` 且与当前不同时修改；所有外部文字写进消息前转义。
- [ ] 白名单：机器人自己、群主、群管理员永不处置。
- [ ] 机器人电脑开启 NTP 自动对时。
