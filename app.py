"""
대전 지역 블로그 생성기 - Streamlit 프론트엔드

실행:
  streamlit run app.py
"""

import hashlib
import html as html_lib
import os
import re
import sys
from contextlib import contextmanager

import anthropic
import psycopg2
import streamlit as st

# src/ 를 import 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import blog_agent  # noqa: E402  (경로 추가 후 import)

st.set_page_config(page_title="대전 블로그 생성기", page_icon="📝", layout="wide")

st.title("📝 대전 지역 블로그 생성기")
st.caption(
    "리서치 → 아웃라인 → 초안 순으로 블로그 글을 만들어 줍니다. "
    "지역 정보가 필요한 주제는 자동으로 **대전**을 맥락으로 삼고, "
    "결과는 카드로 보여 줍니다. 카드를 클릭하면 상세 내용이 모달로 열립니다."
)

# 이전 실행 결과를 담는 세션 키
RESULT_KEYS = (
    "topic", "mode", "cards", "grounding_src", "open_card", "similar_posts",
    "final_content", "research", "sources", "outline", "draft", "post_id",
    "tone", "seo_keywords", "seo_report", "image_url",
    "search_retried", "critique_passed", "critique_feedback", "was_rewritten",
    "research_grounded", "research_reason",
)

TONE_OPTIONS = ["정보성", "캐주얼", "리뷰형", "전문적"]

# 카드 그리드 + 전체폭 레이아웃
CARD_CSS = """
<style>
/* 본문을 화면에 꽉 차게 */
.block-container { padding: 1.4rem 3rem 4rem; max-width: 100%; }

/* 카드 (이미지 + 본문) */
.post-card {
  border: 1px solid rgba(128,128,128,.28);
  border-radius: 16px 16px 0 0;
  border-bottom: 0;
  overflow: hidden;
  height: 300px;
  display: flex;
  flex-direction: column;
  transition: box-shadow .15s ease, transform .15s ease;
}
.post-card-img {
  flex: 0 0 64%;
  background-size: cover;
  background-position: center;
  background-color: rgba(128,128,128,.15);
}
.post-card-body {
  flex: 1;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow: hidden;
}
.post-card-title {
  font-weight: 700; font-size: 1rem; line-height: 1.3;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.post-card-text {
  font-size: .8rem; line-height: 1.4; opacity: .72; flex: 1;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.post-card-foot {
  display: flex; justify-content: space-between; align-items: center;
  font-size: .78rem;
}
.post-card-sub { opacity: .55; }
.post-card-score { font-weight: 700; color: #f5a623; white-space: nowrap; }

/* 카드 컨테이너: 마크다운 카드와 '상세 보기' 버튼을 한 덩어리로 */
[class*="st-key-pc-"] { position: relative; }
[class*="st-key-pc-"] [data-testid="stVerticalBlock"] { gap: 0 !important; }
[class*="st-key-pc-"] .stButton > button,
[class*="st-key-pc-"] button[data-testid^="stBaseButton"] {
  width: 100%;
  border: 1px solid rgba(128,128,128,.28) !important;
  border-radius: 0 0 16px 16px !important;
  margin-top: -1px;
  font-weight: 600;
}
[class*="st-key-pc-"]:hover .post-card {
  box-shadow: 0 8px 28px rgba(0,0,0,.14);
}
[class*="st-key-pc-"]:hover .post-card { transform: translateY(-2px); }

/* 모달 뒤 배경: 어둡게 + 살짝 블러 (best-effort — 안 먹어도 기본 딤은 적용됨) */
div[data-baseweb="modal"] > div:first-child,
div[data-testid="stDialog"] ~ div,
.stDialog + div {
  background-color: rgba(0,0,0,.55) !important;
  backdrop-filter: blur(2px);
}
</style>
"""


