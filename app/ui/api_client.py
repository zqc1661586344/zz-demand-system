"""Gradio HTTP API client — wraps httpx to call FastAPI backend with JWT auth."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "http://localhost:8000"


class ApiClient:
    """HTTP client for FastAPI backend, auto-injects JWT tokens."""

    def __init__(self, access_token: str | None = None, refresh_token: str | None = None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._client = httpx.Client(base_url=BASE_URL, timeout=30.0)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _try_refresh(self) -> bool:
        """Attempt to refresh the access token. Returns True on success."""
        if not self.refresh_token:
            return False
        try:
            resp = self._client.post(
                "/api/auth/refresh",
                json={"refresh_token": self.refresh_token},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                return True
        except Exception:
            logger.exception("Token refresh failed")
        return False

    def request(self, method: str, path: str, **kwargs) -> Any:
        """Make an HTTP request. Auto-retries on 401 after a token refresh."""
        headers = self._headers()
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        resp = self._client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401 and self.refresh_token:
            if self._try_refresh():
                headers = self._headers()
                resp = self._client.request(method, path, headers=headers, **kwargs)

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

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> Any:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> Any:
        return self.request("DELETE", path, **kwargs)

    def upload(self, path: str, files: dict, data: dict | None = None) -> Any:
        """Upload a file via multipart POST."""
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        resp = self._client.post(path, data=data, files=files, headers=headers)

        if resp.status_code == 401 and self.refresh_token:
            if self._try_refresh():
                headers["Authorization"] = f"Bearer {self.access_token}"
                resp = self._client.post(path, data=data, files=files, headers=headers)

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ApiError(resp.status_code, detail)

        return resp.json()


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API {status_code}: {detail}")