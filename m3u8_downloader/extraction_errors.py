"""提取异常与明确的登录要求；独立 worker 也可使用。"""

import re


class ExtractError(RuntimeError):
    """网页提取异常基类。"""


class PageFetchError(ExtractError):
    """网页拉取失败。"""


class DeepModeUnavailableError(ExtractError):
    """深度模式运行环境不可用。"""


class NoCandidateFoundError(ExtractError):
    """没有发现可下载候选。"""


LOGIN_REQUIRED_PREFIX = "需要登录："
LOGIN_REQUIRED_MESSAGE = LOGIN_REQUIRED_PREFIX + "网站明确要求登录，请填写或更新登录信息后重新提取。"


class LoginRequiredError(NoCandidateFoundError):
    def __init__(self):
        super().__init__(LOGIN_REQUIRED_MESSAGE)


_EXPLICIT_LOGIN = re.compile(
    r"请先登录|登录后(?:才可|才能|即可)?(?:观看|播放|访问)|"
    r"登录(?:状态)?(?:已)?(?:失效|过期)|"
    r"please\s+(?:log\s*in|sign\s*in)\s+(?:first|to\s+(?:watch|play|continue))|"
    r"(?:log\s*in|sign\s*in)\s+to\s+(?:watch|play)|"
    r"authentication\s+required|login\s+required", re.I,
)
_LOGIN_TITLE = re.compile(
    r"^(?:请先登录|需要登录|登录后观看|login required|authentication required|sign in to watch)"
    r"(?:\s*[-|｜].*)?$", re.I,
)


LOGIN_EVIDENCE_SCRIPT = """() => Array.from(document.querySelectorAll(
    '[role="alert"], [role="dialog"], .player, #player, [class*="player"]'
)).filter(node => node.getClientRects().length > 0)
  .map(node => (node.innerText || '').slice(0, 1000)).join('\\n').slice(0, 4000)"""


class LoginGate:
    """只保留明确登录提示的布尔结果，不保存网页错误或账号内容。"""

    def __init__(self):
        self.required = False

    def observe(self, message):
        self.required = self.required or bool(_EXPLICIT_LOGIN.search(str(message)[:4000]))

    def observe_title(self, title):
        self.required = self.required or bool(_LOGIN_TITLE.fullmatch((title or "").strip()))
