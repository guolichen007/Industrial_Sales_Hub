"""对外案例的唯一取数入口：字段白名单。

任何新字段要进入对外材料，必须显式加入 PUBLIC_FIELDS 并通过脱敏测试。
"""
PUBLIC_FIELDS = ("title", "year", "region", "industry", "product_summary", "machine_type", "public_summary", "owner_display")


def public_case(perf) -> dict:
    owner_display = perf.owner_org if (perf.public_name_authorized and perf.owner_org) else ""
    data = {
        "title": perf.title,
        "year": perf.year,
        "region": perf.region,
        "industry": perf.industry,
        "product_summary": perf.product_summary,
        "machine_type": perf.machine_type,
        "public_summary": perf.public_summary,
        "owner_display": owner_display,
    }
    assert set(data) == set(PUBLIC_FIELDS)
    return data
