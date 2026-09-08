"""generate_blog 파이프라인 오케스트레이션 테스트 (LLM/DB 는 모두 모의)."""

import blog_agent as b


def _stub_pipeline(
    monkeypatch, *, similar=None, post_id=99, critique_pass=True,
    search_retried=False, research_grounded=True, research_reason="",
):
    """research/outline/draft/critique/rewrite/persist/유사조회를 결정적 스텁으로 교체.

    반환된 dict 의 "draft_calls"/"persist_calls" 로 각 호출 인자를 확인할 수 있다.
    """
    calls: dict = {"draft": [], "rewrite": [], "persist": []}

    monkeypatch.setattr(b, "find_similar_posts", lambda *a, **k: list(similar or []))

    def _research(topic, on_search=None, on_stage=None):
        if on_search:
            on_search("대전 " + topic)
        if on_stage:  # run_search_loop 이 발행하는 grounding 이벤트를 흉내
            on_stage({"stage": "grounding", "state": "start"})
            on_stage({"stage": "grounding", "state": "done",
                      "grounded": research_grounded, "reason": research_reason})
        return {
            "notes": "리서치 노트",
            "sources": [{"url": "https://daejeon.go.kr/x", "trusted": True}],
            "search_retried": search_retried,
            "research_grounded": research_grounded,
            "research_reason": research_reason,
        }

    def _write_draft(topic, outline, notes, tone="정보성", seo_keywords=None,
                     low_grounding=False):
        calls["draft"].append({"tone": tone, "low_grounding": low_grounding})
        return f"# 초안 ({tone}) 성심당 성심당 성심당"

    def _rewrite_draft(*a, **k):
        calls["rewrite"].append(k)
        return "# 재작성 초안 성심당 성심당 성심당"

    monkeypatch.setattr(b, "research", _research)
    monkeypatch.setattr(b, "make_outline", lambda topic, notes: "## 아웃라인")
    monkeypatch.setattr(b, "write_draft", _write_draft)
    monkeypatch.setattr(
        b, "critique_draft",
        lambda draft, ctx, tone="정보성", seo_keywords=None: {
            "pass": critique_pass,
            "feedback": "좋음" if critique_pass else "톤이 요청과 다르고 SEO 과다",
        },
    )
    monkeypatch.setattr(b, "rewrite_draft", _rewrite_draft)
    monkeypatch.setattr(
        b, "persist_blog",
        lambda *a, **k: (calls["persist"].append(k), post_id)[1],
    )
    monkeypatch.setattr(b, "get_topic_image", lambda topic: "https://img.example/x.jpg")
    return calls


EXPECTED_KEYS = {
    "existing", "aborted", "similar_posts", "post_id", "similarity", "topic",
    "tone", "seo_keywords", "seo_report", "research", "sources", "outline",
    "draft", "final_content", "image_url", "search_retried", "critique_passed",
    "critique_feedback", "was_rewritten", "research_grounded", "research_reason",
}


