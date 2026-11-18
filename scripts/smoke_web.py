"""Exercise the first-download ABO web server over HTTP on a prepared checkout."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def exercise(port):
    def get(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        with urlopen(
            Request(
                f"http://127.0.0.1:{port}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
            ),
            timeout=60,
        ) as response:
            return json.load(response) if path.startswith("/api/") else response.read().decode()

    ready_by = monotonic() + 45
    while True:
        try:
            health = get("/api/v1/health")
            break
        except URLError:
            if monotonic() > ready_by:
                raise
            sleep(0.25)

    assert health["status"] == "ready" and not health["gpu"], health
    assert health["catalog"] == {"records": 147702, "categories": 576, "marketplaces": 32}, health
    assert "browse-grid" in get("/") and "product-dialog" in get("/")
    assert "browse-grid" in get("/web-assets/styles.css")
    assert "/catalog/categories" in get("/web-assets/chat.js")
    categories = get("/api/v1/catalog/categories")["items"]
    assert len(categories) == 576 and sum(item["total"] for item in categories) == 147702
    assert [item["key"] for item in categories if not item["image"]] == ["HAIRBAND"]
    shoe_pages = "/api/v1/catalog/categories/SHOES/products?limit=3"
    first = get(shoe_pages)
    second = get(f'{shoe_pages}&after={first["next_after"]}')
    assert (
        first["category"]["total"] == 12965 and first["items"][-1]["id"] < second["items"][0]["id"]
    )
    no_image = get("/api/v1/catalog/categories/HAIRBAND/products")["items"][0]
    assert no_image["image"] is None
    treadmill = get("/api/v1/catalog/categories/TREADMILL/products")["items"][0]
    assert "/images/small/" in treadmill["image"]
    detail = get(f'/api/v1/catalog/products/{first["items"][0]["id"]}')
    assert detail["fields"] and detail["archive"].endswith(".json.gz") and detail["line"] > 0
    assert (
        detail["title"] == first["items"][0]["title"]
        and "/images/original/" in detail["original_image"]
    )

    def ask(question, previous=None):
        row = get(
            "/api/v1/chat",
            {"question": question, "session_id": previous and previous["session_id"]},
        )
        print(
            json.dumps(
                {
                    "question": question,
                    "action": row["action"],
                    "photos": len(row["products"]),
                    "run": row["run_name"],
                    "question_id": row["question_id"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return row

    count = ask("手表类商品有多少个？")
    assert (
        count["action"] == "catalog_count" and "28 条" in count["answer"] and not count["products"]
    ), count
    search = ask("搜索红色高跟鞋")
    assert search["action"] == "search_results" and search["products"], search
    followup = ask("第三款，给我看看它的属性", search)
    assert (
        followup["sources"] and not followup["products"] and "颜色" in followup["answer"]
    ), followup
    photo = ask("看看这款的图片", followup)
    assert (
        len(photo["products"]) == 1 and photo["products"][0]["id"] == search["products"][2]["id"]
    ), photo
    named_photos = ask("有哪些电风扇，给我看看图片")
    assert named_photos["action"] == "search_results" and named_photos["products"], named_photos
    unrelated_photos = ask("给我看看 Docker 教程的图片")
    assert not unrelated_photos["products"], unrelated_photos
    policy = ask("Amazon.com 的一般退货政策是什么？")
    assert policy["sources"] and not policy["products"], policy
    comparison = ask("B07Z6DLFQS@amazon.com和B083V9KNTS@amazon.com哪一个更贵")
    assert comparison["action"] == "requires_tool" and not comparison["products"], comparison
    accessory = ask("给 iPhone 8 Plus 找个手机壳")
    assert (
        accessory["action"] == "found" and accessory["products"] and accessory["sources"]
    ), accessory
    try:
        get("/api/v1/remote", {"address": "https://example.com/?token=smoke-invalid-address"})
    except HTTPError as error:
        assert error.code == 502 and "smoke-invalid-address" not in error.read().decode()
    else:
        raise AssertionError("The Kaggle connector accepted a non-Kaggle domain.")
    print(
        "Full catalogue, pagination, clickable source data and image eligibility passed.",
        flush=True,
    )


def main():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    venv = ROOT / ".venv-web" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = venv if venv.is_file() else sys.executable
    workspace = ROOT / "abo_runtime"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "ABO_GPU": "0",
        "DOC_VQA_WORKSPACE": str(workspace),
        "ABO_FONT_PATH": str(workspace / "fonts/NotoSansSC.ttf"),
    }
    with subprocess.Popen(
        [python, "-m", "uvicorn", "app.asgi:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
    ) as server:
        try:
            exercise(port)
        finally:
            server.terminate()


if __name__ == "__main__":
    main()
