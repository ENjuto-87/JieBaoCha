"""单元测试：制式表格式合同兼容、复杂条文叙述型合同要素去误报、主题去重及4条法定红线规则。"""
from collections import Counter
import unittest

import contract_checker
from report_export import build_snapshot
from review_service import review_contract


TABULAR_COMPLETE_CONTRACT = """借款担保合同（制式表格式）
出借人：甲公司
借款人：乙公司
保证人：丙公司
主债权金额：人民币100万元
保证方式：连带责任保证
保证范围：主债权本金、利息、违约金及律师费、诉讼费、保全费
保证期间：主债务履行期限届满之日起二年
争议解决：向甲方所在地有管辖权的人民法院提起诉讼
送达地址：上海市浦东新区世纪大道88号1201室
"""

TABULAR_DEFECTIVE_CONTRACT = """借款担保合同（制式缺失样本）
甲向乙借款人民币50万元。
丙为该笔借款提供保证。
保证方式：
保证期间：
送达地址：另行提供
"""

NARRATIVE_COMPLEX_REDLINE_CONTRACT = """商事借款与担保合同

第一条 借款本金与放款安排
1.1 甲方向乙方提供借款本金人民币伍佰万元整，借款期限十二个月。双方同意甲方在发放款项时从本金中预先扣除首期利息及综合管理费人民币贰拾伍万元，实际划付人民币肆佰柒拾伍万元，但乙方仍按人民币伍佰万元本金计息并承担还款义务。

第二条 物的担保与处置特别约定
2.1 乙方以其名下位于上海市徐汇区示例路100号的不动产设定抵押；若乙方届期未足额清偿债务，上述抵押房产所有权直接归甲方所有，无需另行拍卖或变卖。

第三条 保证担保条款
3.1 丙方自愿为乙方在本合同项下的借款债务向甲方提供保证担保（具体保证方式双方未作进一步文字限定）。
3.2 丙方担保的范围包括主合同项下借款本金、利息、罚息、违约金以及甲方为实现债权而支出的律师费、诉讼费、保全费等全部费用。
3.3 丙方的保证期间自本合同生效之日起直至主债务本息全部还清之日止。

第四条 通知与文书送达
4.1 各方确认本合同首部所列通讯地址（上海市静安区南京西路666号9层）为各方之间往来通知及涉诉法律文书的有效送达地址。

第五条 法律适用与争议解决
5.1 凡因本合同引起的或与本合同有关的任何争议，双方协商不成的，任何一方均可向上海仲裁委员会申请仲裁，或者向甲方住所地人民法院起诉。
"""


