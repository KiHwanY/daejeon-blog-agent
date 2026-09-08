"""search_tools 의 신뢰 소스 판별 · 결과 포매팅 · tool-use 루프 테스트."""

import pytest

import search_tools as stt

TRUSTED = (("daejeon.go.kr", "대전광역시청"), ("daejonilbo.com", "대전일보"))


@pytest.fixture(autouse=True)
def _stub_trusted_sources(monkeypatch):
    """DB 접근 없이 고정된 신뢰 소스 목록을 쓰도록 교체."""
    monkeypatch.setattr(stt, "_trusted_sources", lambda: TRUSTED)


# --------------------------------------------------------------------------
# match_trusted
# --------------------------------------------------------------------------
class TestMatchTrusted:
    def test_exact_domain(self):
        assert stt.match_trusted("https://daejeon.go.kr/board/1") == "대전광역시청"

    def test_www_prefix_stripped(self):
        assert stt.match_trusted("http://www.daejonilbo.com/news") == "대전일보"

    def test_subdomain_matches(self):
        assert stt.match_trusted("https://tour.daejeon.go.kr/x") == "대전광역시청"

    def test_port_and_userinfo_ignored(self):
        assert stt.match_trusted("https://user@daejeon.go.kr:8443/p") == "대전광역시청"

    def test_lookalike_domain_does_not_match(self):
        assert stt.match_trusted("https://notdaejeon.go.kr/x") is None
        assert stt.match_trusted("https://daejeon.go.kr.evil.com/x") is None

    def test_domain_in_path_does_not_match(self):
        assert stt.match_trusted("https://example.com/daejeon.go.kr") is None

    def test_garbage_input(self):
        assert stt.match_trusted("") is None
        assert stt.match_trusted("not a url") is None


# --------------------------------------------------------------------------
# annotate_trust
# --------------------------------------------------------------------------
def test_annotate_trust_adds_flags():
    hits = [
        {"title": "A", "url": "https://example.com/a", "snippet": "s"},
        {"title": "B", "url": "https://www.daejeon.go.kr/b", "snippet": "s"},
    ]
    out = stt.annotate_trust(hits)
    assert out[0]["trusted"] is False and out[0]["source_name"] is None
    assert out[1]["trusted"] is True and out[1]["source_name"] == "대전광역시청"
    # 원본 필드는 보존
    assert out[1]["title"] == "B"


# --------------------------------------------------------------------------
# format_hits
# --------------------------------------------------------------------------
class TestFormatHits:
    def test_empty(self):
        assert stt.format_hits([]) == "검색 결과가 없습니다."

    def test_trusted_sorted_first_and_tagged(self):
        hits = stt.annotate_trust([
            {"title": "일반", "url": "https://example.com/x", "snippet": "s1"},
            {"title": "관공서", "url": "https://daejeon.go.kr/y", "snippet": "s2"},
        ])
        text = stt.format_hits(hits)
        lines = text.splitlines()
        # 신뢰 소스가 먼저, 태그가 붙는다
        assert lines[0].startswith("- 제목: 관공서 [신뢰 소스: 대전광역시청]")
        assert "일반" in text and "[신뢰 소스" in lines[0]


# --------------------------------------------------------------------------
# last_text
# --------------------------------------------------------------------------
class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class TestLastText:
    def test_returns_last_nonempty_text_block(self):
        resp = _Resp([
            _Block(type="text", text="첫 번째"),
            _Block(type="tool_use", name="web_search"),
            _Block(type="text", text="마지막 텍스트"),
        ])
        assert stt.last_text(resp) == "마지막 텍스트"

    def test_ignores_whitespace_only(self):
        resp = _Resp([_Block(type="text", text="실제 답"), _Block(type="text", text="   ")])
        assert stt.last_text(resp) == "실제 답"

    def test_no_text_block(self):
        assert stt.last_text(_Resp([_Block(type="tool_use", name="web_search")])) == ""


# --------------------------------------------------------------------------
# run_search_loop
# --------------------------------------------------------------------------
class _FakeMessages:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._script.pop(0)


class _FakeClient:
    def __init__(self, script):
        self.messages = _FakeMessages(script)


def test_run_search_loop_runs_tool_then_returns_final(monkeypatch):
    monkeypatch.setattr(
        stt, "web_search",
        lambda q, **k: [
            {"title": "T", "url": "https://daejeon.go.kr/a", "snippet": "s",
             "trusted": True, "source_name": "대전광역시청"},
        ],
    )
    script = [
        _Resp(
            [
                _Block(type="text", text="검색하겠습니다"),
                _Block(type="tool_use", name="web_search", id="t1",
                       input={"query": "성심당"}),
            ],
            stop_reason="tool_use",
        ),
        _Resp([_Block(type="text", text="최종 답변")], stop_reason="end_turn"),
    ]
    client = _FakeClient(script)
    seen_queries = []

    res = stt.run_search_loop(
        client, "질문", "시스템",
        on_search=seen_queries.append,
        query_transform=lambda q: f"대전 {q}",
    )

    assert res["text"] == "최종 답변"
    assert res["search_retried"] is False
    assert seen_queries == ["대전 성심당"]           # query_transform 적용됨
    assert [s["url"] for s in res["sources"]] == ["https://daejeon.go.kr/a"]
    assert client.messages.calls[0]["tools"] is stt.TOOLS


