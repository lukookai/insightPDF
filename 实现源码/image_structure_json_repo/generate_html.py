import base64
import os
import json

def img_to_base64(img_path):
    with open(img_path, 'rb') as f:
        img_data = f.read()
    ext = img_path.split('.')[-1].lower()
    base64_str = base64.b64encode(img_data).decode('utf-8')
    return f"data:image/{ext};base64,{base64_str}"

def bbox_to_style(bbox, color):
    left = bbox[0]
    top = bbox[1]
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return f"position: absolute; left: {left}px; top: {top}px; width: {width}px; height: {height}px; background: {color};"

def build_html(base64_bg, elements):
    head = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>KaTeX Auto Render Example</title>
    <link rel="stylesheet" href="katex.min.css">
    <style>
      html, body,div {{
        width: 100vw;
        height: 100vh;
        margin: 0;
        padding: 0;
        overflow: hidden;
      }}
      @font-face {{
        font-family: 'zh-CN';
        src: url('./fonts/zh.ttf') format('truetype');
      }}
      @font-face {{
        font-family: 'zh-TW';
        src: url('./fonts/zh-TW.ttf') format('truetype');
      }}
      @font-face {{
        font-family: 'ja';
        src: url('./fonts/ja.ttf') format('truetype');
      }}
      @font-face {{
        font-family: 'ko';
        src: url('./fonts/ko.ttf') format('truetype');
      }}
      /* 只保留 CJK 字体，西文不需要单独引入 */
      body {{
        font-size: 10px;
        line-height: 1.2;
        position: relative;
        background: #4f46e5 url('{base64_bg}') no-repeat center/cover;
        font-family: Arial, 'zh-CN', 'zh-TW', 'ja', 'ko', sans-serif;
      }}
      p{{
          font-size: 500px;
      }}
      .autoscale {{
          margin: 0;
          padding: 0;
}}
    </style>
</head>
<body>
<div class="autoscale">
'''
    tail = '''
</div>
<script src="katex.min.js"></script>
<script src="katex.auto-render.min.js"></script>
<script>
document.addEventListener("DOMContentLoaded", function() {
  renderMathInElement(document.body, {
    delimiters: [
      {left: "$", right: "$", display: true},
      {left: "$", right: "$", display: false},
      {left: "\\\\(", right: "\\\\)", display: false},
      {left: "\\\\[", right: "\\\\]", display: true}
    ],
    throwOnError: false
  });
});
</script>
<script>
function fitTextToBoxBinary(element, minFontSize=5, maxFontSize=100) {
  let computedFont = window.getComputedStyle(element).fontSize;
  let origFontSize = parseFloat(computedFont);
  if (!maxFontSize) maxFontSize = origFontSize;
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (element.scrollWidth <= width && element.scrollHeight <= height) { return; }
  let low = minFontSize, high = Math.min(maxFontSize, origFontSize), best = minFontSize;
  while (low <= high) {
    let mid = Math.floor((low + high) / 2);
    element.style.fontSize = mid + "px";
    if (element.scrollWidth > width || element.scrollHeight > height) {
      high = mid - 1;
    } else {
      best = mid;
      low = mid + 1;
    }
  }
  element.style.fontSize = best + "px";
}
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.autoscale').forEach(el => {
    fitTextToBoxBinary(el, 2, 100);

    // 检查行数并调整 line-height
    const fontSize = parseFloat(window.getComputedStyle(el).fontSize);
    const height = el.clientHeight;
    // 允许有小数误差
    const lines = Math.round(height / fontSize);

    if (lines > 1) {
      el.style.lineHeight = 1.2;
    } else {
      el.style.lineHeight = 1.0;
    }
  });
});
</script>
</body>
</html>'''
    return head + '\n'.join(elements) + tail


def main(img_paths_test, jsonl):
    # 1. 图片base64字典
    img_b64_map = {os.path.abspath(p): img_to_base64(p) for p in img_paths_test}

    n = len(jsonl)
    i = 0
    for img_path in img_paths_test:
        abs_img = os.path.abspath(img_path)
        base64_bg = img_b64_map[abs_img]
        elements = []
        # 按顺序收集属于当前图片的所有元素
        while i < n and os.path.abspath(jsonl[i]['src_image']) == abs_img:
            item = jsonl[i]
            bbox = item['bbox']
            color = item.get('color', '#ffffff')
            style = bbox_to_style(bbox, color)
            text = item.get('translated_content', '')
            if not text:
                text = item.get('content', '')

            inner = text
            if item.get('image_type') == 'title':
                inner = f"<strong>{inner}</strong>"
            p_html = f'<p class="autoscale" style="{style}">{inner}</p>'
            elements.append(p_html)
            i += 1
        html = build_html(base64_bg, elements)
        out_html = os.path.splitext(img_path)[0] + ".html"
        with open(out_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"write: {out_html}")
        return out_html


# 示例调用
if __name__ == "__main__":
    img_paths_test = [

        "img.png"
    ]
    out_path = "19913213.json"
    # 读取jsonl
    with open(out_path, "r", encoding="utf-8") as f:
        jsonl = json.load(f)
    main(img_paths_test, jsonl)
