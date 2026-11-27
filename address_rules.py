import re


_ADDRESS_SYNONYM_RE = re.compile(r"(?:送达地址|通讯地址|有效送达)")
_COLON_HEADER_RE = re.compile(r"^(?:送达地址|通讯地址)\s*[:：](.*)$")


def check_service_address(contract_text):
    placeholder_words = ["待补充", "待填写", "未明确", "没有明确", "另行提供", "稍后提供"]
    vague_places = ["上海", "上海市", "北京市", "江苏省无锡市", "广州"]

    header_matches = []
    for line in contract_text.splitlines():
        m = _COLON_HEADER_RE.match(line.strip())
        if m:
            header_matches.append(m.group(1).strip())

    if header_matches:
        issues = []
        for number, address in enumerate(header_matches, start=1):
            if not address:
                issues.append(f"第{number}处送达地址留空。")
            elif any(word in address for word in placeholder_words):
                issues.append(f"第{number}处送达地址含占位或未明确表述：{address}")
            elif address in vague_places:
                issues.append(f"第{number}处送达地址仅填写清单中的地区名称，请人工核查：{address}")
        return issues

    if not _ADDRESS_SYNONYM_RE.search(contract_text):
        return ["未找到送达地址相关约定（送达地址/通讯地址/有效送达），请人工核查。"]

    issues = []
    for clause in re.split(r"[。；;\r\n]+", contract_text):
        text = clause.strip()
        if not text or not _ADDRESS_SYNONYM_RE.search(text):
            continue
        if any(word in text for word in placeholder_words):
            issues.append(f"送达地址条款含占位或未明确表述：{text}")
            break
    return issues
