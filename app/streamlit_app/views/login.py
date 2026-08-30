"""Login/Register page — credential entry, no hardcoded admin auto-login."""

import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.auth import login, register

logger = get_logger(__name__)


def page() -> None:
    """渲染登录 + 注册双 tab 表单。成功登录取门后由入口 st.rerun() 进入业务页。

    注意：不调用 st.set_page_config —— 该命令已由 app.py::main() 统一设置，
    且 Streamlit 要求其必须是每个 session 首个 Streamlit 命令，二次调用会抛错。
    """
    st.title("🔐 企业 RAG 文档问答系统")
    st.caption("请输入账号登录，或注册新账号（注册用户默认 viewer 角色）")

    tab_login, tab_register = st.tabs(["登录", "注册"])

    with tab_login:
        username = st.text_input("用户名", key="login_username")
        password = st.text_input("密码", type="password", key="login_password")
        if st.button("登录", type="primary", use_container_width=True):
            if not username or not password:
                st.error("请输入用户名和密码")
            elif login(username, password):
                st.session_state.pop("register_error", None)
                st.success("登录成功")
                st.rerun()
            else:
                st.error("用户名或密码错误")

    with tab_register:
        reg_username = st.text_input("用户名", key="reg_username")
        reg_email = st.text_input("邮箱", key="reg_email")
        reg_full_name = st.text_input("姓名（可选）", key="reg_full_name")
        reg_password = st.text_input("密码", type="password", key="reg_password")
        reg_confirm = st.text_input("确认密码", type="password", key="reg_confirm")

        # 展示上一次注册失败的后端 detail（如 "Username already exists"）
        if st.session_state.get("register_error"):
            st.error(st.session_state.pop("register_error"))

        if st.button("注册", use_container_width=True):
            if not reg_username or not reg_password:
                st.error("用户名和密码为必填项")
            elif reg_password != reg_confirm:
                st.error("两次输入的密码不一致")
            elif register(reg_username, reg_password, reg_email, reg_full_name):
                st.success("注册成功，已自动登录")
                st.rerun()