"""blog_agent 의 순수 함수(웹/DB/LLM 무관) 단위 테스트."""

import blog_agent as b


class TestNeedsLocalContext:
    def test_local_keyword_present(self):
        assert b.needs_local_context("가을 축제 일정")
        assert b.needs_local_context("근처 맛집 추천")

    def test_no_local_keyword(self):
        assert not b.needs_local_context("파이썬 데코레이터 정리")
        assert not b.needs_local_context("")


class TestHasRegionName:
    def test_region_present(self):
        assert b.has_region_name("대전 성심당")
        assert b.has_region_name("서울 근교 나들이")

    def test_region_absent(self):
        assert not b.has_region_name("축제 일정 정리")


class TestLocalizeQuery:
    def test_prepends_region_when_local_and_no_region(self):
        assert b.localize_query("가을 축제 일정") == "대전 가을 축제 일정"

    def test_keeps_query_when_region_already_present(self):
        assert b.localize_query("서울 맛집") == "서울 맛집"

    def test_keeps_query_when_not_local_topic(self):
        assert b.localize_query("파이썬 튜토리얼") == "파이썬 튜토리얼"

    def test_custom_region(self):
        assert b.localize_query("맛집 투어", region="부산") == "부산 맛집 투어"


class TestParseKeywords:
    def test_comma_string(self):
        assert b.parse_keywords("성심당, 대전 맛집 ,, 빵집") == ["성심당", "대전 맛집", "빵집"]

    def test_list_input_is_stripped(self):
        assert b.parse_keywords([" x ", "y", "  "]) == ["x", "y"]

    def test_empty_inputs(self):
        assert b.parse_keywords(None) == []
        assert b.parse_keywords("") == []
        assert b.parse_keywords("   ") == []


class TestKeywordsToStr:
    def test_joins_with_comma_space(self):
        assert b.keywords_to_str("a,b , c") == "a, b, c"

    def test_none_when_empty(self):
        assert b.keywords_to_str("") is None
        assert b.keywords_to_str([]) is None


class TestCountKeywordOccurrences:
    def test_counts_each_keyword(self):
        text = "성심당은 대전의 명물, 성심당 튀김소보로. 대전 사람이라면 성심당."
        assert b.count_keyword_occurrences(text, "성심당, 대전") == {"성심당": 3, "대전": 2}

    def test_missing_keyword_is_zero(self):
        assert b.count_keyword_occurrences("본문", "없는키워드") == {"없는키워드": 0}

    def test_empty_text(self):
        assert b.count_keyword_occurrences(None, "a") == {"a": 0}

    def test_no_keywords(self):
        assert b.count_keyword_occurrences("아무 본문", "") == {}


class TestSlugify:
    def test_keeps_hangul_and_replaces_separators(self):
        assert b.slugify("대전 성심당 빵집!") == "대전_성심당_빵집"

    def test_collapses_and_trims(self):
        assert b.slugify("  a --- b  ") == "a_b"

    def test_fallback_for_empty_result(self):
        assert b.slugify("") == "blog"
        assert b.slugify("!!!") == "blog"


class TestEmit:
    def test_noop_without_callback(self):
        b._emit(None, "research", "start")  # 예외가 없어야 한다

    def test_passes_event_dict(self):
        seen = []
        b._emit(seen.append, "save", "done", post_id=7)
        assert seen == [{"stage": "save", "state": "done", "post_id": 7}]
