import streamlit as st

from config import settings
from database.connection import init_database
from modules.authentication import authenticate_operator
from modules.dashboard import render_dashboard


st.set_page_config(
    page_title=settings.app_name,
    page_icon="A",
    layout="wide",
)


def main() -> None:
    if not authenticate_operator(settings.operator_password):
        st.stop()
    init_database(settings.database_path)
    render_dashboard()


if __name__ == "__main__":
    main()
