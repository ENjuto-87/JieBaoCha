"""第4课：冻结结果并导出 UTF-8 JSON / 自包含 HTML；不调用 API。"""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
from html import escape
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from review_display import format_result, STATUS_LABELS, LEVEL_LABELS

NOTICES = [
    "这是辅助审查记录，风险等级、理由和法律判断须由律师复核。",
    "completed 只表示本地及 AI 流程完成；空结果不表示合同安全。",
    "引用已匹配只表示文字存在于输入原文，不证明法律判断正确，也不单独证明全文缺少条款。",
    "空白差异匹配仅忽略空格、换行等空白，仍须核对上下文；未匹配引用须人工核对。",
    "本地提示须结合担保类型判断适用性；本课未修正保证与抵押识别规则。",
]


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError(f"{label} 应为字符串列表。")
    return list(value)


def build_snapshot(contract_text, result, use_ai_requested, anonymized=False,
                   review_origin="review_service", source_record=None):
    """仅保存已知返回字段，不读取环境变量，不保存 SDK 响应或 Key 配置。"""
    if not isinstance(contract_text, str) or not contract_text.strip():
        raise ValueError("没有可保存的合同原文。")
    format_result(result)  # 验证第1、3课共同的返回协议。
    if result["status"] == "invalid_input":
        raise ValueError("输入未通过审查，不能作为审查报告保存。")
    clean = {"status": result["status"], "local_findings": [],
             "ai_findings": [], "messages": _strings(result["messages"], "messages")}
    for item in result["local_findings"]:
        if any(not isinstance(item.get(k), str) for k in ("risk_name", "risk_message")):
            raise ValueError("本地待核查项格式不正确。")
        clean["local_findings"].append({k: item[k] for k in ("risk_name", "risk_message")})
    for item in result["ai_findings"]:
        if item.get("risk_level") not in LEVEL_LABELS:
            raise ValueError("AI 风险等级格式不正确。")
        if any(not isinstance(item.get(k), str) for k in ("issue", "reason")):
            raise ValueError("AI 意见或理由格式不正确。")
        entry = {k: item[k] for k in ("risk_level", "issue", "reason")}
        for key in ("evidence", "verified_evidence", "unverified_evidence"):
            entry[key] = _strings(item.get(key), key)
        entry["whitespace_matched_evidence"] = _strings(item.get("whitespace_matched_evidence", []), "whitespace_matched_evidence")
        clean["ai_findings"].append(entry)
    now = datetime.now(timezone(timedelta(hours=8)))
    snapshot = {
        "schema_version": "1.2" if source_record else "1.1",
        "report_id": "review-" + now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8],
        "reviewed_at": now.isoformat(timespec="seconds"),
        "review_origin": review_origin,
        "use_ai_requested": bool(use_ai_requested),
        "input_anonymized": "用户声明已脱敏" if anonymized else "未声明，需核对原件",
        "contract_characters": len(contract_text),
        "contract_sha256": hashlib.sha256(contract_text.encode("utf-8")).hexdigest(),
        "contract_text": contract_text,
        "result": copy.deepcopy(clean),
        "notices": list(NOTICES),
    }
    if source_record is not None:
        if not isinstance(source_record, dict) or source_record.get("status") not in ("complete", "partial"):
            raise ValueError("来源记录无可审查文本。")
        if source_record["status"] == "partial" and not source_record.get("partial_acknowledged"):
            raise ValueError("不完整来源须先明确确认，再保存审查快照。")
        original = source_record["initial_text"]
        if hashlib.sha256(original.encode("utf-8")).hexdigest() != source_record["initial_text_sha256"]:
            raise ValueError("初始识别记录与摘要不一致。")
        snapshot["source_record"] = copy.deepcopy(source_record)
        snapshot["source_record"]["confirmed_text_sha256"] = snapshot["contract_sha256"]
        snapshot["notices"].append("引用匹配依据用户确认后的审查文本，匹配通过不代表 OCR 与原件一致。初始识别文本和校正后审查文本分别记录。")
        if source_record["status"] == "partial":
            snapshot["notices"].append("来源文件仅部分页面识别成功；本报告仅针对当前已确认文本，不是完整合同审查。失败页仍须补充。")
    return snapshot


