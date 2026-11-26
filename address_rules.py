def check_service_address(contract_text):
    address_lines = [
        line.strip()
        for line in contract_text.splitlines()
        if line.strip().startswith("送达地址：")
    ]

    if not address_lines:
        return ["未找到“送达地址：”字段，请人工核查。"]

    placeholder_words = ["待补充", "待填写", "未明确", "没有明确","另行提供","稍后提供"]
    vague_places = ["上海", "上海市", "北京市", "江苏省无锡市","广州"]

    issues = []

    for number, line in enumerate(address_lines, start=1):
        address = line.split("：", 1)[1].strip()

        if not address:
            issues.append(f"第{number}处送达地址留空。")
        elif any(word in address for word in placeholder_words):
            issues.append(
                f"第{number}处送达地址含占位或未明确表述：{address}"
            )
        elif address in vague_places:
            issues.append(
                f"第{number}处送达地址仅填写清单中的地区名称，请人工核查：{address}"
            )

    return issues