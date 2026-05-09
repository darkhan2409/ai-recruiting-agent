"""Заглушка Streamlit UI для БЛОКа 1.

Реальная UI (поиск кандидатов, expand-карточки, quarantine review) —
БЛОК 7. Здесь только то, что нужно, чтобы контейнер `streamlit` поднялся
зелёным в `docker compose up`.
"""

import streamlit as st

st.set_page_config(page_title="HCB Recruiting Agent", layout="wide")
st.title("HCB Recruiting Agent")
st.info("UI будет доступен в БЛОКе 7. Сейчас контейнер используется только как placeholder.")
