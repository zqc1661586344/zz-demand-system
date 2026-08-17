"""Document management Gradio page."""

import mimetypes
import os

import gradio as gr

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client


def render(auth_state: gr.State) -> gr.Blocks:
    """Render the document management page."""

    def load_docs(auth_json: str) -> list[list]:
        client = make_api_client(auth_json)
        try:
            docs = client.get("/api/documents")
            return [
                [doc["original_filename"], doc["status"], doc["file_size"], doc.get("chunk_count", "") or "", doc["created_at"][:19]]
                for doc in docs
            ]
        except ApiError:
            return []

    def upload_file(filedata, auth_json: str) -> tuple:
        """Upload immediately when a file is selected. Returns (status_msg, updated_table)."""
        if filedata is None:
            return "Please select a file", []

        # Gradio 5.x may pass a string path or a FileData object
        if isinstance(filedata, str):
            file_path = filedata
            orig_name = os.path.basename(filedata)
            mime_type, _ = mimetypes.guess_type(orig_name)
        else:
            file_path = getattr(filedata, 'path', None) or getattr(filedata, 'name', None)
            orig_name = getattr(filedata, 'orig_name', None) or os.path.basename(file_path or "")
            mime_type = getattr(filedata, 'mime_type', None)
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(orig_name)

        if not file_path or not os.path.exists(file_path):
            return "Temporary file not found (Gradio cleanup)", []

        if not mime_type:
            mime_type = "application/octet-stream"

        client = make_api_client(auth_json)
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            client.upload(
                "/api/documents/upload",
                files={"file": (orig_name, file_bytes, mime_type)},
            )
            msg = "✅ File uploaded and processing started"
        except ApiError as e:
            msg = f"❌ {e.detail}"

        # Refresh table
        try:
            docs = client.get("/api/documents")
            table = [
                [doc["original_filename"], doc["status"], doc["file_size"], doc.get("chunk_count", "") or "", doc["created_at"][:19]]
                for doc in docs
            ]
        except ApiError:
            table = []
        return msg, table

    with gr.Column(scale=1):
        # Page header
        gr.Markdown("## 📁 文档管理", elem_classes="page-header")

        with gr.Row(elem_classes="doc-content"):
            with gr.Column(scale=2):
                doc_table = gr.Dataframe(
                    headers=["文件名", "状态", "大小", "Chunks", "上传时间"],
                    label="文档列表",
                    interactive=False,
                    wrap=True,
                    elem_classes="doc-table",
                )
            with gr.Column(scale=1):
                with gr.Column(elem_classes="doc-upload"):
                    gr.Markdown("### 上传文档")
                    file_input = gr.UploadButton("📎 选择文件上传", file_types=[".pdf", ".txt", ".md", ".docx"], variant="primary")

        upload_msg = gr.Textbox(label="", interactive=False, elem_classes="doc-msg")

        # Events
        def on_page_load(auth_json: str):
            return load_docs(auth_json)

        gr.on(
            triggers=[auth_state.change],
            fn=on_page_load,
            inputs=[auth_state],
            outputs=[doc_table],
        )

        # Upload immediately when file is selected
        file_input.upload(
            fn=upload_file,
            inputs=[file_input, auth_state],
            outputs=[upload_msg, doc_table],
        )

    return doc_table