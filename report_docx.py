"""Editable A4 reports from a frozen snapshot; no review or network calls."""
import hashlib
import base64
import json
import os
from pathlib import Path
import tempfile


def create_document(snapshot):
    try:
        from docx import Document
        from docx.shared import Mm, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.opc.part import Part
        from docx.opc.packuri import PackURI
    except ImportError as exc:
        raise ValueError("Word 导出缺少 python-docx，请在当前解释器安装 requirements.txt。") from exc
    from review_display import STATUS_LABELS, LEVEL_LABELS
    text = snapshot["contract_text"]
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != snapshot["contract_sha256"]:
        raise ValueError("报告快照原文与 SHA-256 不一致，请重新审查。")
    result = snapshot["result"]
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(20)
    section.left_margin = section.right_margin = Mm(23)
    section.footer_distance = Mm(10)
    for name, size, family in (("Normal", 10.5, "宋体"), ("Title", 21, "黑体"),
                                ("Heading 1", 15, "黑体"), ("Heading 2", 12, "黑体"),
                                ("Heading 3", 11, "黑体")):
        style = doc.styles[name]
        style.font.name = family
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            fonts.attrib.pop(qn("w:" + attribute), None)
        fonts.set(qn("w:eastAsia"), family)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.line_spacing = 1.3
        if name.startswith("Heading"):
            style.paragraph_format.space_before = Pt(12)
            style.paragraph_format.keep_with_next = True
        else:
            style.paragraph_format.keep_with_next = False
        style.paragraph_format.widow_control = True
        for border in style.element.xpath(".//w:pBdr"):
            border.getparent().remove(border)
    doc.core_properties.title = "借款担保合同辅助审查报告"
    doc.core_properties.author = "合同审查 Demo"
    doc.core_properties.identifier = snapshot["report_id"]
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("第 ")
    for instruction in ("PAGE", "NUMPAGES"):
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), instruction)
        footer._p.append(field)
        footer.add_run(" 页 / 共 " if instruction == "PAGE" else " 页")
    doc.add_paragraph("借款担保合同辅助审查报告", "Title")
    doc.add_paragraph("本报告记录本次输入的辅助筛查结果，供律师逐项复核。流程完成、空结果或引用匹配均不表示合同安全。")
    if snapshot["review_origin"] != "review_service":
        doc.add_paragraph("离线模拟或自定义入口测试报告，不能作为真实 AI 审查成果。")
    for label, value in (("报告编号", snapshot["report_id"]), ("审查时间 北京时间", snapshot["reviewed_at"]),
                         ("流程状态", f"{STATUS_LABELS[result['status']]}（{result['status']}）"),
                         ("脱敏声明", snapshot["input_anonymized"]),
                         ("请求 AI", "是" if snapshot["use_ai_requested"] else "否")):
        doc.add_paragraph(f"{label}：{value}")
    counts = {level: sum(x["risk_level"] == level for x in result["ai_findings"]) for level in LEVEL_LABELS}
    doc.add_paragraph(f"结果统计：本地待核查 {len(result['local_findings'])} 条；AI 意见 {len(result['ai_findings'])} 条；高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}。提示可能重复，不等于独立法律风险数。")
    doc.add_heading("阅读范围", 1)
    for notice in snapshot["notices"]:
        doc.add_paragraph(notice)
    doc.add_paragraph("主体身份类意见须结合未脱敏原件核查。保证与抵押识别及法律解释准确性仍需人工复核。")
    doc.add_heading("本地待核查项", 1)
    if not result["local_findings"]:
        doc.add_paragraph("规则未列出待核查项，不表示条款完整或合同安全。")
    for index, item in enumerate(result["local_findings"], 1):
        doc.add_heading(f"{index} {item['risk_name']}", 2)
        doc.add_paragraph(item["risk_message"])
    doc.add_heading("AI 意见与证据", 1)
    if not result["ai_findings"]:
        doc.add_paragraph("本次没有可展示的 AI 意见，请同时阅读流程状态和消息。")
    for index, item in enumerate(result["ai_findings"], 1):
        doc.add_heading(f"{index} {LEVEL_LABELS[item['risk_level']]}风险 {item['issue']}", 2)
        doc.add_paragraph("理由：" + item["reason"])
        for label, key in (("精确匹配的引用", "verified_evidence"),
                           ("空白差异匹配的引用 须核对上下文", "whitespace_matched_evidence"),
                           ("未匹配的引用 须重点核对", "unverified_evidence")):
            doc.add_heading(label, 3)
            for quote in item.get(key, []) or ["（无）"]:
                doc.add_paragraph(quote)
        doc.add_paragraph("人工复核意见：________________________________________________")
    doc.add_heading("流程消息", 1)
    for message in result["messages"] or ["（无）"]:
        doc.add_paragraph(message)
    doc.add_heading("人工复核记录", 1)
    for line in ("复核人：________________    日期：________________", "需核对的原件及补充材料：________________________________", "修正意见及处理结果：____________________________________", "保证与抵押规则适用性已复核：________    OCR 与原件已逐页核对：________"):
        doc.add_paragraph(line)
    source = snapshot.get("source_record")
    if source:
        doc.add_heading("导入来源记录", 1)
        doc.add_paragraph("引用匹配依据用户确认后的审查文本，匹配通过不代表 OCR 与原件一致。初始提取文本独立保存，不随编辑改变。")
        for label, key in (("来源文件", "file_name"), ("来源路径", "file_path"), ("来源文件 SHA-256", "file_sha256"), ("导入时间", "imported_at"), ("导入状态", "status"), ("初始提取文本 SHA-256", "initial_text_sha256")):
            doc.add_paragraph(f"{label}：{source.get(key, '')}")
        doc.add_paragraph("失败页：" + ("、".join(map(str, source.get("failed_pages", []))) or "无"))
        doc.add_paragraph("不完整来源确认：" + ("用户已知悉并确认仅对当前文本审查" if source.get("partial_acknowledged") else "未确认或不适用"))
        for page in source["pages"]:
            doc.add_heading(f"第 {page['page_number']} 页 {page['status']} {page['method']}", 2)
            if page.get("message"):
                paragraph = doc.add_paragraph(page["message"])
                paragraph.paragraph_format.keep_with_next = True
            doc.add_paragraph(page["initial_text"] or "（无提取文本）")
    doc.add_heading("本次审查文本", 1)
    doc.add_paragraph(f"原文字符数：{snapshot['contract_characters']}")
    digest = doc.add_paragraph("原文 SHA-256：" + snapshot["contract_sha256"])
    for run in digest.runs:
        run.font.size = Pt(8)
    # Normal paragraphs may span pages; no long tables or fixed row heights.
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        doc.add_paragraph(line)
    # Preserve the exact UTF-8 snapshot, including CR/LF distinctions, in the DOCX package.
    payload = base64.b64encode(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    part = Part(PackURI("/customXml/review-snapshot.xml"), "application/xml",
                b'<?xml version="1.0" encoding="UTF-8"?><reviewSnapshot xmlns="urn:contract-review:snapshot" encoding="base64-json">' + payload + b'</reviewSnapshot>')
    doc.part.relate_to(part, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml")
    return doc


def save_docx(snapshot, path):
    """Save beside target then atomically replace; failure preserves existing report."""
    document = create_document(snapshot)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".review-", suffix=".docx", delete=False) as stream:
            temporary = Path(stream.name)
        document.save(temporary)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
