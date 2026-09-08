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


class TestRewriteDraftFallback:
    def test_returns_previous_draft_when_reply_empty(self, monkeypatch):
        _use(monkeypatch, "")  # 텍스트 블록이 비어 있는 응답
        out = b.rewrite_draft(
            "붕어빵 골목", "## 아웃라인", "리서치", "이전 초안 본문",
            "피드백", tone="전문적", seo_keywords="암호화폐",
        )
        assert out == "이전 초안 본문"

    def test_uses_rewritten_text_when_present(self, monkeypatch):
        _use(monkeypatch, "새로 쓴 본문")
        out = b.rewrite_draft(
            "붕어빵 골목", "## 아웃라인", "리서치", "이전 초안 본문", "피드백",
        )
        assert out == "새로 쓴 본문"


class TestCheckContrastRepetition:
    KW = "암호화폐, 부동산 경매"

    def _repetitive_text(self):
        return (
            "붕어빵 골목은 겨울마다 붐빈다.\n"
            "암호화폐와 달리 붕어빵은 손에 잡히는 따뜻함을 준다.\n"
            "부동산 경매와는 무관하게 이 골목의 가격은 몇 년째 그대로다.\n"
            "요즘 뜨는 암호화폐와 다른 결의 재미가 여기 있다.\n"
            "결국 부동산 경매와 대비되는 소박함이 이 골목의 매력이다."
        )

    def test_flags_when_pattern_repeats(self):
        out = b.check_contrast_repetition(self._repetitive_text(), self.KW)
        assert out["count"] == 4
        assert out["repetitive"] is True
        assert len(out["examples"]) == 3  # 예시는 최대 3개

    def test_not_flagged_below_threshold(self):
        text = (
            "붕어빵 골목 이야기.\n"
            "암호화폐와 달리 붕어빵은 정겹다.\n"
            "부동산 경매와는 무관하게 골목은 늘 붐빈다."
        )
        out = b.check_contrast_repetition(text, self.KW)
        assert out["count"] == 2
        assert out["repetitive"] is False

    def test_contrast_without_keyword_not_counted(self):
        text = (
            "작년과 달리 올해는 눈이 많다.\n"
            "예년과는 무관하게 상인분들은 일찍 문을 연다.\n"
            "지난겨울과 다른 결의 분위기다."
        )
        out = b.check_contrast_repetition(text, self.KW)
        assert out["count"] == 0
        assert out["repetitive"] is False

    def test_keyword_without_contrast_not_counted(self):
        text = (
            "암호화폐 시세가 출렁인다.\n"
            "부동산 경매 절차는 복잡하다.\n"
            "암호화폐 투자자도 붕어빵은 좋아한다."
        )
        out = b.check_contrast_repetition(text, self.KW)
        assert out["count"] == 0

    def test_no_keywords_returns_zero(self):
        out = b.check_contrast_repetition("암호화폐와 달리 붕어빵은 정겹다.", "")
        assert out == {"count": 0, "repetitive": False, "examples": []}

    def test_threshold_is_configurable(self):
        out = b.check_contrast_repetition(self._repetitive_text(), self.KW, threshold=5)
        assert out["count"] == 4
        assert out["repetitive"] is False

    def test_handles_none_text(self):
        out = b.check_contrast_repetition(None, self.KW)
        assert out["count"] == 0 and out["repetitive"] is False

    def test_list_keywords_accepted(self):
        out = b.check_contrast_repetition(
            self._repetitive_text(), ["암호화폐", "부동산 경매"]
        )
        assert out["repetitive"] is True
