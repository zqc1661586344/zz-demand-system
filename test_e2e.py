#!/usr/bin/env python3
"""End-to-end smoke test for the Enterprise RAG System.

Tests: register → login → /me → upload doc → poll indexed → query RAG
Usage: python test_e2e.py
"""

import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8001/api"


def req(method, path, data=None, token=None):
    url = f"{BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            pass
        print(f"  ERROR {e.code} {method} {path}: {detail}")
        return e.code, detail


def main():
    username = f"testuser_{int(time.time())}"
    password = "Test@1234"
    email = f"{username}@example.com"

    # 1. Register
    print(f"=== 1. Register user: {username} ===")
    status, data = req(
        "POST",
        "/auth/register",
        {"username": username, "password": password, "email": email, "full_name": "Test User"},
    )
    assert status == 201, f"Register failed: {data}"
    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    print(f"  ✓ Registered, got access_token ({len(access_token)} chars)")

    # 2. Login
    print("\n=== 2. Login ===")
    status, data = req("POST", "/auth/login", {"username": username, "password": password})
    assert status == 200, f"Login failed: {data}"
    access_token = data["access_token"]
    print(f"  ✓ Login OK")

    # 3. GET /me
    print("\n=== 3. GET /me ===")
    status, data = req("GET", "/auth/me", token=access_token)
    assert status == 200, f"/me failed: {data}"
    assert data["username"] == username, f"Wrong username: {data}"
    assert "roles" in data, f"No roles in response: {data}"
    print(f"  ✓ /me OK — username={data['username']}, roles={data['roles']}")

    # 4. Upload a text document
    print("\n=== 4. Upload document ===")
    # Use multipart upload via urllib
    boundary = "----TestBoundary123"
    doc_content = (
        "Enterprise RAG System Overview\n"
        "=============================\n\n"
        "The Enterprise RAG System is a knowledge base management platform "
        "that enables organizations to store, manage, and query their internal "
        "documents using Retrieval-Augmented Generation (RAG) technology.\n\n"
        "Key Features:\n"
        "- Multi-user support with role-based access control\n"
        "- Knowledge base management with document versioning\n"
        "- RAG-powered question answering with source citations\n"
        "- Support for PDF, TXT, Markdown, and DOCX files\n"
        "- Scalable architecture supporting both cloud and local LLMs\n\n"
        "Architecture:\n"
        "The system uses FastAPI for the backend API, SQLAlchemy for database "
        "management, Chroma for vector storage, and supports both OpenAI and "
        "Ollama for embeddings and LLM inference.\n\n"
        "The document processing pipeline includes parsing, text chunking "
        "(with configurable chunk size and overlap), embedding generation, "
        "and vector storage indexing.\n"
    )
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="rag_overview.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
        f"{doc_content}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    url = f"{BASE}/documents/upload"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    r = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(r) as resp:
            data = json.loads(resp.read().decode())
            doc_id = data["id"]
            print(f"  ✓ Document uploaded: id={doc_id}, status={data['status']}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            pass
        print(f"  ERROR {e.code} upload: {detail}")
        sys.exit(1)

    # 5. Poll for document to be indexed
    print("\n=== 5. Wait for document processing ===")
    for i in range(30):
        time.sleep(2)
        status, data = req("GET", f"/documents/{doc_id}", token=access_token)
        if status == 200:
            doc_status = data["status"]
            print(f"  Poll {i + 1}: status={doc_status}, chunk_count={data.get('chunk_count', 0)}")
            if doc_status == "indexed":
                print(f"  ✓ Document indexed with {data['chunk_count']} chunks!")
                break
            elif doc_status == "failed":
                print(f"  ✗ Document processing failed: {data.get('error_message', 'unknown')}")
                sys.exit(1)
        else:
            print(f"  Poll {i + 1}: HTTP {status}")
    else:
        print("  ✗ Timeout waiting for indexing")
        sys.exit(1)

    # 6. Create conversation
    print("\n=== 6. Create conversation ===")
    status, data = req(
        "POST", "/conversations", {"title": "E2E Test Conversation"}, token=access_token
    )
    assert status == 201, f"Create conversation failed: {data}"
    conv_id = data["id"]
    print(f"  ✓ Conversation created: id={conv_id}")

    # 7. RAG Query
    print("\n=== 7. RAG Query ===")
    status, data = req(
        "POST",
        f"/conversations/{conv_id}/query",
        {"query": "What are the key features of the Enterprise RAG System?"},
        token=access_token,
    )
    assert status == 200, f"Query failed: {data}"
    print(f"  Answer: {data['answer'][:200]}...")
    if data["sources"]:
        for s in data["sources"]:
            print(f"  Source: {s}")
    else:
        print("  (no sources returned)")
    print("  ✓ RAG query completed!")

    # 8. Get messages
    print("\n=== 8. Get messages ===")
    status, data = req("GET", f"/conversations/{conv_id}/messages", token=access_token)
    assert status == 200, f"Get messages failed: {data}"
    print(f"  ✓ {len(data)} messages retrieved")

    # 9. Health check
    print("\n=== 9. Health check ===")
    with urllib.request.urlopen("http://localhost:8001/api/health") as resp:
        data = json.loads(resp.read().decode())
        assert data["status"] == "ok", f"Health check failed: {data}"
        print(f"  ✓ Health OK: {data}")

    print("\n" + "=" * 50)
    print("ALL E2E TESTS PASSED! 🎉")
    print("=" * 50)


if __name__ == "__main__":
    main()
