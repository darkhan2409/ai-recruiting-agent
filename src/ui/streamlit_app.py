"""HCB Recruiting Agent — Streamlit UI.

Один файл по CLAUDE.md «не дробить на много файлов то, что помещается в 2».
~300 строк, 3 таба:
  • 🔍 Поиск — saved job dropdown / ad-hoc text → top-k cards с
    confidence/recommendation badges; expand с matched/gaps/extras/quotes/
    скачать оригинал.
  • ⚠ Quarantine — read-only список битых файлов.
  • 👤 Кандидаты — таблица + DELETE с подтверждением (right to be forgotten).

Анти-халлюцинация — продуктовая фича: confidence=low → 🔴 + автораскрытие
карточки, чтобы рекрутёр сразу видел, что именно LLM выдумал.

Sync httpx без AsyncClient — Streamlit single-threaded per session, async
не даёт выигрыша и усложняет код.
"""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://api:8000")
TIMEOUT = httpx.Timeout(120.0, connect=5.0)

CONFIDENCE_BADGE = {"high": "🟢", "medium": "🟡", "low": "🔴"}
RECOMMENDATION_BADGE = {"interview": "🎯", "consider": "🤔", "pass": "❌"}

METHOD_LABELS = {
    "hybrid": "Гибридный (рекомендуется)",
    "dense": "Семантический",
    "tfidf": "Ключевые слова",
    "llm": "LLM-оценка",
}
LANG_LABELS = {"ru": "Русский", "en": "Английский"}


# --- API client ---


