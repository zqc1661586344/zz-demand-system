"""Auth helpers for Streamlit — login, register, logout, and token management.

替代旧的 auto_login（硬编码 admin/admin123）：
- 凭据由登录/注册表单录入，不再写死超管账号。
- Token 同时存 st.session_state（当前会话）与浏览器 localStorage（刷新/关标签后恢复），
  不再"刷新需重登"。
- ApiClient（api_client.py）读取同一套 session 字段自动注入 Bearer 并在 401 时 refresh，
  这里不重复实现网络层。
"""

import json

import httpx
import streamlit as st

from app.logging_config import get_logger
from app.streamlit_app.api_client import BASE_URL

logger = get_logger(__name__)

# session_state 中受认证管控的键，login/logout 共同维护
_AUTH_KEYS = ("access_token", "refresh_token", "user_info", "username", "authenticated")

# 浏览器 localStorage 中保存 token 对的键名：刷新 / 关标签 / 新开标签后静默恢复登录用。
# 写入与读取都走 st.html + JS（st.html 非 iframe、同源渲染，可直接访问 localStorage）。
_LOCAL_STORAGE_KEY = "rag_auth_tokens"
# URL 中一次性承载 refresh token 的参数名：JS 读到 localStorage 后注入 URL 触发一次重载，
# Python 侧下一轮脚本运行中读到该参数完成恢复，成功后立即清除（不常驻地址栏）。
_RESTORE_PARAM = "rt"
# "显式退出"标记（URL query param 与 session_state 共用此键）：logout 时同步写入（纯 Python，
# 无 JS 竞态），恢复路径最先检查它，命中则不恢复、直接显示登录页。登录成功时清除。
_LOGGED_OUT = "logged_out"


def _set_auth_session(data: dict, user_info: dict | None = None) -> None:
    """把登录成功后的令牌与用户信息写入 session_state，并落地到 localStorage。

    st.html 是非 iframe、同源渲染（由当前 Streamlit 版本实现保证），注入的 JS
    可直接访问浏览器 localStorage，从而让 F5 / 关标签 / 新开标签后能静默恢复登录。

    登录成功即清除\"显式退出\"标记：否则一旦退过出，即便再重新登录也会被恢复路径拦下。
    """
    # 重新授权成功 → 清除退出标记（URL 与 session_state 各一份）
    st.session_state.pop(_LOGGED_OUT, None)
    st.query_params.pop(_LOGGED_OUT, None)

    st.session_state["access_token"] = data["access_token"]
    st.session_state["refresh_token"] = data["refresh_token"]
    st.session_state["user_info"] = user_info or {}
    st.session_state["username"] = (user_info or {}).get("username", "")
    st.session_state["authenticated"] = True

    # 把 token 对写入 localStorage，供刷新后恢复（仅写入，读回走 restore_session_from_local_storage）
    _persist_to_local_storage(data["access_token"], data["refresh_token"])


def _persist_to_local_storage(access_token: str, refresh_token: str) -> None:
    """通过 st.html 注入 JS，把 token 对写入浏览器 localStorage。

    注意：当前会显式渲染出一块 HTML 占位。为避免每帧都重跑这段 JS、也避免刷新后
    再次注入把已恢复 session_state 覆盖，这里只负责写入，读取逻辑独立在
    restore_session_from_local_storage 中，且仅在未认证时才触发恢复。
    """
    js = (
        "<script>"
        f"localStorage.setItem('{_LOCAL_STORAGE_KEY}', JSON.stringify("
        f"{{access_token: {json.dumps(access_token)}, refresh_token: {json.dumps(refresh_token)}}}));"
        "</script>"
        '<div style="display:none"></div>'
    )
    st.html(js, unsafe_allow_javascript=True)


def _storage_html() -> str:
    """返回一段注入 JS 的 HTML：若 localStorage 存有 token 且 URL 无 rt 参数，
    则把 refresh_token 一次性注入 URL 的 ?rt= 参数并触发一次页面重载。

    重载后 Python 侧读到 ?rt= 再由 _restore_to_session 回调后端 /refresh 完成恢复。
    """
    return (
        "<script>"
        "function writeState(){try{"
        f"var k='{_LOCAL_STORAGE_KEY}';"
        "var raw=localStorage.getItem(k);"
        "if(!raw)return;"
        "if(!location.search.includes('rt=')){"
        "var p=JSON.parse(raw);"
        "var u=new URL(location.href);"
        "u.searchParams.set('rt',p.refresh_token||'');"
        "location.replace(u.toString());"
        "}"
        "}catch(e){}}"
        "writeState();"
        "</script>"
        '<div style="display:none"></div>'
    )