class TestNewArticlePath:
    def test_stage_events_when_critique_passes(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=True)
        events = []
        b.generate_blog("성심당 빵집", on_stage=events.append, tone="리뷰형")
        assert [(e["stage"], e["state"]) for e in events] == [
            ("similar", "start"), ("similar", "done"),
            ("research", "start"),
            ("grounding", "start"), ("grounding", "done"),
            ("research", "done"),
            ("outline", "start"), ("outline", "done"),
            ("draft", "start"), ("draft", "done"),
            ("critique", "start"), ("critique", "done"),
            ("image", "start"), ("image", "done"),
            ("save", "start"), ("save", "done"),
        ]
        crit_done = next(e for e in events if e["stage"] == "critique" and e["state"] == "done")
        assert crit_done["passed"] is True

    def test_stage_events_when_critique_fails_triggers_rewrite(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=False)
        events = []
        r = b.generate_blog("성심당 빵집", on_stage=events.append)
        seq = [(e["stage"], e["state"]) for e in events]
        assert ("rewrite", "start") in seq and ("rewrite", "done") in seq
        assert seq.count(("rewrite", "start")) == 1
        assert seq.index(("critique", "done")) < seq.index(("rewrite", "start"))
        assert seq.index(("rewrite", "done")) < seq.index(("image", "start"))
        assert r["was_rewritten"] is True
        assert r["draft"] == "# 재작성 초안 성심당 성심당 성심당"
        assert r["final_content"] == r["draft"]

    def test_no_rewrite_when_critique_passes(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=True)
        events = []
        r = b.generate_blog("성심당 빵집", on_stage=events.append)
        assert all(e["stage"] != "rewrite" for e in events)
        assert r["was_rewritten"] is False
        assert r["draft"].startswith("# 초안")

    def test_result_shape_and_values(self, monkeypatch):
        _stub_pipeline(monkeypatch, post_id=123, critique_pass=True, search_retried=True)
        r = b.generate_blog("성심당 빵집", tone="정보성", seo_keywords="성심당, 없는키워드")
        assert set(r) == EXPECTED_KEYS
        assert r["existing"] is False
        assert r["aborted"] is False
        assert r["post_id"] == 123
        assert r["seo_report"] == {"성심당": 3, "없는키워드": 0}
        assert r["image_url"] == "https://img.example/x.jpg"
        assert r["search_retried"] is True
        assert r["critique_passed"] is True
        assert r["critique_feedback"] == "좋음"
        assert r["was_rewritten"] is False
        assert r["research_grounded"] is True

    def test_critique_failure_recorded_in_result(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=False)
        r = b.generate_blog("성심당 빵집")
        assert r["critique_passed"] is False
        assert "SEO" in r["critique_feedback"]
        assert r["was_rewritten"] is True

    def test_repetition_warning_appended_when_rewrite_still_repetitive(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=False)
        repetitive = (
            "암호화폐와 달리 붕어빵은 정겹다.\n"
            "부동산 경매와는 무관하게 골목은 늘 붐빈다.\n"
            "요즘 뜨는 암호화폐와 다른 결의 재미가 여기 있다."
        )
        monkeypatch.setattr(b, "rewrite_draft", lambda *a, **k: repetitive)
        events = []
        r = b.generate_blog(
            "붕어빵 골목", on_stage=events.append, seo_keywords="암호화폐, 부동산 경매",
        )
        rw_done = next(
            e for e in events if e["stage"] == "rewrite" and e["state"] == "done"
        )
        assert rw_done["repetition_count"] == 3
        assert rw_done["repetition_warning"] is True
        assert "⚠️[자동 점검]" in r["critique_feedback"]
        assert "3회" in r["critique_feedback"]
        assert r["draft"] == repetitive

    def test_no_repetition_warning_when_rewrite_is_clean(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=False)
        monkeypatch.setattr(b, "rewrite_draft", lambda *a, **k: "# 깔끔한 재작성 본문")
        r = b.generate_blog("붕어빵 골목", seo_keywords="암호화폐, 부동산 경매")
        assert "⚠️[자동 점검]" not in (r["critique_feedback"] or "")

    def test_on_search_forwarded(self, monkeypatch):
        _stub_pipeline(monkeypatch)
        seen = []
        b.generate_blog("가을 축제", on_search=seen.append)
        assert seen == ["대전 가을 축제"]

    def test_reuse_disabled_skips_similar_lookup(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=[{"id": 1}])  # 있어도 무시돼야 한다
        r = b.generate_blog("성심당", reuse_existing=False)
        assert r["existing"] is False