def _plain_excerpt(md: str, n: int = 160) -> str:
    """마크다운에서 대략적인 평문 발췌를 만든다."""
    t = md or ""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)          # 이미지 제거
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)      # 링크 -> 텍스트
    t = re.sub(r"[#>*`_~|-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _card_image_url(post: dict) -> str:
    """카드 이미지 URL: Pexels image_url → 본문 첫 이미지 → 주제 기반 플레이스홀더."""
    if post.get("image_url"):
        return post["image_url"]
    m = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", post.get("final_content") or "")
    if m:
        return m.group(1)
    seed = hashlib.md5((post.get("topic") or "post").encode("utf-8")).hexdigest()[:12]
    return f"https://picsum.photos/seed/{seed}/600/400"


def _render_grounding_warning(src: dict) -> None:
    """리서치 근거가 부족한 결과에 눈에 띄는 경고 배너를 표시한다."""
    if src.get("research_grounded") is not False:
        return
    reason = (src.get("research_reason") or "").strip()
    st.error(
        "⚠️ **이 주제는 온라인에서 확인 가능한 구체적 정보가 부족합니다.** "
        "일반적인 내용 위주로 작성되었으니 **사실 확인이 필요합니다.**"
        + (f"\n\n> {reason}" if reason else "")
    )


def _render_agent_log(src: dict) -> None:
    """에이전트 자체 판단 로그를 접이식으로 표시한다 (검색 재시도 / 자체 검토 / 재작성)."""
    has_any = any(
        src.get(k) is not None
        for k in ("search_retried", "critique_passed", "was_rewritten",
                  "critique_feedback", "research_grounded")
    )
    if not has_any:
        return

    retried = bool(src.get("search_retried"))
    passed = src.get("critique_passed")
    rewritten = bool(src.get("was_rewritten"))
    feedback = src.get("critique_feedback") or ""
    grounded = src.get("research_grounded")

    if passed is True and not rewritten:
        summary = "✅ 자체 검토 통과"
    elif rewritten:
        summary = "🔁 자체 검토 후 재작성함"
    elif passed is False:
        summary = "⚠️ 자체 검토 미통과"
    else:
        summary = "자체 판단 로그"

    # 리서치 근거 부족은 검토 결과보다 우선해서 제목에 노출
    if grounded is False:
        summary = f"⚠️ 리서치 근거 부족 · {summary}"

    with st.expander(f"🧠 에이전트 판단 로그 — {summary}"):
        if grounded is not None:
            gtext = "충분" if grounded else "부족 ⚠️ (사실 확인 필요)"
            st.markdown(f"- **리서치 근거**: {gtext}")
            if grounded is False and src.get("research_reason"):
                st.caption(str(src["research_reason"]))
        st.markdown(f"- **검색 재시도**: {'예 (결과 부족으로 새 키워드 재검색)' if retried else '아니오'}")
        if passed is None:
            st.markdown("- **자체 검토**: 기록 없음")
        else:
            st.markdown(f"- **자체 검토 결과**: {'통과(pass)' if passed else '미통과(fail)'}")
        st.markdown(f"- **재작성 여부**: {'예 (피드백 반영해 1회 재작성)' if rewritten else '아니오'}")
        if feedback:
            main_fb, _, auto_note = feedback.partition("⚠️[자동 점검]")
            st.markdown("- **검토 피드백**:")
            st.info(main_fb.strip())
            if auto_note.strip():
                st.warning("⚠️ 자동 점검: " + auto_note.strip())


def _render_seo_report(report: dict | None) -> None:
    """SEO 키워드 등장 횟수를 metric 으로 표시."""
    report = report or {}
    if not report:
        return
    st.markdown("**SEO 키워드 체크** — 본문 등장 횟수 (권장: 각 3~5회)")
    cols = st.columns(len(report))
    for col, (kw, cnt) in zip(cols, report.items()):
        delta = "적정" if 3 <= cnt <= 5 else ("부족" if cnt < 3 else "과다")
        col.metric(kw, f"{cnt}회", delta, delta_color="off")


def _card_key(item: dict) -> str:
    return str(item.get("id") or item.get("post_id") or "new")


def _close_modal() -> None:
    """모달 상태 해제 (X·바깥클릭·ESC 로 dismiss 시 on_dismiss 로도 호출됨)."""
    st.session_state.pop("open_card", None)


@st.dialog("상세 보기", width="large", on_dismiss=_close_modal)
def _detail_dialog(item: dict) -> None:
    """카드 '상세 보기' 클릭 시 열리는 모달.

    스크롤·X·바깥클릭·ESC 로 닫기, 배경 딤은 st.dialog 기본 제공.
    바깥클릭/ESC 시 on_dismiss=_close_modal 이 open_card 상태를 지워 재렌더를 막는다.
    """
    topic = item.get("topic") or "블로그 글"

    st.image(_card_image_url(item), use_container_width=True)
    st.subheader(topic)

    bits = []
    if item.get("tone"):
        bits.append(item["tone"])
    if item.get("similarity") is not None:
        bits.append(f"유사도 {item['similarity']:.2f}")
    if item.get("created_at"):
        bits.append(str(item["created_at"])[:10])
    if bits:
        st.caption(" · ".join(bits))

    _render_grounding_warning(item)
    _render_agent_log(item)

    st.divider()
    if item.get("_kind") == "new":
        st.markdown("#### 1. 리서치 결과")
        st.markdown(item.get("research") or "_(내용 없음)_")

        srcs = item.get("sources") or []
        if srcs:
            st.markdown("**검색된 출처** (✅ = 대전 신뢰 소스)")
            for s in srcs:
                if s.get("url"):
                    label = s.get("title") or s["url"]
                    badge = f" ✅ {s['source_name']}" if s.get("trusted") else ""
                    st.markdown(f"- [{label}]({s['url']}){badge}")

        st.markdown("#### 2. 아웃라인")
        st.markdown(item.get("outline") or "_(내용 없음)_")

        st.markdown("#### 3. 블로그 초안")
        st.markdown(item.get("draft") or "")

        _render_seo_report(item.get("seo_report"))
        dl_data = item.get("draft") or ""
    else:
        st.markdown(item.get("final_content") or "")
        dl_data = item.get("final_content") or ""

    st.divider()
    c1, c2 = st.columns(2)
    c1.download_button(
        "📥 .md 다운로드",
        data=dl_data,
        file_name=f"{blog_agent.slugify(topic)}.md",
        mime="text/markdown",
        use_container_width=True,
        key=f"dl-{_card_key(item)}",
    )
    if c2.button("닫기", use_container_width=True, key="modal-close"):
        _close_modal()
        st.rerun()


def _one_card(item: dict) -> None:
    """카드 한 장 — 이미지(상단) + 제목/발췌/메타(하단) + '상세 보기' 버튼."""
    is_new = item.get("_kind") == "new"
    if is_new:
        sub = " · ".join(x for x in ["방금 생성", item.get("tone")] if x)
        score = "🆕"
    else:
        sub = " · ".join(
            b for b in [item.get("tone"), str(item.get("created_at") or "")[:10]] if b
        )
        sim = item.get("similarity")
        score = f"⭐ {sim:.2f}" if sim is not None else ""

    excerpt = _plain_excerpt(item.get("final_content") or item.get("draft") or "", 120)
    card = f"""
<div class="post-card">
  <div class="post-card-img" style="background-image:url('{_card_image_url(item)}')"></div>
  <div class="post-card-body">
    <div class="post-card-title">{html_lib.escape(item.get('topic') or '')}</div>
    <div class="post-card-text">{html_lib.escape(excerpt)}</div>
    <div class="post-card-foot">
      <span class="post-card-sub">{html_lib.escape(sub)}</span>
      <span class="post-card-score">{html_lib.escape(score)}</span>
    </div>
  </div>
</div>
"""
    with st.container(key=f"pc-{_card_key(item)}"):
        st.markdown(card, unsafe_allow_html=True)
        if st.button(
            "🔎 상세 보기", key=f"open-{_card_key(item)}", use_container_width=True
        ):
            st.session_state["open_card"] = item
            st.rerun()


def _render_result_cards(items: list[dict]) -> None:
    """결과(생성된 새 글 · 유사 글)를 카드 그리드로 표시하고, 열린 모달을 렌더."""
    if not items:
        return
    st.markdown(CARD_CSS, unsafe_allow_html=True)
    per_row = 3
    for i in range(0, len(items), per_row):
        cols = st.columns(per_row, gap="medium")
        for col, item in zip(cols, items[i:i + per_row]):
            with col:
                _one_card(item)

    if st.session_state.get("open_card") is not None:
        _detail_dialog(st.session_state["open_card"])


def _friendly_error(e: Exception) -> str:
    """예외를 사용자가 이해할 수 있는 한국어 안내 문구로 바꾼다."""
    if isinstance(e, anthropic.AuthenticationError):
        return (
            "Anthropic API 키가 유효하지 않습니다. "
            "프로젝트 루트 `.env` 의 `ANTHROPIC_API_KEY` 값을 확인하세요."
        )
    if isinstance(e, anthropic.PermissionDeniedError):
        return (
            "이 API 키로는 해당 모델을 사용할 수 없습니다. "
            "Anthropic 콘솔에서 결제·권한 상태를 확인하세요."
        )
    if isinstance(e, anthropic.RateLimitError):
        return (
            "Anthropic API 사용량 한도(rate limit)에 걸렸습니다. "
            "잠시 후 다시 시도하거나 콘솔에서 한도를 확인하세요."
        )
    if isinstance(e, anthropic.APIConnectionError):
        return "Anthropic API 에 연결하지 못했습니다. 네트워크 상태를 확인하세요."
    if isinstance(e, anthropic.APIStatusError):
        return (
            f"Anthropic API 오류가 발생했습니다 (HTTP {e.status_code}). "
            "잠시 후 다시 시도하세요."
        )
    if isinstance(e, anthropic.APIError):
        return f"Anthropic API 호출 중 오류가 발생했습니다: {e}"
    if isinstance(e, psycopg2.OperationalError):
        return (
            "데이터베이스에 연결하지 못했습니다. Docker 컨테이너(`daejeon-blog-db`) 실행 여부와 "
            "`.env` 의 `DB_*` 설정을 확인하세요."
        )
    if isinstance(e, psycopg2.Error):
        return f"데이터베이스 오류가 발생했습니다: {e}"
    return f"예상치 못한 오류가 발생했습니다: {e}"


@contextmanager
def _guard():
    """블록 내부에서 발생한 예외를 안내 문구로 표시하고 실행을 멈춘다."""
    try:
        yield
    except Exception as e:  # noqa: BLE001 - 사용자에게 보여줄 최종 방어선
        st.error(_friendly_error(e))
        st.stop()


# 파이프라인 단계 → (진행 중 라벨, 완료 라벨)
_STAGE_LABELS = {
    "similar": ("기존 글 확인 중 (임베딩 유사도 검색)...", "기존 글 확인 완료"),
    "research": ("1/3 · 리서치 중 (웹 검색)...", "1/3 · 리서치 완료"),
    "outline": ("2/3 · 아웃라인 작성 중...", "2/3 · 아웃라인 완료"),
    "grounding": ("리서치 근거 확인 중...", "리서치 근거 확인 완료"),
    "draft": ("3/3 · 블로그 초안 작성 중...", "3/3 · 초안 완료"),
    "critique": ("자체 검토 중 (사실·구조·톤·SEO)...", "자체 검토 완료"),
    "rewrite": ("재작성 중 (검토 피드백 반영)...", "재작성 완료"),
    "image": ("이미지 검색 중 (Pexels)...", "이미지 준비 완료"),
    "save": ("저장 중 (임베딩 생성 + DB 저장)...", "저장 완료"),
}


def _run_pipeline(
    topic_text: str, tone: str, seo_raw: str, abort_on_weak_research: bool = False
) -> dict:
    """blog_agent.generate_blog 를 단계별 st.status UI 콜백과 함께 호출한다."""
    widgets: dict = {}

    def on_stage(ev: dict) -> None:
        stage, state = ev["stage"], ev["state"]
        start_label, done_label = _STAGE_LABELS[stage]
        if state == "start":
            if stage == "draft" and ev.get("tone"):
                start_label = f"3/3 · 블로그 초안 작성 중 (톤: {ev['tone']})..."
            widgets[stage] = st.status(start_label, expanded=(stage == "research"))
        elif state == "done":
            widget = widgets.get(stage)
            if widget is None:
                return
            if stage == "save" and ev.get("post_id"):
                done_label = f"blog_posts #{ev['post_id']} 저장 완료"
            elif stage == "image" and not ev.get("image_url"):
                done_label = "이미지 없음 (플레이스홀더 사용)"
            elif stage == "critique":
                done_label = (
                    "자체 검토 통과" if ev.get("passed") else "자체 검토 미통과 → 재작성"
                )
            elif stage == "grounding" and ev.get("grounded") is False:
                done_label = "리서치 근거 부족 ⚠️"
            widget.update(label=done_label, state="complete", expanded=False)

    def on_search(query: str) -> None:
        (widgets.get("research") or st).write(f"🔎 검색: `{query}`")

    return blog_agent.generate_blog(
        topic_text,
        on_search=on_search,
        on_stage=on_stage,
        tone=tone,
        seo_keywords=seo_raw,
        abort_on_weak_research=abort_on_weak_research,
    )


def _stash_result(result: dict) -> None:
    """generate_blog 결과를 결과 표시 섹션이 읽는 session_state 키로 옮긴다."""
    if result.get("aborted"):
        st.session_state["mode"] = "aborted"
        st.session_state["research_grounded"] = result.get("research_grounded")
        st.session_state["research_reason"] = result.get("research_reason")
        return

    if result["existing"]:
        posts = result["similar_posts"]
        st.session_state["mode"] = "existing"
        st.session_state["cards"] = posts
        st.session_state["grounding_src"] = posts[0] if posts else {}
        return

    # 새로 생성 — 결과 dict 전체를 카드 1장으로 (모달에서 리서치/아웃라인/초안 표시)
    item = {**result, "_kind": "new", "id": result.get("post_id")}
    st.session_state["mode"] = "new"
    st.session_state["cards"] = [item]
    st.session_state["grounding_src"] = item
    st.session_state["post_id"] = result.get("post_id")
    st.session_state["tone"] = result.get("tone")


topic = st.text_input("블로그 주제", value="대전 성심당 빵집 추천")

col1, col2 = st.columns(2)
with col1:
    tone = st.selectbox("톤앤매너", TONE_OPTIONS, index=0)
with col2:
    seo_raw = st.text_input(
        "SEO 키워드 (선택 · 쉼표로 구분)", value="", placeholder="예: 대전 맛집, 성심당"
    )

abort_weak = st.checkbox(
    "리서치 근거 부족 시 생성 중단",
    value=False,
    help=(
        "체크 시(옵션 A): 온라인에서 확인 가능한 구체 정보가 부족하다고 판단되면 "
        "초안을 만들지 않고 중단합니다. 해제 시(옵션 B, 기본): 경고와 함께 "
        "일반론 위주로 초안을 생성합니다."
    ),
)

run = st.button("블로그 생성", type="primary")

if run and topic.strip():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY가 없습니다. 프로젝트 루트의 .env 파일을 확인하세요.")
        st.stop()

    for key in RESULT_KEYS:
        st.session_state.pop(key, None)

    t = topic.strip()
    st.session_state["topic"] = t
    st.session_state["tone"] = tone
    st.session_state["seo_keywords"] = seo_raw

    with _guard():
        result = _run_pipeline(t, tone, seo_raw, abort_on_weak_research=abort_weak)
        _stash_result(result)


# --- 결과 표시 (생성 직후 & 재실행 시 모두) ---
mode = st.session_state.get("mode")
topic_done = st.session_state.get("topic", "blog")

if mode == "aborted":
    st.divider()
    _render_grounding_warning(st.session_state)
    st.info(
        "리서치 근거가 부족해 초안 생성을 **중단**했습니다. "
        "주제를 더 구체적으로 바꾸거나 다른 주제로 시도해 보세요."
    )
    st.caption(f"입력한 주제: {topic_done}")

elif mode in ("existing", "new"):
    cards = st.session_state.get("cards") or []
    st.divider()
    _render_grounding_warning(
        st.session_state.get("grounding_src") or (cards[0] if cards else {})
    )
    if mode == "existing":
        st.warning(
            f"이미 비슷한 글이 {len(cards)}건 있습니다. "
            "새로 생성하지 않고 기존 글을 보여드립니다."
        )
    else:
        st.success(
            f"새 글을 생성했습니다. (톤: {st.session_state.get('tone')} · "
            f"blog_posts #{st.session_state.get('post_id')} 저장됨)"
        )
    st.caption(f"입력한 주제: {topic_done} · 카드를 클릭하면 상세 내용이 열립니다")
    _render_result_cards(cards)
