"""
간단한 리서치 AI 에이전트 예제
- Claude API의 tool_use 기능으로 필요할 때 웹 검색을 수행하는 에이전트
- 검색 도구/루프는 blog_agent 와 공유하는 search_tools 모듈을 사용한다.

사전 준비:
  1) 가상환경 생성 및 패키지 설치
       python -m venv .venv
       .venv\\Scripts\\activate        (Windows)
       source .venv/bin/activate       (macOS / Linux)
       pip install -r requirements.txt
  2) 프로젝트 루트에 .env 파일을 만들고 API 키를 입력
       cp .env.example .env
       # .env 안의 ANTHROPIC_API_KEY 값을 실제 키로 변경

실행:
  python src/research_agent.py
"""

import os

from anthropic import Anthropic
from dotenv import load_dotenv

from search_tools import run_search_loop

# 프로젝트 루트(혹은 상위 경로)의 .env 파일에서 환경변수를 읽어온다.
load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = (
    "당신은 리서치를 도와주는 AI 에이전트입니다. "
    "질문에 답하기 위해 최신 정보나 사실 확인이 필요하면 반드시 web_search 도구를 사용하세요. "
    "추측으로 답하지 말고, 검색 결과에 근거해 답변하며 참고한 출처(URL)를 함께 밝히세요. "
    "충분한 정보를 확보했다고 판단되면 명확하고 간결한 최종 답변을 작성하세요."
)


def run_agent(user_question: str, max_turns: int = 5) -> str:
    """질문을 받아 tool-use 루프를 돌며 최종 답변을 반환"""
    result = run_search_loop(
        client,
        user_question,
        SYSTEM_PROMPT,
        max_turns=max_turns,
        max_tokens=1500,
    )
    return result["text"]


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY가 설정되어 있지 않습니다.\n"
            ".env.example을 .env로 복사한 뒤 실제 API 키를 입력하세요."
        )
        raise SystemExit(1)

    question = input("리서치하고 싶은 질문을 입력하세요: ")
    answer = run_agent(question)
    print("\n=== 최종 답변 ===")
    print(answer)
