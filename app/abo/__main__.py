"""Run the Windows console against the local or remote ABO application."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.abo.service import AboDialogue, bind_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", choices=("check","chat","ask","cloud-chat","serve"), default="chat")
    parser.add_argument("--question")
    parser.add_argument("--gpu",action="store_true")
    parser.add_argument("--model-dir",type=Path)
    args = parser.parse_args()
    workspace = bind_runtime()
    if args.command=="cloud-chat":
        import sys
        from kaggle_chat_client import main as remote

        sys.argv = [sys.argv[0],*(["--question",args.question] if args.question else [])]
        return remote()
    if args.command=="serve":
        if args.gpu:
            parser.error("使用 ABO_GPU=1 配置服务器的 GPU。")
        import uvicorn

        uvicorn.run("app.asgi:app",host="127.0.0.1",port=8769)
        return 0
    if args.command=="check":
        from full_retrieval import FullRetriever

        retriever = FullRetriever()
        try:
            manifest = retriever.manifest
            print(json.dumps({"records":manifest["records"],"categories":len(manifest["product_types"]),
                              "sites":len(manifest["domains"]),"profile":"abo-adaptive-rag"}))
        finally:
            retriever.close()
        return 0
    if args.command=="ask" and not args.question:
        parser.error("ask 需要 --question。")
    output = workspace / "reports/ecommerce_rag" / ("abo-adaptive-chat-"+uuid4().hex[:10])
    output.mkdir(parents=True)
    session = AboDialogue(output,gpu=args.gpu,model_dir=args.model_dir)
    try:
        def ask(question):
            row = session.respond(question)
            print("助手："+row["display_answer"])
            for label in dict.fromkeys(source["label"] for source in row["display_sources"]):
                print("来源 "+label)
            return row["decision"]

        if args.command=="ask":
            ask(args.question)
        else:
            print("ABO 商品助手已启动，可查询商品或输入「退出」。")
            while True:
                try:
                    question = input("你：").strip()
                except (EOFError,KeyboardInterrupt):
                    break
                if question and ask(question)=="exit":
                    break
    finally:
        session.close()
        print("逐题记录 "+str(output/"逐题结果.txt"))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
