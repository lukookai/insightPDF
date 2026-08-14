import fitz
from pathlib import Path
doc = fitz.open("t1.pdf")
page = doc[0]  # 获取第一页
xref = 13      # 要替换的图片的 xref

filename = "t6.png"
abs_path = Path(filename).resolve()
page.replace_image(xref, filename=abs_path)
doc.save("replaced.pdf")
