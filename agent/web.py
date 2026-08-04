"""SFF 2026 議程問答 —— 最小 Web UI（Gradio）。

    python3 -m agent.web        然後開 http://127.0.0.1:7860

跟 cli.py 共用同一個 build_agent() / ask()，這裡只負責介面。

要對外開放時不必改程式，Gradio 自己就吃這幾個環境變數：
GRADIO_SERVER_NAME（預設 127.0.0.1，內網分享設 0.0.0.0）、GRADIO_SERVER_PORT、
GRADIO_SHARE=True（開臨時公開網址；會把服務暴露到公網，確定要再開）。

議程網頁 index.html 要接的是 api.py，不是這一支。
"""

from __future__ import annotations

import uuid

import gradio as gr

if __package__:  # 見 rag.py 的說明
    from .core import ask, build_agent, load_env, require_api_key
else:
    from core import ask, build_agent, load_env, require_api_key

INTRO = """## SFF 2026 議程小幫手

問我對哪個主題有興趣、有哪些場次，或某一場在講什麼。中英文皆可。
"""


def main() -> None:
    load_env()
    require_api_key()

    # 工具全是唯讀的，所以整個 server 共用一個 agent；對話由 thread_id 隔開。
    agent, status = build_agent()
    print(status)

    def respond(message: str, history, name: str, browser_session: str) -> str:
        # 填了名字就用名字當 thread_id，沒填就用這個瀏覽器分頁自己的 uuid。
        # 這條 thread_id 通道就是之後「多人共看行程」要接上去的地方。
        return ask(agent, message, (name or "").strip() or browser_session)

    with gr.Blocks(title="SFF 2026 議程小幫手") as demo:
        gr.Markdown(INTRO)
        browser_session = gr.State(lambda: uuid.uuid4().hex)
        name = gr.Textbox(
            label="你的名字 / Your name",
            placeholder="留白也可以；填了之後同名的人會共用一段對話記憶",
        )
        gr.ChatInterface(fn=respond, additional_inputs=[name, browser_session])

    demo.launch()  # 主機／連接埠／share 都走 GRADIO_* 環境變數（見檔頭）


if __name__ == "__main__":
    main()
