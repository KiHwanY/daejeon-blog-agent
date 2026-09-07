"""
대전 지역 블로그 생성기 - Streamlit 프론트엔드

실행:
  streamlit run app.py
"""

import os
import sys

import streamlit as st

# src/ 를 import 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import blog_agent  # noqa: E402  (경로 추가 후 import)

st.set_page_config(page_title="대전 블로그 생성기", page_icon="📝")

st.title("📝 대전 지역 블로그 생성기")
st.caption(
    "리서치 → 아웃라인 → 초안 순으로 블로그 글을 만들어 줍니다. "
    "지역 정보가 필요한 주제는 자동으로 **대전**을 맥락으로 삼고, "
    "이미 비슷한 글이 있으면 기존 글을 보여 줍니다."
)

# 이전 실행 결과를 담는 세션 키
RESULT_KEYS = (
    "topic", "mode", "similarity", "existing_topic", "final_content",
    "research", "sources", "outline", "draft", "post_id",
)

topic = st.text_input("블로그 주제", value="대전 가을 축제")
run = st.button("블로그 생성", type="primary")

if run and topic.strip():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY가 없습니다. 프로젝트 루트의 .env 파일을 확인하세요.")
        st.stop()

    for key in RESULT_KEYS:
        st.session_state.pop(key, None)

    t = topic.strip()
    st.session_state["topic"] = t

    # --- 0단계: 임베딩 유사도로 기존 글 확인 ---
    with st.spinner("기존 글 확인 중 (임베딩 유사도 검색)..."):
        found = blog_agent.find_similar_post(t)

    if found:
        st.session_state["mode"] = "existing"
        st.session_state["similarity"] = found["similarity"]
        st.session_state["existing_topic"] = found["topic"]
        st.session_state["final_content"] = found["final_content"]
    else:
        st.session_state["mode"] = "new"

        # --- 1단계: 리서치 ---
        with st.status("1/3 · 리서치 중 (웹 검색)...", expanded=True) as status:
            searched: list[str] = []

            def _on_search(q: str) -> None:
                searched.append(q)
                st.write(f"🔎 검색: `{q}`")

            notes, sources = blog_agent.research(t, on_search=_on_search)
            st.session_state["research"] = notes
            st.session_state["sources"] = sources
            status.update(label="1/3 · 리서치 완료", state="complete", expanded=False)

        # --- 2단계: 아웃라인 ---
        with st.status("2/3 · 아웃라인 작성 중...", expanded=False) as status:
            outline = blog_agent.make_outline(t, notes)
            st.session_state["outline"] = outline
            status.update(label="2/3 · 아웃라인 완료", state="complete")

        # --- 3단계: 초안 ---
        with st.status("3/3 · 블로그 초안 작성 중...", expanded=False) as status:
            draft = blog_agent.write_draft(t, outline, notes)
            st.session_state["draft"] = draft
            st.session_state["final_content"] = draft
            status.update(label="3/3 · 초안 완료", state="complete")

        # --- 4단계: 임베딩 생성 + DB 저장 ---
        with st.status("저장 중 (임베딩 생성 + DB 저장)...", expanded=False) as status:
            post_id = blog_agent.persist_blog(t, outline, draft)
            st.session_state["post_id"] = post_id
            status.update(label=f"blog_posts #{post_id} 저장 완료", state="complete")


# --- 결과 표시 (생성 직후 & 재실행 시 모두) ---
mode = st.session_state.get("mode")
topic_done = st.session_state.get("topic", "blog")

if mode == "existing":
    st.divider()
    st.warning(
        f"이미 비슷한 글이 있습니다 (유사도: {st.session_state['similarity']:.2f})"
    )
    st.caption(
        f"기존 글 주제: **{st.session_state['existing_topic']}**  ·  "
        f"입력한 주제: {topic_done}"
    )
    st.subheader("기존 글")
    st.markdown(st.session_state["final_content"])
    st.download_button(
        "📥 .md 파일로 다운로드",
        data=st.session_state["final_content"],
        file_name=f"{blog_agent.slugify(topic_done)}.md",
        mime="text/markdown",
    )

elif mode == "new":
    st.divider()
    st.success(
        f"새 글을 생성했습니다. (blog_posts #{st.session_state.get('post_id')} 저장됨)"
    )

    st.subheader("1. 리서치 결과")
    st.markdown(st.session_state["research"])

    sources = st.session_state.get("sources", [])
    if sources:
        st.markdown("**검색된 출처**")
        for s in sources:
            label = s.get("title") or s.get("url")
            if s.get("url"):
                st.markdown(f"- [{label}]({s['url']})")

    st.subheader("2. 아웃라인")
    st.markdown(st.session_state["outline"])

    st.subheader("3. 블로그 초안")
    st.markdown(st.session_state["draft"])

    st.download_button(
        "📥 .md 파일로 다운로드",
        data=st.session_state["draft"],
        file_name=f"{blog_agent.slugify(topic_done)}.md",
        mime="text/markdown",
    )
