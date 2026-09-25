"""HMAC-signed JSON API for the QQ bot (docs/SPEC.md section 4, API.md).

* :mod:`qqbot.api.signing` -- string-to-sign, signature and header checks.
* :mod:`qqbot.api.views` -- the five endpoints and the auth decorator.
* :mod:`qqbot.api.urls` -- routes, included by ``qqbot.urls`` as a nested
  include so AA's public-view exclusion stays confined to this list.

给 QQ 机器人用的 JSON 接口，请求用 HMAC 签名（见 docs/SPEC.md 第 4 节和 API.md）。

* ``qqbot.api.signing`` -- 待签名字符串、签名计算和请求头检查。
* ``qqbot.api.views`` -- 五个接口，以及负责认证的装饰器。
* ``qqbot.api.urls`` -- 路由。``qqbot.urls`` 以嵌套 include 的方式引入它，
  这样 AA 对公开页面的排除只作用于这份列表。
"""