def restore_session_from_local_storage() -> bool:
    """应用启动入口调用：尝试从 localStorage 静默恢复登录态。

    前置条件：session_state 中 authenticated 为假（未登录/刷新后丢失）。
    流程：
      0. 若\"显式退出\"标记存在（URL 或 session_state）→ 拒绝恢复（返回 False），
         并趁机用 JS 清除 localStorage 里的残留 token；
      1. 否则：URL 若无 rt，注入 JS 把 localStorage 里的 refresh_token 写进 URL ?rt= 并重载；
      2. 重载后本函数再次运行：从 ?rt= 读到 refresh_token，调后端 /api/auth/refresh
         换新 token 对写入 session_state（并回写 localStorage），置 authenticated=True。

    返回 True 表示已恢复，False 表示无可用 token（应显示登录页）。注意：恢复成功
    时必须补拉 user_info，否则 username 为空、侧栏显示残缺（\"***\"）。
    """
    # 步骤0：显式退出过 → 拒绝自动恢复（URL 标记对 F5/新标签的\"新会话\"同样生效）
    if st.query_params.get(_LOGGED_OUT) == "1" or st.session_state.get(_LOGGED_OUT):
        logger.info("restore skipped: user logged out")
        _clear_local_storage()  # 清除 localStorage 残留 token，防止换 URL 后恢复
        return False

    # 步骤1：URL 里还没有 rt，先注入 JS 触发一次"读 localStorage -> 写 URL -> 重载"
    rt = st.query_params.get(_RESTORE_PARAM)
    if not rt:
        st.html(_storage_html(), unsafe_allow_javascript=True)
        return False

    # 步骤2：URL 里已有 rt，用它在 Python 侧换新 token 恢复登录
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.post(
                "/api/auth/refresh",
                json={"refresh_token": rt},
            )
            if resp.status_code != 200:
                logger.info("restore refresh failed: status=%s", resp.status_code)
                st.query_params.pop(_RESTORE_PARAM, None)
                return False
            data = resp.json()
    except Exception as e:
        logger.warning("restore refresh exception: %s", e)
        st.query_params.pop(_RESTORE_PARAM, None)
        return False

    # 补拉用户信息：恢复路径此前漏掉这步，导致 username 为空、侧栏显示残缺
    user_info = _fetch_me(data["access_token"])
    _set_auth_session(data, user_info)
    # 恢复成功后立即清除 URL 上的敏感 token，避免常驻地址栏
    st.query_params.pop(_RESTORE_PARAM, None)
    logger.info("session restored from localStorage for user")
    return True


def _fetch_me(token: str) -> dict:
    """用 access token 拉取当前用户信息（/api/auth/me）。

    失败（非 200、JSON 解析失败、网络异常）返回空 dict，**不向上抛异常**，
    避免让一次本该成功的登录/注册因"拉 user_info"这个小步骤失败而被误判为整体失败。
    """
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                logger.info("fetch /me failed: status=%s", resp.status_code)
                return {}
            return resp.json()
    except Exception as e:
        # 网络异常 / JSON 解析失败：仅记录，不阻断登录
        logger.warning("fetch /me failed: %s", e)
        return {}


def login(username: str, password: str) -> bool:
    """用户名密码登录；成功写会话态返回 True，凭据错误返回 False。"""
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.post(
                "/api/auth/login",
                json={"username": username, "password": password},
            )
            if resp.status_code != 200:
                logger.info("login failed for %r: %s", username, resp.status_code)
                return False
            data = resp.json()
            user_info = _fetch_me(data["access_token"])
        _set_auth_session(data, user_info)
        logger.info("user logged in: %s", username)
        return True
    except Exception as e:
        logger.error("login exception: %s", e)
        return False


def register(username: str, password: str, email: str = "", full_name: str = "") -> bool:
    """注册新用户；成功后自动登录并写会话态。

    后端 /api/auth/register 返回 TokenResponse（无 user_info），故写态后补一次 /me。
    """
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as http:
            resp = http.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "password": password,
                    "email": email,
                    "full_name": full_name,
                },
            )
            if resp.status_code != 201:
                # 透出后端 detail（如 "Username already exists"）供前端展示
                try:
                    raw = resp.json().get("detail", resp.text)
                    detail = raw if isinstance(raw, str) else str(raw)  # 422 的 detail 是列表
                except Exception:
                    detail = resp.text
                st.session_state["register_error"] = detail
                logger.info("register failed for %r: %s %s", username, resp.status_code, detail)
                return False
            data = resp.json()
            user_info = _fetch_me(data["access_token"])
        _set_auth_session(data, user_info)
        logger.info("user registered & logged in: %s", username)
        return True
    except Exception as e:
        logger.error("register exception: %s", e)
        st.session_state["register_error"] = "注册失败，请稍后再试"
        return False


def logout() -> None:
    """退出登录：清除会话态、URL 上的恢复 token，并写"显式退出"标记。

    只清 session_state 不够：若 localStorage 里还残留 refresh_token，刷新后
    restore_session_from_local_storage 会把用户静默恢复登录，等于\"退出无效\"。
    故这里注入 JS 同时删除 localStorage 键。
    """
    for key in _AUTH_KEYS:
        st.session_state.pop(key, None)
    st.session_state.pop("register_error", None)
    st.session_state[_LOGGED_OUT] = True
    st.query_params.pop(_RESTORE_PARAM, None)
    st.query_params[_LOGGED_OUT] = "1"
    _clear_local_storage()
    logger.info("user logged out")


def _clear_local_storage() -> None:
    """注入 JS 删除 localStorage 中的 token 键。"""
    js = (
        "<script>"
        f"localStorage.removeItem('{_LOCAL_STORAGE_KEY}');"
        "</script>"
        '<div style="display:none"></div>'
    )
    st.html(js, unsafe_allow_javascript=True)
