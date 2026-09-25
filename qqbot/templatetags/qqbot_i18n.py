"""``{% qqbot_language as var %}``: qqbot's UI language (see ``qqbot.i18n``).

``{% qqbot_language as var %}``：qqbot 的界面语言（见 ``qqbot.i18n``）。
"""

from django import template

from ..i18n import ui_language

register = template.Library()


@register.simple_tag
def qqbot_language():
    return ui_language()
