"""images.get_topic_image 테스트 — Claude 호출과 Pexels HTTP 를 모두 모의한다.

키나 네트워크 없이 통과해야 한다.
"""

import pytest

import images


# --------------------------------------------------------------------------
# 모의 객체
# --------------------------------------------------------------------------
class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


class _FakeMessages:
    def __init__(self, reply=None, exc=None):
        self._reply = reply
        self._exc = exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return _Resp([_TextBlock(self._reply)])


class _FakeClient:
    def __init__(self, reply=None, exc=None):
        self.messages = _FakeMessages(reply, exc)


class _FakeHTTPResponse:
    def __init__(self, payload, raise_exc=None):
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        return self._payload


def _photo_payload(url="https://images.pexels.com/photo/123/x.jpg"):
    return {"photos": [{"src": {"landscape": url, "large": url}}]}


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(images, "PEXELS_API_KEY", "test-key")


# --------------------------------------------------------------------------
# _english_keywords
# --------------------------------------------------------------------------
class TestEnglishKeywords:
    def test_returns_lowercased_trimmed_keywords(self, monkeypatch):
        monkeypatch.setattr(images, "client", _FakeClient(reply='  "Korean Bakery Bread" '))
        assert images._english_keywords("대전 성심당 빵집 추천") == "korean bakery bread"

    def test_caps_at_four_words(self, monkeypatch):
        monkeypatch.setattr(images, "client", _FakeClient(reply="one two three four five six"))
        assert images._english_keywords("아무거나") == "one two three four"

    def test_returns_none_on_api_error(self, monkeypatch):
        monkeypatch.setattr(images, "client", _FakeClient(exc=RuntimeError("boom")))
        assert images._english_keywords("주제") is None

    def test_returns_none_when_reply_blank(self, monkeypatch):
        monkeypatch.setattr(images, "client", _FakeClient(reply="   "))
        assert images._english_keywords("주제") is None


# --------------------------------------------------------------------------
# _pexels_search
# --------------------------------------------------------------------------
class TestPexelsSearch:
    def test_returns_first_photo_url(self, monkeypatch, with_key):
        monkeypatch.setattr(
            images.httpx, "get",
            lambda *a, **k: _FakeHTTPResponse(_photo_payload("https://img/a.jpg")),
        )
        assert images._pexels_search("korean bakery") == "https://img/a.jpg"

    def test_none_when_no_photos(self, monkeypatch, with_key):
        monkeypatch.setattr(
            images.httpx, "get", lambda *a, **k: _FakeHTTPResponse({"photos": []})
        )
        assert images._pexels_search("nothing here") is None

    def test_none_on_http_error(self, monkeypatch, with_key):
        def _raise(*a, **k):
            raise images.httpx.ConnectError("no network")

        monkeypatch.setattr(images.httpx, "get", _raise)
        assert images._pexels_search("x") is None

    def test_none_on_non_2xx(self, monkeypatch, with_key):
        monkeypatch.setattr(
            images.httpx, "get",
            lambda *a, **k: _FakeHTTPResponse({}, raise_exc=RuntimeError("429")),
        )
        assert images._pexels_search("x") is None

    def test_sends_authorization_header(self, monkeypatch, with_key):
        captured = {}

        def _get(url, **kwargs):
            captured.update(kwargs)
            return _FakeHTTPResponse(_photo_payload())

        monkeypatch.setattr(images.httpx, "get", _get)
        images._pexels_search("korean bakery")
        assert captured["headers"]["Authorization"] == "test-key"
        assert captured["params"]["query"] == "korean bakery"


# --------------------------------------------------------------------------
# get_topic_image (통합)
# --------------------------------------------------------------------------
class TestGetTopicImage:
    def test_happy_path(self, monkeypatch, with_key):
        monkeypatch.setattr(images, "_english_keywords", lambda topic: "korean bakery bread")
        monkeypatch.setattr(
            images.httpx, "get",
            lambda *a, **k: _FakeHTTPResponse(_photo_payload("https://img/final.jpg")),
        )
        assert images.get_topic_image("대전 성심당 빵집 추천") == "https://img/final.jpg"

    def test_none_without_api_key(self, monkeypatch):
        monkeypatch.setattr(images, "PEXELS_API_KEY", None)
        # 키가 없으면 Claude/HTTP 를 아예 부르지 않아야 한다
        monkeypatch.setattr(images, "_english_keywords", lambda t: pytest.fail("호출되면 안 됨"))
        assert images.get_topic_image("대전 가을 축제") is None

    def test_none_for_blank_topic(self, with_key):
        assert images.get_topic_image("   ") is None
        assert images.get_topic_image("") is None

    def test_none_when_keyword_conversion_fails(self, monkeypatch, with_key):
        monkeypatch.setattr(images, "_english_keywords", lambda topic: None)
        monkeypatch.setattr(images.httpx, "get", lambda *a, **k: pytest.fail("호출되면 안 됨"))
        assert images.get_topic_image("대전 가을 축제") is None

    def test_none_when_pexels_returns_nothing(self, monkeypatch, with_key):
        monkeypatch.setattr(images, "_english_keywords", lambda topic: "autumn festival lights")
        monkeypatch.setattr(
            images.httpx, "get", lambda *a, **k: _FakeHTTPResponse({"photos": []})
        )
        assert images.get_topic_image("대전 가을 축제") is None
