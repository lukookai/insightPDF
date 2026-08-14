import requests

url = "http://127.0.0.1:8502/extract_json"
with open("t5.pdf", "rb") as f:
    files = {
        "pdf_file": ("t5.pdf", f, "application/pdf")
    }
    data = {
        "enable_image": "True"
    }
    response = requests.post(url, files=files, data=data)
    print(response.status_code)
    print(response.json())
