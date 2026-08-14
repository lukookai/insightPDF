import requests
import time
from pathlib import Path
from configs import screen_shoot_api
def generate_screenshots(html_files, endpoint= screen_shoot_api):
    html_and_output = []
    for item in html_files:
        html_file = item["file"]
        width = item.get("width", 1280)
        height = item.get("height", 720)
        html_path = Path(html_file)
        suffix = "." + item.get("type", "png")
        out_path = str(html_path.with_suffix(suffix))
        html_and_output.append({
            "html": str(html_path),
            "output": out_path,
            "width": width,
            "height": height,
            "type": item.get("type", "png")
        })

    start_time = time.time()

    resp = requests.post(
        endpoint,
        json={
            "html_and_output": html_and_output
        }
    )

    end_time = time.time()
    print(resp.json())
    print(f"生成截图总用时: {end_time - start_time:.2f} 秒")
    return resp.json()

if __name__ == "__main__":
    html_files = [

        {"file": "test.html", "width": 864, "height": 1223, "type": "pdf"}
    ]
    result = generate_screenshots(html_files)
