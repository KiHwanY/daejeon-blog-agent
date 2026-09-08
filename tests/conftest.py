"""pytest 공통 설정 — src/ 를 import 경로에 추가한다.

(pyproject.toml 의 pythonpath 설정과 중복되지만, IDE 등에서 conftest 만
로드되는 경우에도 동작하도록 함께 둔다.)
"""

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402  (경로 추가 후 import)


@pytest.fixture(autouse=True)
def _stub_keyword_lists(monkeypatch):
    """keyword_lists 를 DB 없이 시드값으로 채운 것처럼 만든다.

    `blog_agent._keyword_list` 는 원래 keyword_lists 테이블을 조회한다.
    테스트에는 DB가 없으므로 db.KEYWORD_LIST_SEEDS 로 대체(= init_db 가
    시딩하는 값과 동일).
    """
    import blog_agent
    import db

    monkeypatch.setattr(
        blog_agent, "_keyword_list",
        lambda name: tuple(db.KEYWORD_LIST_SEEDS.get(name, ())),
    )
