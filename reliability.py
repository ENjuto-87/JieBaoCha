"""第5课：证据辅助核验及可操作的异常提示；不读取 Key，不调用 API。"""
import re


def verify_evidence_three_way(evidence, contract_text):
    """保留原文；只忽略空白，不删除标点、不改数字，不进行语义匹配。"""
    compact_text = re.sub(r"\s+", "", contract_text)
    exact, whitespace, unmatched = [], [], []
    for item in evidence:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("证据必须是非空字符串。")
        if item in contract_text:
            exact.append(item)
        elif re.sub(r"\s+", "", item) in compact_text:
            whitespace.append(item)
        else:
            unmatched.append(item)
    return exact, whitespace, unmatched


def describe_api_error(error):
    """只返回受控提示，不回显响应正文、合同或认证信息。"""
    names = {cls.__name__ for cls in type(error).__mro__}
    code = getattr(error, "status_code", None)
    if "APITimeoutError" in names or isinstance(error, TimeoutError):
        return "请求超时。请检查网络，稍后重新审查；本次不自动重试。"
    if code == 401 or "AuthenticationError" in names:
        return "认证失败（HTTP 401）。请检查当前运行配置的 DEEPSEEK_API_KEY。"
    if code == 402:
        return "服务返回 HTTP 402。请到服务商控制台核查余额及账户状态。"
    if code == 403 or "PermissionDeniedError" in names:
        return "访问被拒绝（HTTP 403）。请检查账户权限及服务商限制。"
    if code == 429 or "RateLimitError" in names:
        return "请求受限（HTTP 429）。请稍后再试，并核查服务商限额。"
    if isinstance(code, int) and code >= 500:
        return "服务端暂时异常（HTTP 5xx）。请稍后重新审查。"
    if "APIConnectionError" in names or isinstance(error, ConnectionError):
        return "连接失败。请检查网络、代理与接口可达性。"
    if isinstance(error, ModuleNotFoundError):
        return "缺少 Python 依赖。请在当前项目解释器中安装 openai，并核对虚拟环境。"
    if code in (400, 404, 422):
        return "请求参数或接口不可用。请核查模型名称、接口地址及 SDK 版本。"
    return f"请求或响应处理失败（{type(error).__name__}）。请核对配置及返回结构。"
