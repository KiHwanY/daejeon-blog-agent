"""generate_blog 파이프라인 오케스트레이션 테스트 (LLM/DB 는 모두 모의)."""

import blog_agent as b


def _stub_pipeline(monkeypatch, *, similar=None, post_id=99):
    """research/outline/draft/persist/유사조회를 결정적 스텁으로 교체."""
    monkeypatch.setattr(b, "find_similar_posts", lambda *a, **k: list(similar or []))
    monkeypatch.setattr(
        b, "research",
        lambda topic, on_search=None: (
            on_search("대전 " + topic) if on_search else None,
            ("리서치 노트", [{"url": "https://daejeon.go.kr/x", "trusted": True}]),
        )[1],
    )
    monkeypatch.setattr(b, "make_outline", lambda topic, notes: "## 아웃라인")
    monkeypatch.setattr(
        b, "write_draft",
        lambda topic, outline, notes, tone="정보성", seo_keywords=None: (
            f"# 초안 ({tone}) 성심당 성심당 성심당"
        ),
    )
    monkeypatch.setattr(b, "persist_blog", lambda *a, **k: post_id)


EXPECTED_KEYS = {
    "existing", "similar_posts", "post_id", "similarity", "topic", "tone",
    "seo_keywords", "seo_report", "research", "sources", "outline", "draft",
    "final_content",
}


class TestNewArticlePath:
    def test_stage_events_in_order(self, monkeypatch):
        _stub_pipeline(monkeypatch)
        events = []
        b.generate_blog("성심당 빵집", on_stage=events.append, tone="리뷰형")
        assert [(e["stage"], e["state"]) for e in events] == [
            ("similar", "start"), ("similar", "done"),
            ("research", "start"), ("research", "done"),
            ("outline", "start"), ("outline", "done"),
            ("draft", "start"), ("draft", "done"),
            ("save", "start"), ("save", "done"),
        ]
        # draft start 이벤트에 tone 이 실린다
        draft_start = next(e for e in events if e["stage"] == "draft" and e["state"] == "start")
        assert draft_start["tone"] == "리뷰형"

    def test_result_shape_and_values(self, monkeypatch):
        _stub_pipeline(monkeypatch, post_id=123)
        r = b.generate_blog("성심당 빵집", tone="정보성", seo_keywords="성심당, 없는키워드")
        assert set(r) == EXPECTED_KEYS
        assert r["existing"] is False
        assert r["post_id"] == 123
        assert r["similar_posts"] == []
        assert r["final_content"] == r["draft"]
        assert r["seo_report"] == {"성심당": 3, "없는키워드": 0}
        assert r["tone"] == "정보성"

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
             "tone": "정보성", "seo_keywords": None, "created_at": "2026-09-01",
             "similarity": 0.93},
            {"id": 8, "topic": "대전 빵집 지도", "final_content": "다른 글",
             "tone": None, "seo_keywords": None, "created_at": "2026-08-01",
             "similarity": 0.87},
        ]

    def test_returns_existing_without_running_pipeline(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=self._posts())
        called = []
        monkeypatch.setattr(b, "research", lambda *a, **k: called.append("research") or ("", []))
        events = []
        r = b.generate_blog("성심당 빵집", on_stage=events.append, seo_keywords="성심당")

        assert called == []  # 리서치가 실행되지 않았다
        assert [(e["stage"], e["state"]) for e in events] == [
            ("similar", "start"), ("similar", "done"),
        ]
        assert r["existing"] is True
        assert set(r) == EXPECTED_KEYS
        assert len(r["similar_posts"]) == 2
        assert r["post_id"] == 7
        assert r["similarity"] == 0.93
        assert r["topic"] == "성심당 명물"
        assert r["final_content"] == "성심당 본문 성심당"
        assert r["seo_report"] == {"성심당": 2}

    def test_similar_done_event_carries_posts(self, monkeypatch):
        _stub_pipeline(monkeypatch, similar=self._posts())
        events = []
        b.generate_blog("성심당", on_stage=events.append)
        done = next(e for e in events if e["stage"] == "similar" and e["state"] == "done")
        assert len(done["posts"]) == 2
