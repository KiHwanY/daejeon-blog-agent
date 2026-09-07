"""blog_agent.critique_draft 테스트 — Claude 호출은 모의 처리한다."""

import blog_agent as b


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_TextBlock(text)]


class _FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self.reply)


class _FakeClient:
    def __init__(self, reply):
        self.messages = _FakeMessages(reply)


def _use(monkeypatch, reply):
    fake = _FakeClient(reply)
    monkeypatch.setattr(b, "client", fake)
    return fake


DRAFT = "# 성심당 이야기\n대전의 성심당은 ..."
CONTEXT = "- 성심당은 1956년 창업 (출처: https://daejeon.go.kr/x)"


class TestCritiqueDraft:
    def test_pass_true(self, monkeypatch):
        _use(monkeypatch, '{"pass": true, "feedback": "근거와 일치하고 톤도 맞음"}')
        out = b.critique_draft(DRAFT, CONTEXT, tone="정보성", seo_keywords="성심당")
        assert out == {"pass": True, "feedback": "근거와 일치하고 톤도 맞음"}

    def test_pass_false_with_feedback(self, monkeypatch):
        _use(monkeypatch, '{"pass": false, "feedback": "SEO 키워드가 본문에 전혀 없음"}')
        out = b.critique_draft(DRAFT, CONTEXT, tone="리뷰형", seo_keywords="성심당, 튀김소보로")
        assert out["pass"] is False
        assert "SEO" in out["feedback"]

    def test_parses_fenced_json(self, monkeypatch):
        _use(monkeypatch, '```json\n{"pass": false, "feedback": "구조가 단조로움"}\n```')
        out = b.critique_draft(DRAFT, CONTEXT)
        assert out["pass"] is False
        assert out["feedback"] == "구조가 단조로움"

    def test_unparseable_reply_defaults_to_pass(self, monkeypatch):
        _use(monkeypatch, "죄송하지만 평가할 수 없습니다.")
        out = b.critique_draft(DRAFT, CONTEXT)
        assert out["pass"] is True
        assert "해석하지 못" in out["feedback"]

    def test_missing_pass_key_defaults_to_pass(self, monkeypatch):
        _use(monkeypatch, '{"feedback": "pass 키 없음"}')
        out = b.critique_draft(DRAFT, CONTEXT)
        assert out["pass"] is True

    def test_prompt_includes_draft_tone_and_keywords(self, monkeypatch):
        fake = _use(monkeypatch, '{"pass": true, "feedback": "ok"}')
        b.critique_draft(DRAFT, CONTEXT, tone="전문적", seo_keywords="성심당, 대전 빵집")
        sent = fake.messages.calls[0]
        user_msg = sent["messages"][0]["content"]
        assert DRAFT in user_msg
        assert CONTEXT in user_msg
        assert "전문적" in user_msg
        assert "성심당" in user_msg and "대전 빵집" in user_msg
