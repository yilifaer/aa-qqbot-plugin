"""HMAC-signed JSON API for the QQ bot (docs/SPEC.md section 4, API.md).

* :mod:`qqbot.api.signing` -- string-to-sign, signature and header checks.
* :mod:`qqbot.api.views` -- the five endpoints and the auth decorator.
* :mod:`qqbot.api.urls` -- routes, included by ``qqbot.urls`` as a nested
  include so AA's public-view exclusion stays confined to this list.
"""