def _e(value):
    return escape(str(value), quote=True)


def _quotes(items):
    return "".join(f'<li class="quote">{_e(x)}</li>' for x in items) or "<li>（无）</li>"


def render_html(snapshot):
    """所有合同和模型文字均转义，作为文字展示；不载入外部资源。"""
    result = snapshot["result"]
    counts = {level: sum(x["risk_level"] == level for x in result["ai_findings"])
              for level in LEVEL_LABELS}
    status = STATUS_LABELS[result["status"]]
    demo = snapshot["review_origin"] != "review_service"
    banner = "离线模拟 / 自定义入口测试报告，不能作为真实 AI 审查成果。" if demo else "审查流程记录 · 待律师复核"
    local = "".join(
        f'<article><h3>{i}. {_e(x["risk_name"])}</h3><p>{_e(x["risk_message"])}</p></article>'
        for i, x in enumerate(result["local_findings"], 1)
    ) or "<p>规则没有列出待核查项；不表示合同完整或安全。</p>"
    ai = []
    for i, x in enumerate(result["ai_findings"], 1):
        ai.append(f'''<article><h3>{i}. <span class="level {x['risk_level']}">{LEVEL_LABELS[x['risk_level']]}风险（AI）</span> {_e(x['issue'])}</h3>
<p><strong>理由：</strong>{_e(x['reason'])}</p>
<h4>原文精确匹配的引用</h4><ul>{_quotes(x['verified_evidence'])}</ul>
<h4>仅忽略空白差异后匹配 · 须核对上下文</h4><ul>{_quotes(x.get('whitespace_matched_evidence', []))}</ul>
<h4>未匹配的引用 · 须人工核对</h4><ul class="unverified">{_quotes(x['unverified_evidence'])}</ul>
<p class="muted">辅助匹配仅忽略空白；不修改输入原文，不代表法律解释正确。</p></article>''')
    ai_text = "".join(ai) or "<p>本次没有可展示的 AI 意见，请查看流程状态和消息。</p>"
    identity_note = ("输入已声明脱敏：主体身份缺失类意见须核对未脱敏原件，不能直接计入真实合同缺陷。"
                     if snapshot["input_anonymized"] == "用户声明已脱敏"
                     else "输入未声明脱敏；当事人身份类意见须结合原件核查。")
    notices = "".join(f"<li>{_e(x)}</li>" for x in snapshot["notices"])
    messages = "".join(f"<li>{_e(x)}</li>" for x in result["messages"]) or "<li>（无）</li>"
    source_html = ""
    source = snapshot.get("source_record")
    if source:
        pages = "".join(f"<h3>第 {p['page_number']} 页 · {_e(p['method'])} · {_e(p['status'])}</h3><p>{_e(p['message'])}</p><pre>{_e(p['initial_text'] or '（无可用文本）')}</pre>" for p in source["pages"])
        source_html = f'''<h2>导入来源记录与初始提取文本</h2><p>来源：{_e(source['file_name'])}<br>路径：{_e(source['file_path'])}<br>导入状态：{_e(source['status'])}；总页数：{source['page_count']}；失败页：{_e(source['failed_pages'])}<br>来源文件 SHA-256：{_e(source['file_sha256'])}<br>初始文本 SHA-256：{_e(source['initial_text_sha256'])}<br>不完整来源确认：{'已知悉并确认' if source.get('partial_acknowledged') else '未确认或不适用'}</p><p>引用匹配依据用户确认后的审查文本；匹配通过不代表 OCR 与原件一致。初始提取文本不会随编辑改变。</p>{pages}'''
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>合同审查报告 · {_e(snapshot['report_id'])}</title>
<style>
* {{box-sizing:border-box}} body {{margin:0;background:#edf1f5;color:#172a3d;font:15px/1.75 "Microsoft YaHei",sans-serif}}
main {{max-width:960px;margin:32px auto;background:white;padding:36px 44px;border-radius:10px}}
h1 {{font-size:27px;margin:8px 0}} h2 {{font-size:20px;margin-top:30px;border-bottom:2px solid #dbe5ef;padding-bottom:6px}}
h3 {{font-size:16px;margin:0 0 10px}} h4 {{font-size:14px;margin-bottom:4px}}
p,li {{white-space:pre-wrap;overflow-wrap:anywhere}} article {{padding:18px 0;border-bottom:1px solid #dae2ea}}
.banner {{background:#edf4ff;padding:12px;border-left:4px solid #2765a1}}
.meta,.muted {{font-size:13px;color:#526578}} .status {{font-weight:bold}}
.stats {{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}} .stats span {{padding:6px 12px;background:#f0f4f8;border-radius:4px}}
.level {{padding:2px 6px;border-radius:3px;font-size:13px}} .high {{background:#fff0ed;color:#8f332b}}
.medium {{background:#fff5d9;color:#78571b}} .low {{background:#eaf0f7;color:#315572}}
.quote {{font-family:"Microsoft YaHei",sans-serif}} .unverified {{color:#873e2f}}
.notice {{background:#faf5e8;padding:10px 20px}} pre {{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.8 "Microsoft YaHei",sans-serif;background:#f6f8fa;padding:16px}}
@page {{size:A4;margin:16mm}} @media print {{body {{background:white;font-size:11pt}} main {{margin:0;padding:0;max-width:none}} h2,h3,h4 {{break-after:avoid}} .banner,.stats {{break-inside:avoid}} pre {{background:white}}}}
</style></head><body><main>
<div class="banner">{_e(banner)}</div><h1>借款担保合同审查报告</h1>
<div class="meta">审查完成时间（北京时间）：{_e(snapshot['reviewed_at'])}<br>报告编号：{_e(snapshot['report_id'])}<br>
原文字符数：{snapshot['contract_characters']} · 请求 AI：{'是' if snapshot['use_ai_requested'] else '否'} · {_e(snapshot['input_anonymized'])}</div>
<p class="status">流程状态：{_e(status)}（{_e(result['status'])}）</p>
<div class="stats"><span>本地待核查 {len(result['local_findings'])} 条</span><span>AI 意见 {len(result['ai_findings'])} 条</span>
<span>AI 高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}</span></div>
<div class="notice"><strong>阅读范围</strong><ul>{notices}<li>{_e(identity_note)}</li></ul></div>
<h2>一、本地待核查</h2>{local}<h2>二、AI 意见与证据</h2>{ai_text}
<h2>三、流程消息</h2><ul>{messages}</ul>
<h2>四、人工复核记录</h2><p>复核人：________________　日期：________________<br>修正意见 / 待补充材料：________________________________________________</p>
{source_html}
<h2>附件：本次审查原文</h2><p class="meta">原文 SHA-256：{_e(snapshot['contract_sha256'])}<br>原文按本次输入保存；报告和 JSON 均包含合同内容。</p>
<pre>{_e(snapshot['contract_text'])}</pre></main></body></html>'''


def suggested_filename(snapshot, suffix):
    return f"{snapshot['report_id']}{suffix}"


def _atomic_write(path, text):
    """先写同目录临时文件，再替换目标；写入失败尽量保留原文件。"""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix=".review-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path


def save_json(snapshot, path):
    data = json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False)
    return _atomic_write(path, data + "\n")


def save_html(snapshot, path):
    return _atomic_write(path, render_html(snapshot))


def save_docx(snapshot, path):
    # Lazy import keeps paste, TXT, JSON and HTML usable without Word dependencies.
    from report_docx import save_docx as write_document
    return write_document(snapshot, path)
