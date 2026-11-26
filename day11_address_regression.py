import sys
from address_rules import check_service_address
test_cases = [
    {
        "name": "字段缺失",
        "text": "保证方式：连带责任保证",
        "expected_count": 1,
        "expected_phrase": "未找到"
    },
    {
        "name": "地址留空",
        "text": "送达地址：   ",
        "expected_count": 1,
        "expected_phrase": "留空"
    },
    {
        "name": "待补充",
        "text": "送达地址：待补充",
        "expected_count": 1,
        "expected_phrase": "占位或未明确"
    },
    {
        "name": "没有明确",
        "text": "送达地址：合同没有明确送达文书地址",
        "expected_count": 1,
        "expected_phrase": "占位或未明确"
    },
    {
        "name": "上海过于笼统",
        "text": "送达地址：上海",
        "expected_count": 1,
        "expected_phrase": "地区名称"
    },
    {
        "name": "上海具体示例地址",
        "text": "送达地址：上海市黄浦区示例路8号201室",
        "expected_count": 0,
        "expected_phrase": None
    },
    {
        "name": "第二处地址留空",
        "text": (
            "送达地址：上海市黄浦区示例路8号201室\n"
            "送达地址：   "
        ),
        "expected_count": 1,
        "expected_phrase": "第2处"
    },
    {
        "name": "另行提供",
        "text": "送达地址：另行提供",
        "expected_count": 1,
        "expected_phrase": "占位或未明确"
    },
    {
        "name": "广州过于笼统",
        "text": "送达地址：广州",
        "expected_count": 1,
        "expected_phrase": "地区名称"
    },
    {
        "name": "广州具体示例地址",
        "text": "送达地址：广州市示例路8号",
        "expected_count": 0,
        "expected_phrase": None
    },
    {
         "name": "北京过于笼统",
         "text": "送达地址：北京市",
         "expected_count": 1,
         "expected_phrase": "地区名称"
    },
    {
         "name": "稍后提供",
         "text": "送达地址：稍后提供",
         "expected_count": 1,
         "expected_phrase": "占位或未明确"
    }

]
print("地址函数所属模块：", check_service_address.__module__)

passed_count = 0

for number, case in enumerate(test_cases, start=1):
    actual_issues = check_service_address(case["text"])

    count_ok = (
        len(actual_issues) == case["expected_count"]
    )

    expected_phrase = case["expected_phrase"]

    phrase_ok = (
        expected_phrase is None
        or any(
            expected_phrase in issue
            for issue in actual_issues
        )
    )

    passed = count_ok and phrase_ok

    if passed:
        passed_count += 1
        print(f"{number}. PASS - {case['name']}")
    else:
        print(f"{number}. FAIL - {case['name']}")
        print("   预期提醒数量：", case["expected_count"])
        print("   预期包含文字：", expected_phrase)
        print("   实际结果：", actual_issues)
total_count = len(test_cases)
failed_count = total_count - passed_count

print(f"测试结果：{passed_count}/{total_count} 通过")
print(f"失败数量：{failed_count}")

if failed_count > 0:
    sys.exit(1)

sys.exit(0)