class NarrativeAndRedlineTests(unittest.TestCase):
    def assert_valid_schema_and_deduped(self, res):
        self.assertEqual(set(res), {"status", "local_findings", "ai_findings", "messages"})
        for item in res["local_findings"]:
            self.assertEqual(set(item), {"risk_name", "risk_message"})
            self.assertIsInstance(item["risk_name"], str)
            self.assertIsInstance(item["risk_message"], str)
            self.assertTrue(item["risk_name"].strip())
            self.assertTrue(item["risk_message"].strip())
        counts = Counter(item["risk_name"] for item in res["local_findings"])
        duplicates = {k: v for k, v in counts.items() if v > 1}
        self.assertEqual(duplicates, {}, f"发现未去重的重复审查维度: {duplicates}")

    def test_tabular_contracts_remain_compatible(self):
        clean_res = review_contract(TABULAR_COMPLETE_CONTRACT, use_ai=False)
        self.assert_valid_schema_and_deduped(clean_res)
        self.assertEqual(clean_res["local_findings"], [])

        defect_res = review_contract(TABULAR_DEFECTIVE_CONTRACT, use_ai=False)
        self.assert_valid_schema_and_deduped(defect_res)
        by_name = {f["risk_name"]: f["risk_message"] for f in defect_res["local_findings"]}
        self.assertIn("保证方式", by_name)
        self.assertIn("保证期间", by_name)
        self.assertIn("留空", by_name["保证期间"])
        self.assertIn("送达地址", by_name)
        self.assertIn("占位", by_name["送达地址"])

    def test_complex_narrative_contract_no_element_false_positives_and_hits_all_four_redlines(self):
        res = review_contract(NARRATIVE_COMPLEX_REDLINE_CONTRACT, use_ai=False)
        self.assert_valid_schema_and_deduped(res)

        names = [f["risk_name"] for f in res["local_findings"]]
        # 1. 条文已明确约定的要素不再因缺少“字段：”冒号而误报
        for non_missing in ("主债权金额", "保证范围", "保证期间", "送达地址", "争议解决", "实现债权费用"):
            self.assertNotIn(non_missing, names, f"{non_missing} 在条文叙述型合同中不应误报")

        # 2. 未写明具体保证方式时，保证方式仅提示 1 次，且援引《民法典》第686条
        guarantee_mode_items = [f for f in res["local_findings"] if f["risk_name"] == "保证方式"]
        self.assertEqual(len(guarantee_mode_items), 1)
        self.assertIn("第686条", guarantee_mode_items[0]["risk_message"])

        # 3. 新增 4 条高确定性法定红线全部精准命中
        by_name = {f["risk_name"]: f["risk_message"] for f in res["local_findings"]}
        self.assertIn("疑似砍头息（预先扣除本息/费用）", by_name)
        self.assertIn("第670条", by_name["疑似砍头息（预先扣除本息/费用）"])

        self.assertIn("疑似流押/流质条款", by_name)
        self.assertIn("401", by_name["疑似流押/流质条款"])
        self.assertIn("428", by_name["疑似流押/流质条款"])

        self.assertIn("疑似“或裁或审”无效争议解决条款", by_name)
        self.assertIn("第7条", by_name["疑似“或裁或审”无效争议解决条款"])

        self.assertIn("疑似保证期间约定不明", by_name)
        self.assertIn("第32条", by_name["疑似保证期间约定不明"])

        # 4. 验证导出快照结构无缝兼容
        snap = build_snapshot(NARRATIVE_COMPLEX_REDLINE_CONTRACT, res, False, True)
        self.assertEqual(snap["result"]["local_findings"], res["local_findings"])

    def test_narrative_explicit_guarantee_mode_synonyms(self):
        variants = {
            "joint_guarantee_short": ("丙公司自愿为乙方上述借款提供连带保证。", "explicit_joint"),
            "joint_repayment": ("保证人丙对乙的借款承担连带清偿责任。", "explicit_joint"),
            "narrative_is_mode": ("第三条 本合同项下保证方式为连带保证。", "explicit_joint"),
            "narrative_general": ("第五条 本合同保证方式为一般保证。", "explicit_general"),
        }
        for label, (text, expected) in variants.items():
            with self.subTest(label=label):
                self.assertEqual(contract_checker.recognize_explicit_guarantee_mode(text), expected)
                findings = review_contract(text, use_ai=False)["local_findings"]
                self.assertFalse([f for f in findings if f["risk_name"] == "保证方式"])

    def test_redlines_do_not_false_positive_on_compliant_or_cross_paragraph_clauses(self):
        compliant_contract = """合规商事借款担保合同
第一条 借款金额为人民币200万元，借款利息不得预先在本金中扣除。
第二条 抵押期间，抵押物仍归抵押人所有；双方不得约定抵押财产直接归债权人所有。
第三条 保证人丙公司承担连带责任保证，担保范围包括本金、利息及律师费。
第四条 保证期间为主债务履行期限届满之日起二年；借款利息计算直至全部本息付清之日止。
第五条 各方确认首部通讯地址为法律文书有效送达地址。
第六条 争议提交北京仲裁委员会仲裁，任何一方不得向人民法院起诉。
"""
        res = review_contract(compliant_contract, use_ai=False)
        self.assert_valid_schema_and_deduped(res)
        self.assertEqual(res["local_findings"], [])

        # 跨段落隔离：不同段落的词汇不应拼接触发流押或或裁或审
        cross_para = (
            "第一段：乙方提供名下房产作为抵押物。\n"
            "第二段：合同解除后剩余办公用品直接归甲方所有。\n"
            "第三段：知识产权争议提交仲裁机构仲裁。\n"
            "第四段：借款纠纷或者其他事项依法向人民法院起诉。"
        )
        redlines = contract_checker.check_statutory_redlines(cross_para)
        self.assertEqual(redlines, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
