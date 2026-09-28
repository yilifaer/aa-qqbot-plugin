"""What needs a QQ manager to act (DECISIONS #24).

The number badge on the "QQ Admin" menu entry and on the card's "QQ Admin"
button counts only conflicts and misconfigured role groups. Unbound
members, the trusted review list and pending codes are not counted.

需要 QQ 管理员动手处理的事（决定 #24）。

侧边栏「QQ 管理」和卡片上「QQ 管理」按钮的数字角标只数冲突和配置错误的
身份组小群。未绑定的群成员、免验证复核列表、待验证的验证码都不算。
"""

from ..models import QQGroup
from . import bindings


def misconfigured_groups():
    """Active role groups without any required AA group: everyone in them is
    ``review`` / ``GROUP_MISCONFIGURED`` (e.g. the AA group was deleted).

    启用中、却一个 AA 组都没选的身份组小群：群里所有人都判为
    ``review`` / ``GROUP_MISCONFIGURED``（例如要求的 AA 组被删了）。
    """
    return QQGroup.objects.filter(
        kind=QQGroup.Kind.ROLE, is_active=True, required_groups__isnull=True
    )


def attention_counts() -> dict:
    """Conflicting QQs and misconfigured role groups; two queries, however
    many bindings there are.

    冲突的 QQ 数和配置错误的身份组小群数；固定两次查询，不随绑定数增长。
    """
    conflicts = len(bindings.conflict_qqs())
    misconfigured = misconfigured_groups().count()
    return {
        "conflicts": conflicts,
        "misconfigured": misconfigured,
        "total": conflicts + misconfigured,
    }
