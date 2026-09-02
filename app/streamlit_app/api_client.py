"""Sync HTTP API client — wraps httpx to call FastAPI backend with JWT auth.

Streamlit runs in a sync context, so this uses httpx.Client (sync) instead of
httpx.AsyncClient. Token management uses st.session_state.

Single source of truth for BASE_URL — every frontend component reads from here.
"""

import json
from typing import Any

import httpx
import streamlit as st

from app.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "http://localhost:8001"


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API {status_code}: {detail}")


def _get_tokens() -> tuple[str | None, str | None]:
    """Read JWT tokens from Streamlit session state."""
    return (
        st.session_state.get("access_token"),
        st.session_state.get("refresh_token"),
    )


def _headers(access_token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _try_refresh(client: httpx.Client, refresh_token: str | None) -> bool:
    """Attempt to refresh the access token. Updates session state on success."""
    if not refresh_token:
        return False
    try:
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        if resp.status_code == 200:
            data = resp.json()
            st.session_state["access_token"] = data["access_token"]
            st.session_state["refresh_token"] = data["refresh_token"]
            return True
    except Exception:
        logger.exception("Token refresh failed")
    return False


def request(method: str, path: str, **kwargs) -> Any:
    """Make a sync HTTP request. Auto-retries on 401 after a token refresh."""
    access_token, refresh_token = _get_tokens()

    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        headers = _headers(access_token)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        resp = client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401 and refresh_token:
            if _try_refresh(client, refresh_token):
                access_token, _ = _get_tokens()
                headers = _headers(access_token)
                resp = client.request(method, path, headers=headers, **kwargs)

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ApiError(resp.status_code, detail)

        try:
            return resp.json()
        except Exception:
            return resp.text


def get(path: str, **kwargs) -> Any:
    return request("GET", path, **kwargs)


def post(path: str, **kwargs) -> Any:
    return request("POST", path, **kwargs)


def put(path: str, **kwargs) -> Any:
    return request("PUT", path, **kwargs)


def delete(path: str, **kwargs) -> Any:
    return request("DELETE", path, **kwargs)


def download_bytes(path: str) -> bytes:
    """GET 请求并返回原始 bytes，用于文件下载（报告、导出等）。"""
    access_token, refresh_token = _get_tokens()
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as client:
        headers = _headers(access_token)
        resp = client.get(path, headers=headers)
        if resp.status_code == 401 and refresh_token:
            if _try_refresh(client, refresh_token):
                access_token, _ = _get_tokens()
                headers = _headers(access_token)
                resp = client.get(path, headers=headers)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ApiError(resp.status_code, detail)
        return resp.content


def upload(path: str, file_path: str, filename: str, mime_type: str) -> Any:
    """Upload a file via multipart POST.

    Reads the file from disk (the path returned by st.file_uploader's temp file).
    """
    access_token, refresh_token = _get_tokens()

    with httpx.Client(base_url=BASE_URL, timeout=120.0) as client:
        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        with open(file_path, "rb") as f:
            files = {"file": (filename, f, mime_type)}
            resp = client.post(path, files=files, headers=headers)

            if resp.status_code == 401 and refresh_token:
                if _try_refresh(client, refresh_token):
                    access_token, _ = _get_tokens()
                    headers["Authorization"] = f"Bearer {access_token}"
                    f.seek(0)
                    resp = client.post(path, files=files, headers=headers)

            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise ApiError(resp.status_code, detail)

            return resp.json()
