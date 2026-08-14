"""Workflow management Gradio page."""

import gradio as gr

from app.ui.api_client import ApiError
from app.ui.auth_helpers import make_api_client


def render(auth_state: gr.State) -> gr.Blocks:
    """Render the workflow management page."""

    def load_definitions(auth_json: str) -> list[list]:
        client = make_api_client(auth_json)
        try:
            defs = client.get("/api/workflows/definitions")
            return [[d["name"], d.get("description", "") or "", d["version"], "✅" if d["is_active"] == "true" else "❌"] for d in defs]
        except ApiError:
            return []

    def load_instances(auth_json: str) -> list[list]:
        client = make_api_client(auth_json)
        try:
            insts = client.get("/api/workflows/instances")
            return [
                [i["id"][:8], i["status"], i["created_at"][:19], i.get("completed_at", "")[:19] if i.get("completed_at") else ""]
                for i in insts
            ]
        except ApiError:
            return []

    def create_definition(name: str, desc: str, config: str, auth_json: str) -> str:
        client = make_api_client(auth_json)
        try:
            client.post("/api/workflows/definitions", json={
                "name": name,
                "description": desc or None,
                "config": config or None,
            })
            return f"✅ Workflow '{name}' created"
        except ApiError as e:
            return f"❌ {e.detail}"

    def create_instance(def_name: str, input_data: str, auth_json: str) -> str:
        client = make_api_client(auth_json)
        try:
            defs = client.get("/api/workflows/definitions")
            target = next((d for d in defs if d["name"] == def_name), None)
            if target:
                client.post("/api/workflows/instances", json={
                    "definition_id": target["id"],
                    "input_data": input_data or None,
                })
                return f"✅ Instance created for '{def_name}'"
            return "❌ Workflow definition not found"
        except ApiError as e:
            return f"❌ {e.detail}"

    with gr.Column(scale=1):
        gr.Markdown("## 🔄 业务流程")

        with gr.Tabs():
            with gr.TabItem("流程定义"):
                with gr.Row():
                    with gr.Column(scale=2):
                        def_table = gr.Dataframe(
                            headers=["名称", "描述", "版本", "活跃"],
                            label="流程定义列表",
                            interactive=False,
                        )
                    with gr.Column(scale=1):
                        gr.Markdown("### 新建定义")
                        def_name = gr.Textbox(label="名称", placeholder="Workflow name")
                        def_desc = gr.Textbox(label="描述", placeholder="Description", lines=2)
                        def_config = gr.Textbox(label="配置 (JSON)", placeholder='{"steps": []}', lines=3)
                        create_def_btn = gr.Button("创建", variant="primary")
                        create_def_msg = gr.Textbox(label="", interactive=False)

            with gr.TabItem("流程实例"):
                with gr.Row():
                    with gr.Column(scale=2):
                        inst_table = gr.Dataframe(
                            headers=["ID", "状态", "创建时间", "完成时间"],
                            label="流程实例列表",
                            interactive=False,
                        )
                    with gr.Column(scale=1):
                        gr.Markdown("### 新建实例")
                        inst_def = gr.Textbox(label="流程定义名称", placeholder="Definition name")
                        inst_input = gr.Textbox(label="输入数据 (JSON)", placeholder='{"key": "value"}', lines=3)
                        create_inst_btn = gr.Button("创建实例", variant="primary")
                        create_inst_msg = gr.Textbox(label="", interactive=False)

        # Events
        gr.on(
            triggers=[auth_state.change],
            fn=lambda aj: (load_definitions(aj), load_instances(aj)),
            inputs=[auth_state],
            outputs=[def_table, inst_table],
        )

        create_def_btn.click(
            fn=create_definition,
            inputs=[def_name, def_desc, def_config, auth_state],
            outputs=[create_def_msg],
        ).then(
            fn=load_definitions,
            inputs=[auth_state],
            outputs=[def_table],
        )

        create_inst_btn.click(
            fn=create_instance,
            inputs=[inst_def, inst_input, auth_state],
            outputs=[create_inst_msg],
        ).then(
            fn=load_instances,
            inputs=[auth_state],
            outputs=[inst_table],
        )

    return def_table