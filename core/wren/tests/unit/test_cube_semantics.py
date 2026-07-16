from __future__ import annotations

from wren.cube_semantics import resolve_cubes


def _member(name: str, label: str, **extra):
    return {"name": name, "expression": name, "type": "STRING", "label": label, **extra}


def _manifest() -> dict:
    shared_measures = [
        {
            "name": "sale_amt_sum",
            "expression": "SUM(sale_amt)",
            "type": "DOUBLE",
            "label": "广告销售额",
            "synonyms": ["归因销售额"],
        },
        {
            "name": "ctr",
            "expression": "clicks_sum / impressions_sum",
            "type": "DOUBLE",
            "label": "点击率",
            "synonyms": ["广告点击率"],
        },
        {
            "name": "clicks_sum",
            "expression": "SUM(clicks)",
            "type": "BIGINT",
            "label": "点击量",
            "synonyms": ["点击"],
        },
        {
            "name": "impressions_sum",
            "expression": "SUM(impressions)",
            "type": "BIGINT",
            "label": "曝光量",
            "synonyms": ["曝光"],
        },
    ]
    return {
        "cubes": [
            {
                "name": "sp_campaign_performance",
                "baseObject": "campaigns",
                "label": "SP 广告活动效果",
                "synonyms": ["活动表现"],
                "priority": 100,
                "measures": shared_measures,
                "dimensions": [
                    _member("tenant", "租户"),
                    _member("marketplace", "站点"),
                    _member("campaign", "广告活动"),
                ],
                "hierarchies": {"campaign_drill": ["tenant", "campaign"]},
            },
            {
                "name": "sp_search_term_performance",
                "baseObject": "search_terms",
                "label": "SP 搜索词效果",
                "synonyms": ["客户搜索词表现"],
                "priority": 60,
                "measures": shared_measures,
                "dimensions": [
                    _member("marketplace", "站点"),
                    _member("campaign", "广告活动"),
                    _member("search_term", "搜索词", synonyms=["客户搜索词"]),
                ],
                "hierarchies": {"search_term_drill": ["campaign", "search_term"]},
            },
        ]
    }


def test_chinese_query_prefers_cube_level_business_subject() -> None:
    result = resolve_cubes(_manifest(), "昨天按搜索词看广告销售额和点击率")

    assert result["matches"][0]["cube"] == "sp_search_term_performance"
    assert [m["name"] for m in result["matches"][0]["measures"]] == [
        "sale_amt_sum",
        "ctr",
    ]
    assert result["matches"][0]["dimensions"][0]["name"] == "search_term"


def test_technical_measure_name_does_not_need_a_duplicate_synonym() -> None:
    result = resolve_cubes(_manifest(), "按搜索词看 CTR")

    assert result["matches"][0]["cube"] == "sp_search_term_performance"
    assert [m["name"] for m in result["matches"][0]["measures"]] == ["ctr"]


def test_priority_breaks_equal_semantic_score_for_shared_members() -> None:
    result = resolve_cubes(_manifest(), "按站点看曝光量")

    assert result["matches"][0]["cube"] == "sp_campaign_performance"
    assert result["matches"][0]["priority"] == 100
    assert result["matches"][0]["score"] == result["matches"][1]["score"]
    assert result["ambiguous"] is False


def test_priority_does_not_override_explicit_business_subject() -> None:
    result = resolve_cubes(_manifest(), "按搜索词看曝光量")

    assert result["matches"][0]["cube"] == "sp_search_term_performance"
    assert result["matches"][0]["score"] > result["matches"][1]["score"]


def test_drill_request_expands_single_cube_hierarchy() -> None:
    result = resolve_cubes(_manifest(), "广告活动销售额下钻明细")

    top = result["matches"][0]
    assert top["cube"] == "sp_campaign_performance"
    assert top["suggestedQuery"]["measures"] == ["sale_amt_sum"]
    assert top["suggestedQuery"]["dimensions"] == ["tenant", "campaign"]
    assert top["hierarchies"][0]["name"] == "campaign_drill"


def test_unmatched_question_returns_empty_result() -> None:
    result = resolve_cubes(_manifest(), "天气预报")

    assert result == {"query": "天气预报", "ambiguous": False, "matches": []}
