"""
간단한 리서치 AI 에이전트 예제
- Claude API의 tool_use 기능으로 필요할 때 웹 검색을 수행하는 에이전트

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
from ddgs import DDGS
from dotenv import load_dotenv

# 프로젝트 루트(혹은 상위 경로)의 .env 파일에서 환경변수를 읽어온다.
load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "당신은 리서치를 도와주는 AI 에이전트입니다. "
    "질문에 답하기 위해 최신 정보나 사실 확인이 필요하면 반드시 web_search 도구를 사용하세요. "
    "추측으로 답하지 말고, 검색 결과에 근거해 답변하며 참고한 출처(URL)를 함께 밝히세요. "
    "충분한 정보를 확보했다고 판단되면 명확하고 간결한 최종 답변을 작성하세요."
)

# 1) 에이전트가 사용할 도구 정의 (스키마)
TOOLS = [
    {
        "name": "web_search",
        "description": (
            "웹에서 최신 정보, 뉴스, 통계, 특정 주제에 대한 사실을 검색합니다. "
            "모르는 내용이나 최신성이 중요한 질문에는 반드시 이 도구를 사용하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색할 키워드. 짧고 구체적으로 작성하세요.",
                }
            },
            "required": ["query"],
        },
    }
]


def web_search(query: str, max_results: int = 5) -> str:
    """DuckDuckGo로 웹 검색을 실행하고 결과를 텍스트로 정리해서 반환"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"검색 중 오류가 발생했습니다: {e}"

    if not results:
        return "검색 결과가 없습니다."

    lines = []
    for r in results:
        lines.append(
            f"- 제목: {r.get('title')}\n  URL: {r.get('href')}\n  요약: {r.get('body')}"
        )
    return "\n".join(lines)


def run_agent(user_question: str, max_turns: int = 5) -> str:
    """질문을 받아 tool-use 루프를 돌며 최종 답변을 반환"""
    messages = [{"role": "user", "content": user_question}]
    final_answer = ""

    for turn in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # 이번 응답에 포함된 텍스트를 모아둔다
        for block in response.content:
            if block.type == "text" and block.text.strip():
                final_answer = block.text

        # 더 이상 도구 호출이 필요 없으면 종료
        if response.stop_reason != "tool_use":
            break

        # 어시스턴트의 이번 턴(도구 요청 포함)을 대화 기록에 추가
        messages.append({"role": "assistant", "content": response.content})

        # 요청된 도구들을 실제로 실행하고 결과를 모은다
        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "web_search":
                query = block.input["query"]
                print(f"[검색 실행] {query}")
                result_text = web_search(query)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    return final_answer


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