@st.cache_data(ttl=30, show_spinner=False)
def fetch_jobs() -> list[dict[str, Any]]:
    r = httpx.get(f"{API_URL}/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    return cast(list[dict[str, Any]], r.json())


@st.cache_data(ttl=30, show_spinner=False)
def fetch_candidates() -> list[dict[str, Any]]:
    r = httpx.get(f"{API_URL}/candidates", timeout=TIMEOUT)
    r.raise_for_status()
    return cast(list[dict[str, Any]], r.json())


def fetch_quarantine() -> list[dict[str, Any]]:
    r = httpx.get(f"{API_URL}/quarantine", timeout=TIMEOUT)
    r.raise_for_status()
    return cast(list[dict[str, Any]], r.json())


@st.cache_data(ttl=10, show_spinner=False)
def fetch_health() -> dict[str, Any]:
    try:
        r = httpx.get(f"{API_URL}/health", timeout=5.0)
        return cast(dict[str, Any], r.json())
    except Exception as exc:
        return {"status": "down", "components": {}, "error": str(exc)}


def fetch_job_detail(job_id: int) -> dict[str, Any]:
    r = httpx.get(f"{API_URL}/jobs/{job_id}", timeout=TIMEOUT)
    r.raise_for_status()
    return cast(dict[str, Any], r.json())


def fetch_recommendations_get(
    job_id: int, top_k: int, method: str, min_score: float
) -> dict[str, Any]:
    r = httpx.get(
        f"{API_URL}/recommendations",
        params={"job_id": job_id, "top_k": top_k, "method": method, "min_score": min_score},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return cast(dict[str, Any], r.json())


def fetch_recommendations_post(
    job_text: str,
    required_skills: list[str],
    top_k: int,
    method: str,
    min_score: float,
) -> dict[str, Any]:
    r = httpx.post(
        f"{API_URL}/recommendations",
        json={
            "job_text": job_text,
            "required_skills": required_skills,
            "top_k": top_k,
            "method": method,
            "min_score": min_score,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return cast(dict[str, Any], r.json())


def trigger_sync_mail() -> dict[str, int]:
    r = httpx.post(f"{API_URL}/sync-mail", timeout=TIMEOUT)
    r.raise_for_status()
    return cast(dict[str, int], r.json())


def delete_candidate(candidate_id: int) -> bool:
    r = httpx.delete(f"{API_URL}/candidates/{candidate_id}", timeout=TIMEOUT)
    return r.status_code == 204


def delete_job(job_id: int) -> bool:
    r = httpx.delete(f"{API_URL}/jobs/{job_id}", timeout=TIMEOUT)
    return r.status_code == 204


def fetch_candidate_file(candidate_id: int) -> tuple[bytes, str] | None:
    """Возвращает (bytes, filename) или None при 404 / ошибке."""
    try:
        r = httpx.get(f"{API_URL}/candidates/{candidate_id}/file", timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        cd = r.headers.get("Content-Disposition", "")
        filename = f"resume_{candidate_id}"
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"').strip("'")
        return r.content, filename
    except Exception:
        return None


def _match_option_index(prefill: str, options: list[str]) -> int:
    """Case-insensitive поиск prefill в options. Не нашли → 0 (—).

    LLM может вернуть «гибрид» / «Гибрид» / «hybrid» / «hybrid (3/2)» —
    подстраховываемся case-insensitive equality + substring fallback.
    """
    if not prefill:
        return 0
    lo = prefill.strip().lower()
    for i, opt in enumerate(options):
        if opt.strip().lower() == lo:
            return i
    for i, opt in enumerate(options):
        if opt.strip().lower() != "—" and opt.strip().lower() in lo:
            return i
    return 0


# --- Page setup ---

st.set_page_config(page_title="HCB Recruiting Agent", layout="wide", page_icon="🎯")

# Лёгкий CSS-bump: крупнее базовый шрифт, мягкие отступы у bordered-containers
# (карточки в st.container(border=True)), чуть строже типографика заголовков.
# Один блок, БЕЗ зависимости от Streamlit internal classes — только публичные
# data-testid атрибуты, чтобы апдейт Streamlit не сломал стили.
st.markdown(
    """
    <style>
      html, body, [data-testid="stAppViewContainer"] {
          font-size: 16px;
      }
      .stRadio label, .stSelectbox label, .stTextInput label,
      .stTextArea label, .stSlider label, .stFileUploader label,
      .stCheckbox label, .stMultiSelect label {
          font-size: 0.95rem;
          font-weight: 500;
      }
      h1 { font-size: 2.0rem; }
      h2 { font-size: 1.45rem; margin-top: 0.4rem; }
      h3 { font-size: 1.20rem; margin-top: 0.4rem; }
      /* Карточки от st.container(border=True) */
      [data-testid="stVerticalBlockBorderWrapper"] {
          padding: 1.1rem 1.4rem;
          margin-bottom: 1.25rem;
          border-radius: 0.6rem;
      }
      /* Спейсинг между табами и контентом */
      [data-testid="stTabs"] [data-baseweb="tab-list"] {
          gap: 0.25rem;
          margin-bottom: 0.6rem;
      }
      /* Чуть просторнее общий main */
      .block-container { padding-top: 1.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🎯 HCB Recruiting Agent")


# --- Sidebar ---

with st.sidebar:
    st.header("⚙ Параметры поиска")
    method = st.radio(
        "Метод матчинга",
        options=["hybrid", "dense", "tfidf", "llm"],
        index=0,
        format_func=lambda v: METHOD_LABELS[v],
        help=(
            "hybrid (default) — RRF dense+tfidf → LLM-judge → anti-halluc. "
            "Остальные — для сравнения подходов на одной вакансии."
        ),
    )
    with st.expander("⚙️ Расширенные настройки", expanded=False):
        top_k = st.slider("Top K кандидатов", min_value=1, max_value=20, value=5)
        min_score = st.slider(
            "Мин. порог релевантности",
            min_value=0.0,
            max_value=1.0,
            value=0.45,
            step=0.05,
            help="Кандидаты ниже порога не показываются (блокировка «топ-5 случайных»).",
        )

    st.divider()
    st.header("🔌 Состояние")
    health = fetch_health()
    pg_ok = health.get("components", {}).get("postgres") == "ok"
    qd_ok = health.get("components", {}).get("qdrant") == "ok"
    st.markdown(f"**База данных:** {'🟢 ok' if pg_ok else '🔴 down'}")
    st.markdown(f"**Векторный поиск:** {'🟢 ok' if qd_ok else '🔴 down'}")
    try:
        st.markdown(f"**Вакансий:** {len(fetch_jobs())}")
        st.markdown(f"**Кандидатов:** {len(fetch_candidates())}")
    except Exception as exc:
        st.error(f"API недоступен: {exc}")

    st.divider()
    if st.button("🔄 Синхронизировать почту", use_container_width=True):
        with st.spinner("Опрашиваем источник..."):
            try:
                counts = trigger_sync_mail()
                st.toast(
                    f"✅ processed={counts['processed']} "
                    f"skipped={counts['skipped']} failed={counts['failed']}"
                )
                fetch_jobs.clear()
                fetch_candidates.clear()
            except Exception as exc:
                st.error(f"Sync failed: {exc}")


# --- Tabs ---

tab_search, tab_add_job, tab_jobs_list, tab_quarantine, tab_candidates = st.tabs(
    [
        "🔍 Поиск кандидатов",
        "➕ Добавить вакансию",
        "📋 Список вакансий",
        "⚠ Карантин",
        "👤 Кандидаты",
    ]
)


# --- TAB 1: Поиск ---

with tab_search:
    with st.container(border=True):
        st.markdown("### 📄 Вакансия для поиска")
        source = st.radio(
            "Источник вакансии",
            options=["Сохранённая вакансия", "Произвольный текст"],
            horizontal=True,
        )

        job_to_search: dict[str, Any] | None = None
        job_text_input = ""
        required_skills_input: list[str] = []

        if source == "Сохранённая вакансия":
            try:
                jobs = fetch_jobs()
            except Exception as exc:
                st.error(f"Не удалось загрузить вакансии: {exc}")
                jobs = []
            if not jobs:
                st.info("Нет сохранённых вакансий. Перейдите в таб «➕ Добавить вакансию».")
            else:
                options = {
                    f"{j['title']} ({LANG_LABELS.get(j['language'], j['language'])})": j
                    for j in jobs
                }
                selected = st.selectbox("Вакансия", options=list(options.keys()))
                job_to_search = options[selected]
                with st.expander("📄 Описание и навыки", expanded=False):
                    try:
                        detail = fetch_job_detail(job_to_search["id"])
                        skills = detail.get("required_skills", [])
                        if skills:
                            st.markdown(
                                "**Required skills:** " + " ".join(f"`{s}`" for s in skills)
                            )
                        st.markdown(f"**Description:**\n\n{detail.get('description', '')}")
                    except Exception as exc:
                        st.error(f"Не удалось загрузить детали вакансии: {exc}")
        else:
            job_text_input = st.text_area(
                "Текст вакансии",
                height=200,
                placeholder="Senior Python developer with FastAPI and Kubernetes...",
            )
            skills_csv = st.text_input(
                "Required skills (через запятую, опционально)",
                placeholder="python, fastapi, kubernetes",
            )
            required_skills_input = [s.strip() for s in skills_csv.split(",") if s.strip()]

    can_search = (source == "Сохранённая вакансия" and job_to_search is not None) or (
        source == "Произвольный текст" and len(job_text_input.strip()) >= 20
    )

    if st.button("🔍 Найти кандидатов", type="primary", disabled=not can_search):
        with st.spinner(f"Считаем матч (method={method})... cold cache до 30 сек."):
            try:
                if source == "Сохранённая вакансия" and job_to_search is not None:
                    response = fetch_recommendations_get(
                        job_to_search["id"], top_k, method, min_score
                    )
                else:
                    response = fetch_recommendations_post(
                        job_text_input,
                        required_skills_input,
                        top_k,
                        method,
                        min_score,
                    )
                st.session_state["last_results"] = response
            except httpx.HTTPStatusError as exc:
                st.error(f"API error {exc.response.status_code}: {exc.response.text}")
            except Exception as exc:
                st.error(f"Search failed: {exc}")

    # --- Results ---
    if "last_results" in st.session_state:
        response = st.session_state["last_results"]
        results = response.get("results", [])
        st.markdown(
            f"### 🎯 Результаты  "
            f"<span style='font-size:0.9rem;color:#6b7280;font-weight:400;'>"
            f"method={response['method']} · min_score={response['min_score']}</span>",
            unsafe_allow_html=True,
        )

        if not results:
            st.warning(
                f"❌ По вакансии нет кандидатов с релевантностью ≥ {response['min_score']}. "
                "Попробуйте снизить порог или загрузить новые резюме."
            )
        elif len(results) < top_k:
            st.info(
                f"ℹ Найдено {len(results)} кандидатов с релевантностью ≥ "
                f"{response['min_score']} (запрашивали {top_k})."
            )

        try:
            cands_by_id = {c["id"]: c for c in fetch_candidates()}
        except Exception:
            cands_by_id = {}

        for r in results:
            cand_id = r["candidate_id"]
            cand_info = cands_by_id.get(cand_id, {})
            name = cand_info.get("full_name") or f"Candidate {cand_id}"
            score = float(r["score"])
            confidence = r["confidence"]
            recommendation = r["recommendation"]

            with st.container(border=True):
                cols = st.columns([3, 2, 2, 2])
                cols[0].markdown(f"**{name}**  `id={cand_id}`")
                cols[1].progress(min(max(score, 0.0), 1.0), text=f"{score:.2f}")
                cols[2].markdown(f"{CONFIDENCE_BADGE.get(confidence, '?')} {confidence}")
                cols[3].markdown(
                    f"{RECOMMENDATION_BADGE.get(recommendation, '?')} {recommendation}"
                )

                with st.expander("📋 Detail", expanded=(confidence == "low")):
                    if r.get("matched_skills"):
                        st.markdown(
                            "**✅ Matched:** " + " ".join(f"`{s}`" for s in r["matched_skills"])
                        )
                    if r.get("gaps"):
                        st.markdown("**❌ Gaps:** " + " ".join(f"`{s}`" for s in r["gaps"]))
                    if r.get("extras"):
                        st.markdown("**➕ Extras:** " + " ".join(f"`{s}`" for s in r["extras"]))
                    if r.get("explanation"):
                        st.markdown(f"**💬 Explanation:** {r['explanation']}")
                    if r.get("quotes"):
                        st.markdown("**📝 Quotes from resume:**")
                        for q in r["quotes"]:
                            st.markdown(f"> {q}")

                    file_data = fetch_candidate_file(cand_id)
                    if file_data is not None:
                        content, filename = file_data
                        st.download_button(
                            "📥 Скачать оригинал",
                            data=content,
                            file_name=filename,
                            mime="application/octet-stream",
                            key=f"dl_{cand_id}_{response.get('job_id', 'adhoc')}",
                        )


# --- TAB 2: Добавить вакансию ---

with tab_add_job:
    EXPERIENCE_OPTIONS = ["—", "Без опыта", "1-3 года", "3-5 лет", "5+ лет"]
    WORK_FORMAT_OPTIONS = ["—", "Офис", "Гибрид", "Удалёнка"]

    with st.container(border=True):
        st.markdown("### ➕ Источник данных")
        add_mode = st.radio(
            "Способ заполнения",
            options=["Заполнить вручную", "Загрузить документ"],
            horizontal=True,
            key="add_job_mode",
        )

    # File-uploader должен жить ВНЕ st.form — иначе LLM-парсинг отложится
    # до сабмита формы (st.form блокирует промежуточные ре-рендеры).
    if add_mode == "Загрузить документ":
        with st.container(border=True):
            st.markdown("### 📂 Загрузка DOCX")
            uploaded_doc = st.file_uploader(
                "Документ вакансии (.docx)",
                type=["docx"],
                key="job_doc_uploader",
            )
        if uploaded_doc is not None:
            doc_marker = f"{uploaded_doc.name}|{uploaded_doc.size}"
            if st.session_state.get("last_parsed_doc") != doc_marker:
                with st.spinner("Анализируем документ через LLM..."):
                    try:
                        files = {
                            "file": (
                                uploaded_doc.name,
                                uploaded_doc.getvalue(),
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            )
                        }
                        with httpx.Client(timeout=TIMEOUT) as client:
                            resp = client.post(f"{API_URL}/jobs/parse-document", files=files)
                            resp.raise_for_status()
                            parsed = resp.json()

                        # Записываем напрямую в widget-keys формы — st.rerun
                        # ниже применит значения к ре-рендеренным виджетам.
                        st.session_state["form_title"] = parsed.get("title") or ""
                        st.session_state["form_description"] = parsed.get("description") or ""
                        st.session_state["form_department"] = parsed.get("department") or ""
                        st.session_state["form_experience"] = EXPERIENCE_OPTIONS[
                            _match_option_index(
                                parsed.get("experience_years") or "",
                                EXPERIENCE_OPTIONS,
                            )
                        ]
                        st.session_state["form_work_format"] = WORK_FORMAT_OPTIONS[
                            _match_option_index(
                                parsed.get("work_format") or "",
                                WORK_FORMAT_OPTIONS,
                            )
                        ]
                        st.session_state["form_salary"] = parsed.get("salary_range") or ""
                        st.session_state["form_responsibilities"] = (
                            parsed.get("responsibilities") or ""
                        )
                        st.session_state["form_conditions"] = parsed.get("conditions") or ""
                        st.session_state["form_skills"] = ", ".join(
                            parsed.get("required_skills") or []
                        )
                        st.session_state["form_language"] = (
                            "Русский" if parsed.get("language") == "ru" else "Английский"
                        )
                        st.session_state["last_parsed_doc"] = doc_marker
                        st.session_state["job_form_just_parsed"] = True
                        st.rerun()
                    except httpx.HTTPStatusError as exc:
                        st.error(
                            f"Ошибка парсинга ({exc.response.status_code}): "
                            f"{exc.response.text[:300]}"
                        )
                    except Exception as exc:
                        st.error(f"Не удалось обработать документ: {exc}")

        # Маркер `just_parsed` появляется один раз после успешного rerun.
        if st.session_state.pop("job_form_just_parsed", False):
            st.success(
                "✅ Документ распарсен. Поля формы пре-заполнены — проверьте и нажмите «Сохранить»."
            )

    with st.container(border=True):
        st.markdown("### 📝 Поля вакансии")
        with st.form("create_job_form", clear_on_submit=True):
            title_input = st.text_input(
                "Название должности *",
                placeholder="Например: Senior AI Engineer",
                key="form_title",
            )
            department_input = st.text_input(
                "Отдел",
                placeholder="Например: Управление AI-разработки",
                key="form_department",
            )

            col_exp, col_fmt = st.columns(2)
            with col_exp:
                experience_choice = st.selectbox(
                    "Требуемый опыт",
                    options=EXPERIENCE_OPTIONS,
                    key="form_experience",
                )
            with col_fmt:
                work_format_choice = st.selectbox(
                    "Формат работы",
                    options=WORK_FORMAT_OPTIONS,
                    key="form_work_format",
                )

            salary_input = st.text_input(
                "Зарплатная вилка",
                placeholder="Например: от 700 000 ₽ gross",
                key="form_salary",
            )
            responsibilities_input = st.text_area(
                "Обязанности",
                height=140,
                placeholder="Каждая обязанность с новой строки",
                key="form_responsibilities",
            )
            description_input = st.text_area(
                "Описание и требования *",
                height=220,
                help="Краткое описание вакансии. Минимум 20 символов.",
                key="form_description",
            )
            conditions_input = st.text_area(
                "Условия",
                height=100,
                placeholder="Бенефиты, льготы, оборудование",
                key="form_conditions",
            )
            skills_csv_input = st.text_input(
                "Ключевые навыки",
                placeholder="через запятую: python, fastapi, pytorch, llm",
                key="form_skills",
            )
            language_choice = st.selectbox(
                "Язык",
                options=["Русский", "Английский"],
                key="form_language",
            )

            submitted = st.form_submit_button("💾 Сохранить вакансию", type="primary")

    if submitted:
        # UI-валидация: backend тоже отвергнет, но раннее сообщение удобнее.
        if not title_input.strip():
            st.error("Заполните «Название должности».")
        elif len(description_input.strip()) < 20:
            st.error("«Описание и требования» — минимум 20 символов.")
        else:
            payload: dict[str, Any] = {
                "title": title_input.strip(),
                "description": description_input.strip(),
                "language": "ru" if language_choice == "Русский" else "en",
            }
            if department_input.strip():
                payload["department"] = department_input.strip()
            if experience_choice and experience_choice != "—":
                payload["experience_years"] = experience_choice
            if work_format_choice and work_format_choice != "—":
                payload["work_format"] = work_format_choice
            if salary_input.strip():
                payload["salary_range"] = salary_input.strip()
            if responsibilities_input.strip():
                payload["responsibilities"] = responsibilities_input.strip()
            if conditions_input.strip():
                payload["conditions"] = conditions_input.strip()
            skills_list = [s.strip().lower() for s in skills_csv_input.split(",") if s.strip()]
            if skills_list:
                payload["required_skills"] = skills_list

            try:
                with httpx.Client(timeout=TIMEOUT) as client:
                    r = client.post(f"{API_URL}/jobs", json=payload)
                    r.raise_for_status()
                    created = r.json()
                st.success(
                    f"✅ Вакансия сохранена (id={created['id']}). "
                    "Доступна в табе «🔍 Поиск кандидатов»."
                )
                fetch_jobs.clear()
                # `clear_on_submit=True` уже очистил form_* widget-keys; явно
                # сбрасываем маркер парсинга, чтобы повторная загрузка того же
                # файла снова сработала.
                st.session_state.pop("last_parsed_doc", None)
            except httpx.HTTPStatusError as exc:
                st.error(f"Ошибка сервера ({exc.response.status_code}): {exc.response.text[:300]}")
            except Exception as exc:
                st.error(f"Не удалось сохранить: {exc}")


# --- TAB 3: Список вакансий ---

with tab_jobs_list:
    try:
        jobs_list = fetch_jobs()
    except Exception as exc:
        st.error(f"Не удалось загрузить вакансии: {exc}")
        jobs_list = []

    if not jobs_list:
        st.info("Нет сохранённых вакансий. Перейдите в таб «➕ Добавить вакансию».")
    else:
        with st.container(border=True):
            st.markdown("### 📋 Все вакансии")
            st.caption("Выберите одну ниже, чтобы посмотреть полный текст или удалить.")
            st.dataframe(
                [
                    {
                        "id": j["id"],
                        "Название": j["title"],
                        "Язык": "Русский" if j["language"] == "ru" else "Английский",
                        "Навыков": len(j.get("required_skills") or []),
                        "Создана": j["created_at"],
                    }
                    for j in jobs_list
                ],
                use_container_width=True,
                hide_index=True,
            )

        with st.container(border=True):
            st.markdown("### 📄 Просмотр и удаление")
            job_options = {f"{j['id']} — {j['title']}": j["id"] for j in jobs_list}
            selected_job_label = st.selectbox(
                "Выберите вакансию",
                options=list(job_options.keys()),
                key="jobs_list_select",
            )
            selected_job_id = job_options[selected_job_label]

            try:
                job_detail = fetch_job_detail(selected_job_id)
                with st.expander("📄 Полный текст вакансии", expanded=True):
                    if job_detail.get("required_skills"):
                        st.markdown(
                            "**Ключевые навыки:** "
                            + " ".join(f"`{s}`" for s in job_detail["required_skills"])
                        )
                    st.markdown(
                        f"**Язык:** {'Русский' if job_detail['language'] == 'ru' else 'Английский'}"
                    )
                    st.text(job_detail.get("description", ""))
            except Exception as exc:
                st.error(f"Не удалось загрузить детали вакансии: {exc}")

            confirm_job_delete = st.checkbox(
                "Я понимаю, что это необратимо (удаляются вакансия и история matches)",
                key="job_del_confirm",
            )
            if st.button(
                "🗑 Удалить вакансию",
                disabled=not confirm_job_delete,
                type="primary",
                key="job_del_button",
            ):
                try:
                    if delete_job(selected_job_id):
                        st.toast(f"✅ Вакансия {selected_job_id} удалена.")
                        fetch_jobs.clear()
                        st.rerun()
                    else:
                        st.error("Не удалось удалить вакансию.")
                except Exception as exc:
                    st.error(f"Удаление не удалось: {exc}")


# --- TAB 4: Quarantine ---

with tab_quarantine, st.container(border=True):
    st.markdown("### ⚠ Карантин")
    st.caption("Файлы, которые не удалось обработать корректно — для review рекрутёром.")
    try:
        qrows = fetch_quarantine()
    except Exception as exc:
        st.error(f"Не удалось загрузить quarantine: {exc}")
        qrows = []

    if not qrows:
        st.success("🎉 Quarantine пуст.")
    else:
        st.dataframe(
            [
                {
                    "id": q["id"],
                    "reason": q["reason"],
                    "source_message_id": q["source_message_id"],
                    "file_path": q["file_path"],
                    "created_at": q["created_at"],
                }
                for q in qrows
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Чтобы удалить связанного кандидата (и зачистить quarantine entry) — "
            "таб «Кандидаты» → DELETE."
        )


# --- TAB 5: Кандидаты ---

with tab_candidates:
    try:
        cands = fetch_candidates()
    except Exception as exc:
        st.error(f"Не удалось загрузить кандидатов: {exc}")
        cands = []

    if not cands:
        st.info(
            "Нет кандидатов в БД. Добавьте файлы в `./storage/inbox/` и нажмите "
            "«Синхронизировать почту»."
        )
    else:
        with st.container(border=True):
            st.markdown("### 👤 Список кандидатов")
            st.caption(
                "Удаление каскадно очищает Postgres (matches), Qdrant (embedding), "
                "файл на диске + пишет audit_log."
            )
            st.dataframe(
                [
                    {
                        "id": c["id"],
                        "full_name": c["full_name"],
                        "language": c["language"],
                        "file_path": c["file_path"],
                        "created_at": c["created_at"],
                    }
                    for c in cands
                ],
                use_container_width=True,
                hide_index=True,
            )

        with st.container(border=True):
            st.markdown("### 🗑 Удалить кандидата")
            cand_options = {f"{c['id']} — {c['full_name'] or 'Unknown'}": c["id"] for c in cands}
            selected_label = st.selectbox(
                "Кандидат", options=list(cand_options.keys()), key="del_select"
            )
            confirm = st.checkbox(
                "Я понимаю, что это необратимо (удаляются резюме, эмбеддинги, история matches)",
                key="del_confirm",
            )
            if st.button("Удалить", disabled=not confirm, type="primary"):
                cand_id_to_del = cand_options[selected_label]
                try:
                    if delete_candidate(cand_id_to_del):
                        st.toast(f"✅ Кандидат {cand_id_to_del} удалён.")
                        fetch_candidates.clear()
                        st.rerun()
                    else:
                        st.error("Не удалось удалить.")
                except Exception as exc:
                    st.error(f"Delete failed: {exc}")
