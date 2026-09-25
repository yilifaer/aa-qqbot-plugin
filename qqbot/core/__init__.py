"""Domain logic for aa-qqbot (docs/SPEC.md section 3).

Views and the bot API must change data only through these modules.
Submodules are imported explicitly (``from qqbot.core import bindings``);
nothing is re-exported here to keep import order free of cycles.

aa-qqbot 的业务逻辑（见 docs/SPEC.md 第 3 节）。

视图和机器人 API 只能通过这些模块修改数据。
子模块需要显式导入（``from qqbot.core import bindings``）；
这里不再导出任何内容，以免导入时出现循环依赖。
"""