class TestWeakResearch:
    def test_strong_research_no_low_grounding_constraint(self, monkeypatch):
        calls = _stub_pipeline(monkeypatch, research_grounded=True)
        r = b.generate_blog("성심당 빵집")
        assert calls["draft"][0]["low_grounding"] is False
        assert r["research_grounded"] is True
        assert r["aborted"] is False

    def test_option_b_proceeds_with_warning_and_constraint(self, monkeypatch):
        calls = _stub_pipeline(
            monkeypatch, research_grounded=False,
            research_reason="구체적 수치·상호가 확인되지 않음",
        )
        events = []
        r = b.generate_blog("겨울 붕어빵 골목", on_stage=events.append)

        # 초안은 만들어지되 '근거 부족' 제약이 걸린다
        assert calls["draft"][0]["low_grounding"] is True
        assert r["aborted"] is False
        assert r["draft"].startswith("# 초안")
        assert r["research_grounded"] is False
        assert r["research_reason"] == "구체적 수치·상호가 확인되지 않음"
        # 저장까지 진행
        assert calls["persist"] and calls["persist"][0]["research_grounded"] is False
        # grounding done 이벤트에 사유가 실린다
        g_done = next(e for e in events if e["stage"] == "grounding" and e["state"] == "done")
        assert g_done["grounded"] is False
        assert g_done["reason"] == "구체적 수치·상호가 확인되지 않음"

    def test_option_a_aborts_without_draft_or_save(self, monkeypatch):
        calls = _stub_pipeline(
            monkeypatch, research_grounded=False, research_reason="근거 없음",
        )
        events = []
        r = b.generate_blog(
            "겨울 붕어빵 골목", on_stage=events.append, abort_on_weak_research=True,
        )

        assert r["aborted"] is True
        assert r["existing"] is False
        assert r["draft"] == "" and r["final_content"] == "" and r["outline"] == ""
        assert r["post_id"] is None
        assert r["research_grounded"] is False
        assert r["research_reason"] == "근거 없음"
        assert set(r) == EXPECTED_KEYS
        # 초안/검토/저장 단계로 넘어가지 않는다
        assert calls["draft"] == [] and calls["persist"] == []
        stages = {e["stage"] for e in events}
        assert "draft" not in stages and "save" not in stages and "critique" not in stages

    def test_option_a_still_generates_when_research_is_grounded(self, monkeypatch):
        calls = _stub_pipeline(monkeypatch, research_grounded=True)
        r = b.generate_blog("성심당 빵집", abort_on_weak_research=True)
        assert r["aborted"] is False
        assert calls["draft"] and calls["persist"]


class TestExistingArticlePath:
    def _posts(self):
        return [
            {"id": 7, "topic": "성심당 명물", "final_content": "성심당 본문 성심당",
             "tone": "정보성", "seo_keywords": None, "image_url": "https://img/7.jpg",
             "critique_passed": False, "critique_feedback": "톤 불일치",
             "was_rewritten": True, "search_retried": True,
             "research_grounded": False, "research_reason": "근거 얕음",
             "created_at": "2026-09-01", "similarity": 0.93},
            {"id": 8, "topic": "대전 빵집 지도", "final_content": "다른 글",
             "tone": None, "seo_keywords": None, "image_url": None,
             "critique_passed": None, "critique_feedback": None,
             "was_rewritten": False, "search_retried": False,
             "research_grounded": None, "research_reason": None,
             "created_at": "2026-08-01", "similarity": 0.87},
        ]

    def test_returns_existing_without_running_pipeline(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=self._posts())
        called = []
        monkeypatch.setattr(
            b, "research",
            lambda *a, **k: called.append("research") or {
                "notes": "", "sources": [], "search_retried": False,
                "research_grounded": True, "research_reason": "",
            },
        )
        events = []
        r = b.generate_blog("성심당 빵집", on_stage=events.append, seo_keywords="성심당")

        assert called == []
        assert [(e["stage"], e["state"]) for e in events] == [
            ("similar", "start"), ("similar", "done"),
        ]
        assert r["existing"] is True
        assert r["aborted"] is False
        assert set(r) == EXPECTED_KEYS
        assert r["post_id"] == 7
        assert r["image_url"] == "https://img/7.jpg"
        assert r["critique_passed"] is False
        assert r["critique_feedback"] == "톤 불일치"
        assert r["was_rewritten"] is True
        assert r["search_retried"] is True
        # 최상위 유사 글의 근거 판정도 실려 온다
        assert r["research_grounded"] is False
        assert r["research_reason"] == "근거 얕음"

    def test_similar_done_event_carries_posts(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=self._posts())
        events = []
        b.generate_blog("성심당", on_stage=events.append)
        done = next(e for e in events if e["stage"] == "similar" and e["state"] == "done")
        assert len(done["posts"]) == 2
