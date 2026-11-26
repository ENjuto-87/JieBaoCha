"""第1课统一入口：文本输入，结构化结果输出。导入本文件不执行审查。"""

import os
from reliability import verify_evidence_three_way

from contract_checker import (
    call_deepseek,
    check_contract,
    check_guarantee_terms,
    inspect_guarantee_applicability,
    risk_rules,
    verify_evidence,
)
from address_rules import check_service_address
from ai_config import ConfigError, get_settings


def review_contract(contract_text: str, use_ai: bool = True) -> dict:
    """status 表示流程状态；证据匹配不代表法律判断正确。"""
    result = {
        "status": "invalid_input",
        "local_findings": [],
        "ai_findings": [],
        "messages": [],
    }
    if not isinstance(contract_text, str) or not contract_text.strip():
        result["messages"].append("合同文本必须是非空字符串，本次未执行审查。")
        return result

    # 保留原文，不对空格或换行作清洗，以免改变精确匹配的依据。
    result["local_findings"] = check_contract(contract_text, risk_rules)
    missing_terms, empty_terms = check_guarantee_terms(contract_text)
    for term in missing_terms:
        result["local_findings"].append({
            "risk_name": term,
            "risk_message": f"未找到“{term}：”字段，请人工核查；其他表述也可能约定该事项。",
        })
    for term in empty_terms:
        result["local_findings"].append({
            "risk_name": term,
            "risk_message": "标题已出现但内容留空，请人工核查。",
        })
    for issue in check_service_address(contract_text):
        result["local_findings"].append({
            "risk_name": "送达地址",
            "risk_message": issue,
        })
    result["status"] = "local_only"
    result["messages"].append("本地规则审查已完成；规则提示和空结果均须人工核查。")
    guarantee_applicable, applicability_note = inspect_guarantee_applicability(contract_text)
    if not guarantee_applicable and applicability_note:
        result["messages"].append(applicability_note)
    if not use_ai:
        result["messages"].append("本次关闭 AI，仅完成本地规则审查。")
        return result

    try:
        settings = get_settings()
    except ConfigError as exc:
        result["status"] = "ai_incomplete"
        result["messages"].append("AI 配置不可用：" + str(exc) + "已保留本地结果。")
        return result
    api_key = settings.api_key
    if not api_key:
        result["messages"].append("未配置 DEEPSEEK_API_KEY 或本机加密密钥，仅完成本地规则审查。请运行“配置AI接口”。")
        return result

    # 原 AI 函数的错误说明放进 messages，不再要求界面读取控制台输出。
    result["messages"].append(f"AI 服务：DeepSeek；请求模型：{settings.model}；网络超时配置：{settings.timeout}秒；不自动重试。")
    api_result = call_deepseek(contract_text, api_key, on_message=result["messages"].append, settings=settings)
    if api_result is None:
        result["status"] = "ai_incomplete"
        result["messages"].append("AI 审查未完成，已保留本地审查结果。")
        return result

    for risk in api_result["risks"]:
        finding = dict(risk)
        verified, whitespace, unverified = verify_evidence_three_way(finding["evidence"], contract_text)
        finding["verified_evidence"] = verified
        finding["whitespace_matched_evidence"] = whitespace
        finding["unverified_evidence"] = unverified
        result["ai_findings"].append(finding)
    result["status"] = "completed"
    result["messages"].append("本地及 AI 流程已完成；流程完成不表示合同安全。")
    if not result["ai_findings"]:
        result["messages"].append("AI 未识别到明显风险，仍须人工复核。")
    elif any(risk["unverified_evidence"] for risk in result["ai_findings"]):
        result["messages"].append("部分引用未通过精确或空白差异匹配，须人工核对。")
    if any(risk["whitespace_matched_evidence"] for risk in result["ai_findings"]):
        result["messages"].append("部分引用仅忽略空白后匹配，仍须核对原文上下文，不属于精确匹配。")
    if result["ai_findings"] and not any(risk["verified_evidence"] for risk in result["ai_findings"]):
        result["messages"].append("AI 意见没有通过原文精确匹配的证据，须人工复核。")
    return result
