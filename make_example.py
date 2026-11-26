"""Reproduce an illustrative report; AI findings below are explicitly fictional."""
from pathlib import Path
from report_export import build_snapshot, save_docx, save_json, save_html
from review_service import review_contract
from reliability import verify_evidence_three_way


def main():
    text = """虚构借款及保证合同
本合同仅供软件演示，不用于真实交易。
借款人：张测试。出借人：李示例。保证人：王测试。
主债权金额：人民币100000元。
借款期限自2026年10月12日至2027年10月11日。
年利率：6%。
保证方式：连带责任保证。
保证范围：本金及约定利息。
保证期间：主债务履行期限届满之日起二年。
送达地址：另行提供。
争议由出借人住所地人民法院解决。
"""
    result = review_contract(text, use_ai=False)
    # These findings exercise all report categories; they are not AI or legal advice.
    for level, issue, reason, quotes in [
        ("medium", "演示意见 核对送达地址", "示例理由：需结合原件补充核查。本意见是导出测试数据。", ["送达地址：另行提供。", "主债权金额：人民币 100000 元。", "原件不存在的示例引用"]),
        ("low", "演示意见 核查保证范围", "示例理由：请律师结合交易背景复核适用性，本演示不作法律结论。", ["保证范围：本金及约定利息。"]),
    ]:
        exact, whitespace, unmatched = verify_evidence_three_way(quotes, text)
        result["ai_findings"].append({"risk_level": level, "issue": issue, "reason": reason, "evidence": quotes,
                                      "verified_evidence": exact, "whitespace_matched_evidence": whitespace, "unverified_evidence": unmatched})
    result["status"] = "completed"
    result["messages"] = ["离线导出示例：AI 意见为人工编写的虚构测试数据，未调用真实 AI。", "completed 仅用于展示流程状态，不表示合同安全。"]
    snapshot = build_snapshot(text, result, True, True, review_origin="offline_simulation")
    output = Path(__file__).parent / "examples"
    output.mkdir(exist_ok=True)
    for suffix, writer in (("docx", save_docx), ("json", save_json), ("html", save_html)):
        writer(snapshot, output / f"示例审查报告.{suffix}")
    print("Created same-snapshot Word / JSON / HTML example (fictional findings, no API).")


if __name__ == "__main__":
    main()
