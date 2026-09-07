"""generate_blog 파이프라인 오케스트레이션 테스트 (LLM/DB 는 모두 모의)."""

import blog_agent as b


def _stub_pipeline(
    monkeypatch, *, similar=None, post_id=99, critique_pass=True,
    search_retried=False,
):
    """research/outline/draft/critique/rewrite/persist/유사조회를 결정적 스텁으로 교체."""
    monkeypatch.setattr(b, "find_similar_posts", lambda *a, **k: list(similar or []))
    monkeypatch.setattr(
        b, "research",
        lambda topic, on_search=None: (
            on_search("대전 " + topic) if on_search else None,
            {
                "notes": "리서치 노트",
                "sources": [{"url": "https://daejeon.go.kr/x", "trusted": True}],
                "search_retried": search_retried,
            },
        )[1],
    )
    monkeypatch.setattr(b, "make_outline", lambda topic, notes: "## 아웃라인")
    monkeypatch.setattr(
        b, "write_draft",
        lambda topic, outline, notes, tone="정보성", seo_keywords=None: (
            f"# 초안 ({tone}) 성심당 성심당 성심당"
        ),
    )
    monkeypatch.setattr(
        b, "critique_draft",
        lambda draft, ctx, tone="정보성", seo_keywords=None: {
            "pass": critique_pass,
            "feedback": "좋음" if critique_pass else "톤이 요청과 다르고 SEO 과다",
        },
    )
    monkeypatch.setattr(
        b, "rewrite_draft",
        lambda *a, **k: "# 재작성 초안 성심당 성심당 성심당",
    )
    monkeypatch.setattr(b, "persist_blog", lambda *a, **k: post_id)
    monkeypatch.setattr(b, "get_topic_image", lambda topic: "https://img.example/x.jpg")


EXPECTED_KEYS = {
    "existing", "similar_posts", "post_id", "similarity", "topic", "tone",
    "seo_keywords", "seo_report", "research", "sources", "outline", "draft",
    "final_content", "image_url", "search_retried", "critique_passed",
    "critique_feedback", "was_rewritten",
}


class TestNewArticlePath:
    def test_stage_events_when_critique_passes(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=True)
        events = []
        b.generate_blog("성심당 빵집", on_stage=events.append, tone="리뷰형")
        assert [(e["stage"], e["state"]) for e in events] == [
            ("similar", "start"), ("similar", "done"),
            ("research", "start"), ("research", "done"),
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
        # rewrite 는 critique 다음, image 이전에 정확히 한 번
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
        assert r["post_id"] == 123
        assert r["seo_report"] == {"성심당": 3, "없는키워드": 0}
        assert r["image_url"] == "https://img.example/x.jpg"
        assert r["search_retried"] is True
        assert r["critique_passed"] is True
        assert r["critique_feedback"] == "좋음"
        assert r["was_rewritten"] is False

    def test_critique_failure_recorded_in_result(self, monkeypatch):
        _stub_pipeline(monkeypatch, critique_pass=False)
        r = b.generate_blog("성심당 빵집")
        assert r["critique_passed"] is False
        assert "SEO" in r["critique_feedback"]
        assert r["was_rewritten"] is True

    def test_on_search_forwarded(self, monkeypatch):
        _stub_pipeline(monkeypatch)
        seen = []
        b.generate_blog("가을 축제", on_search=seen.append)
        assert seen == ["대전 가을 축제"]

    def test_reuse_disabled_skips_similar_lookup(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=[{"id": 1}])  # 있어도 무시돼야 한다
        r = b.generate_blog("성심당", reuse_existing=False)
        assert r["existing"] is False


class TestExistingArticlePath:
    def _posts(self):
        return [
            {"id": 7, "topic": "성심당 명물", "final_content": "성심당 본문 성심당",
             "tone": "정보성", "seo_keywords": None, "image_url": "https://img/7.jpg",
             "critique_passed": False, "critique_feedback": "톤 불일치",
             "was_rewritten": True, "search_retried": True,
             "created_at": "2026-09-01", "similarity": 0.93},
            {"id": 8, "topic": "대전 빵집 지도", "final_content": "다른 글",
             "tone": None, "seo_keywords": None, "image_url": None,
             "critique_passed": None, "critique_feedback": None,
             "was_rewritten": False, "search_retried": False,
             "created_at": "2026-08-01", "similarity": 0.87},
        ]

    def test_returns_existing_without_running_pipeline(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=self._posts())
        called = []
        monkeypatch.setattr(
            b, "research",
            lambda *a, **k: called.append("research") or {
                "notes": "", "sources": [], "search_retried": False
            },
        )
        events = []
        r = b.generate_blog("성심당 빵집", on_stage=events.append, seo_keywords="성심당")

        assert called == []  # 리서치가 실행되지 않았다
        assert [(e["stage"], e["state"]) for e in events] == [
            ("similar", "start"), ("similar", "done"),
        ]
        assert r["existing"] is True
        assert set(r) == EXPECTED_KEYS
        assert r["post_id"] == 7
        assert r["image_url"] == "https://img/7.jpg"
        # 최상위 유사 글의 자체검토 메타가 그대로 실려 온다
        assert r["critique_passed"] is False
        assert r["critique_feedback"] == "톤 불일치"
        assert r["was_rewritten"] is True
        assert r["search_retried"] is True

    def test_similar_done_event_carries_posts(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=self._posts())
        events = []
        b.generate_blog("성심당", on_stage=events.append)
        done = next(e for e in events if e["stage"] == "similar" and e["state"] == "done")
        assert len(done["posts"]) == 2
