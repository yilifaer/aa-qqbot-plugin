"""The language of qqbot's UI: Chinese for Chinese users, English for everyone else.

AA (Django's ``LocaleMiddleware``) picks each request's language: the language
chosen in AA's menu, else the browser's language, else ``LANGUAGE_CODE``.
qqbot ships only a Simplified Chinese catalog. For any other language Django
would fall back to the site's ``LANGUAGE_CODE`` catalog and translate a few
generic words ("Save", "Status") from AA's or Django's catalogs, so a German
user would get a mix of English or Chinese and German. So qqbot's own output
(the services card, the sidebar entry, the QQ Admin pages and their messages)
is shown in Simplified Chinese when the request's language is any Chinese
(``zh-hans``, ``zh-hant``, ``zh-cn`` ...), and in plain English otherwise.

On the QQ Admin pages only qqbot's part is switched: the views run in
:func:`ui_language` (so messages and form errors are in it), and
``qqbot/base.html`` renders its blocks inside ``{% language %}``; AA's own
menus around them stay in the user's language.

qqbot 界面的语言：中文用户看中文，其他所有人看英文。

每个请求的语言由 AA（Django 的 ``LocaleMiddleware``）决定：先看 AA 语言菜单
里选的语言，再看浏览器的语言，最后用 ``LANGUAGE_CODE``。qqbot 只带了简体
中文的翻译。遇到其他语言时，Django 会退回到站点 ``LANGUAGE_CODE`` 的翻译，
还会从 AA 或 Django 的翻译里翻出几个通用词（"Save"、"Status"），德语用户
就会看到英文或中文夹着德文。所以 qqbot 自己的输出（服务卡片、侧边栏入口、
「QQ 管理」页面和它们的提示信息）在请求语言是任何一种中文（``zh-hans``、
``zh-hant``、``zh-cn`` 等）时显示简体中文，其他情况一律显示纯英文。

「QQ 管理」页面只切换 qqbot 自己的部分：视图在 :func:`ui_language` 下运行
（所以提示信息和表单错误用这个语言），``qqbot/base.html`` 把它的块放在
``{% language %}`` 里渲染；外面 AA 自己的菜单仍然是用户的语言。
"""

from functools import wraps

from django.utils import translation

CHINESE = "zh-hans"
ENGLISH = "en"


def is_chinese(language: str | None) -> bool:
    code = (language or "").lower().replace("_", "-")
    return code == "zh" or code.startswith("zh-")


def ui_language(language: str | None = None) -> str:
    """qqbot's UI language for ``language`` (default: the active language).

    ``zh-hans`` for any Chinese (``zh-hans``, ``zh-hant``, ``zh-cn`` ...),
    else ``en``. Applying it twice gives the same result.

    ``language``（默认为当前语言）对应的 qqbot 界面语言：任何中文（``zh-hans``、
    ``zh-hant``、``zh-cn`` 等）都返回 ``zh-hans``，其他一律返回 ``en``。
    """
    if language is None:
        language = translation.get_language()
    return CHINESE if is_chinese(language) else ENGLISH


def override():
    """Context manager: switch to :func:`ui_language` of the active language.

    上下文管理器：切换到当前语言对应的 :func:`ui_language`。
    """
    return translation.override(ui_language())


def ui_language_view(view):
    """Decorator: run the view in :func:`ui_language` of the request's language.

    Strings the view builds (messages, form errors, texts stored for the
    card) are then in qqbot's UI language. A ``TemplateResponse`` is rendered
    later, in the request's language; ``qqbot/base.html`` switches its own
    blocks.

    装饰器：视图在请求语言对应的 :func:`ui_language` 下运行。

    这样视图里生成的文字（提示信息、表单错误、存给卡片显示的文字）都是
    qqbot 的界面语言。``TemplateResponse`` 会在之后按请求的语言渲染，
    ``qqbot/base.html`` 自己切换它的块。
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        with override():
            return view(request, *args, **kwargs)

    return wrapper