def test_run_search_loop_stops_at_max_turns(monkeypatch):
    monkeypatch.setattr(stt, "web_search", lambda q, **k: [])
    always_tool = _Resp(
        [_Block(type="tool_use", name="web_search", id="x", input={"query": "q"})],
        stop_reason="tool_use",
    )
    client = _FakeClient([always_tool] * 10)
    res = stt.run_search_loop(client, "q", "sys", max_turns=3)
    assert len(client.messages.calls) == 3
    assert res["text"] == "" and res["sources"] == []


# --------------------------------------------------------------------------
# extract_json
# --------------------------------------------------------------------------
class TestExtractJson:
    def test_plain_object(self):
        assert stt.extract_json('{"pass": true, "feedback": "ok"}') == {
            "pass": True, "feedback": "ok"
        }

    def test_fenced_json_block(self):
        text = '설명\n```json\n{"keywords": "대전 축제 일정"}\n```\n끝'
        assert stt.extract_json(text) == {"keywords": "대전 축제 일정"}

    def test_object_embedded_in_prose(self):
        assert stt.extract_json('결과: {"a": 1} 입니다') == {"a": 1}

    def test_returns_none_for_junk(self):
        assert stt.extract_json("JSON 아님") is None
        assert stt.extract_json("") is None
        assert stt.extract_json("[1, 2, 3]") is None  # dict 가 아니면 None


# --------------------------------------------------------------------------
# run_search_loop — 결과 부족 시 1회 재시도
# --------------------------------------------------------------------------
def _tool_then_stop(query="성심당"):
    return [
        _Resp(
            [_Block(type="tool_use", name="web_search", id="t1",
                    input={"query": query})],
            stop_reason="tool_use",
        ),
        _Resp([_Block(type="text", text="정리 끝")], stop_reason="end_turn"),
    ]


def _hit(url):
    return {"title": "t", "url": url, "snippet": "s", "trusted": False,
            "source_name": None}


def test_retry_on_thin_results_asks_and_researches_again(monkeypatch):
    calls = {"queries": []}

    def fake_search(q, **k):
        calls["queries"].append(q)
        # 첫 검색은 1건(부족), 재시도 검색은 다른 1건
        return [_hit("https://a/1")] if len(calls["queries"]) == 1 else [_hit("https://b/2")]

    monkeypatch.setattr(stt, "web_search", fake_search)
    script = _tool_then_stop() + [
        _Resp([_Block(type="text",
                      text='{"reason":"키워드가 좁았음","keywords":"성심당 대표 메뉴"}')]),
    ]
    client = _FakeClient(script)

    res = stt.run_search_loop(client, "성심당 소개글", "sys", retry_on_thin=True)

    assert res["search_retried"] is True
    assert calls["queries"] == ["성심당", "성심당 대표 메뉴"]  # 재시도 검색어 사용
    assert {s["url"] for s in res["sources"]} == {"https://a/1", "https://b/2"}


def test_no_retry_when_enough_results(monkeypatch):
    monkeypatch.setattr(
        stt, "web_search",
        lambda q, **k: [_hit("https://a/1"), _hit("https://a/2"), _hit("https://a/3")],
    )
    client = _FakeClient(_tool_then_stop())
    res = stt.run_search_loop(client, "q", "sys", retry_on_thin=True)
    assert res["search_retried"] is False
    assert len(client.messages.calls) == 2  # 재시도 제안 호출 없음


def test_no_retry_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(stt, "web_search", lambda q, **k: [_hit("https://a/1")])
    client = _FakeClient(_tool_then_stop())
    res = stt.run_search_loop(client, "q", "sys")  # retry_on_thin 기본 False
    assert res["search_retried"] is False
    assert len(client.messages.calls) == 2


def test_retry_happens_at_most_once(monkeypatch):
    monkeypatch.setattr(stt, "web_search", lambda q, **k: [_hit("https://same/1")])
    script = _tool_then_stop() + [
        _Resp([_Block(type="text", text='{"keywords":"또 다른 검색어"}')]),
        _Resp([_Block(type="text", text='{"keywords":"세 번째 검색어"}')]),
    ]
    client = _FakeClient(script)
    res = stt.run_search_loop(client, "q", "sys", retry_on_thin=True)
    assert res["search_retried"] is True
    # create 호출: tool_use + end_turn + 재시도 제안 1회 = 3 (그 이상 아님)
    assert len(client.messages.calls) == 3


def test_no_retry_when_suggestion_has_no_keywords(monkeypatch):
    monkeypatch.setattr(stt, "web_search", lambda q, **k: [_hit("https://a/1")])
    script = _tool_then_stop() + [
        _Resp([_Block(type="text", text='{"reason":"모르겠음"}')]),  # keywords 없음
    ]
    client = _FakeClient(script)
    res = stt.run_search_loop(client, "q", "sys", retry_on_thin=True)
    assert res["search_retried"] is False
