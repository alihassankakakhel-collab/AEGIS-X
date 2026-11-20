from __future__ import annotations

import hmac

import streamlit as st


def password_matches(candidate: str, expected: str) -> bool:
    return bool(candidate and expected) and hmac.compare_digest(candidate, expected)


def authenticate_operator(expected_password: str | None) -> bool:
    if not expected_password:
        st.error(
            "Operator login is not configured. Add AEGIS_OPERATOR_PASSWORD to "
            "your local .env file, then restart the app."
        )
        return False

    if st.session_state.get("operator_authenticated"):
        return True

    st.title("AEGIS X operator sign-in")
    st.caption("Local single-operator demonstration authentication.")
    with st.form("operator-login"):
        operator_name = st.text_input("Operator name", value="Operator")
        password = st.text_input("Operator password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        if not operator_name.strip():
            st.error("Enter an operator name.")
        elif password_matches(password, expected_password):
            st.session_state["operator_authenticated"] = True
            st.session_state["operator_name"] = operator_name.strip()
            st.rerun()
        else:
            st.error("Incorrect operator password.")
    return False
