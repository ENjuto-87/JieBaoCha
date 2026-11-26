"""第3课：把审查字典变成可阅读的文字。没有窗口或 API 副作用。"""

STATUS_LABELS = {
    "completed": "本地及 AI 流程完成",
    "local_only": "仅完成本地规则审查",
    "ai_incomplete": "AI 审查未完成（保留本地结果）",
    "invalid_input": "输入不符合要求（未执行审查）",
}
LEVEL_LABELS = {"high": "高", "medium": "中", "low": "低"}


def format_result(result):
    """沿用第1课返回协议；不重新计算风险等级或修改证据。"""
    if not isinstance(result, dict):
        raise ValueError("审查入口必须返回字典。")
    for key in ("local_findings", "ai_findings", "messages"):
        if not isinstance(result.get(key), list):
            raise ValueError(f"返回值中的 {key} 必须是列表。")
    status = result.get("status")
    if status not in STATUS_LABELS:
        raise ValueError("未知流程状态，请核对第1课接口。")
    local = result["local_findings"]
    ai = result["ai_findings"]
    label = STATUS_LABELS[status]
    summary = f"{label} | 本地待核查 {len(local)} 条 | AI 意见 {len(ai)} 条"
    local_lines = ["本地规则提示（须人工核查）", ""]
    if not local:
        local_lines.append("规则没有列出待核查项；这不表示条款完整或合同安全。")
    for number, finding in enumerate(local, 1):
        local_lines.extend([
            f"{number}. {finding['risk_name']}",
            finding["risk_message"], "",
        ])
    ai_lines = ["AI 意见（等级与理由由模型生成，须律师复核）", ""]
    if not ai:
        ai_lines.append("本次没有可展示的 AI 意见，请同时阅读“流程消息”。")
    for number, finding in enumerate(ai, 1):
        ai_lines.extend([
            f"{number}. [{LEVEL_LABELS.get(finding['risk_level'], '未知')}] {finding['issue']}",
            f"理由：{finding['reason']}",
            "原文精确匹配的引用：",
        ])
        verified = finding["verified_evidence"]
        unverified = finding["unverified_evidence"]
        ai_lines.extend([f"  [已匹配] {item}" for item in verified] or ["  （无）"])
        ai_lines.append("仅忽略空白差异后匹配的引用（须核对上下文）：")
        ai_lines.extend([f"  [空白差异匹配] {item}" for item in finding.get("whitespace_matched_evidence", [])] or ["  （无）"])
        ai_lines.append("未匹配的引用（须重点核对）：")
        ai_lines.extend([f"  [未匹配] {item}" for item in unverified] or ["  （无）"])
        ai_lines.extend([
            "核验范围：只核验引用是否存在于输入原文；不证明法律判断正确，也不单独证明全文缺少条款。", "",
        ])
    message_lines = [f"状态代码：{status}", f"状态说明：{label}", ""]
    message_lines.extend(str(item) for item in result["messages"])
    message_lines.extend(["", "样本中的甲、乙、丙为脱敏代称；有关主体身份的意见须结合完整材料复核。"])
    return summary, "\n".join(local_lines), "\n".join(ai_lines), "\n".join(message_lines)


def run_review_job(contract_text, use_ai, output_queue, review_fn=None):
    """后台线程只计算并写队列，不读取或修改任何 Tk 控件。"""
    try:
        if review_fn is None:
            from review_service import review_contract
            review_fn = review_contract
        result = review_fn(contract_text, use_ai=use_ai)
        displays = format_result(result)
        output_queue.put(("ok", result, displays))
    except Exception as exc:
        # 不把原始异常或 Key 打到窗口；类型用于初步定位。
        output_queue.put(("error", type(exc).__name__, None))
