"""Document management Gradio page."""

import os
import tempfile

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

    def upload_file(file: tempfile.NamedTemporaryFile, auth_json: str) -> str:
        if not file:
            return "Please select a file"
        client = make_api_client(auth_json)
        try:
            with open(file, "rb") as f:
                client.upload(
                    "/api/documents/upload",
                    files={"file": (os.path.basename(file), f, "application/octet-stream")},
                )
            return f"✅ File uploaded and processing started"
        except ApiError as e:
            return f"❌ {e.detail}"

    with gr.Column(scale=1):
        gr.Markdown("## 📁 文档管理")

        with gr.Row():
            with gr.Column(scale=2):
                doc_table = gr.Dataframe(
                    headers=["文件名", "状态", "大小", "Chunks", "上传时间"],
                    label="文档列表",
                    interactive=False,
                    wrap=True,
                )
            with gr.Column(scale=1):
                gr.Markdown("### 上传文档")
                file_input = gr.File(label="选择文件", file_types=[".pdf", ".txt", ".md", ".docx"])
                upload_btn = gr.Button("上传", variant="primary")
                upload_msg = gr.Textbox(label="", interactive=False)

        # Events
        def on_page_load(auth_json: str):
            return load_docs(auth_json)

        gr.on(
            triggers=[auth_state.change],
            fn=on_page_load,
            inputs=[auth_state],
            outputs=[doc_table],
        )

        upload_btn.click(
            fn=upload_file,
            inputs=[file_input, auth_state],
            outputs=[upload_msg],
        ).then(
            fn=on_page_load,
            inputs=[auth_state],
            outputs=[doc_table],
        )

    return doc_table