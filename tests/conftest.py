"""pytest 공통 설정 — src/ 를 import 경로에 추가한다.

(pyproject.toml 의 pythonpath 설정과 중복되지만, IDE 등에서 conftest 만
로드되는 경우에도 동작하도록 함께 둔다.)
"""

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
