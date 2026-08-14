import subprocess
import os
import fitz  # PyMuPDF
import platform  # 新增

def extract_first_page(input_pdf, output_pdf):
    """
    用PyMuPDF将input_pdf的第一页保存为output_pdf
    """
    doc = fitz.open(input_pdf)
    new_doc = fitz.open()  # 新建一个空PDF
    new_doc.insert_pdf(doc, from_page=0, to_page=0)
    new_doc.save(output_pdf)
    new_doc.close()
    doc.close()

def convert_with_inkscape(
    input_file,
    output_type='svg',
    output_file=None,
    inkscape_path=None  # 默认None
):
    # 自动判断系统，设置inkscape路径
    if inkscape_path is None:
        if platform.system() == 'Windows':
            inkscape_path = r'C:\Program Files\Inkscape\bin\inkscape.exe'
        else:
            inkscape_path = 'inkscape'

    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"输入文件不存在: {input_file}")

    # 默认输出文件名
    if output_file is None:
        base, _ = os.path.splitext(input_file)
        output_file = f"{base}.{output_type}"

    # 如果是pdf，先提取第一页
    temp_input = input_file
    remove_temp = False
    if input_file.lower().endswith('.pdf'):
        temp_input = input_file + "_page1.pdf"
        extract_first_page(input_file, temp_input)
        remove_temp = True

    cmd = [
        inkscape_path,
        temp_input,
        f'--export-type={output_type}',
        f'--export-filename={output_file}',
        '--pdf-poppler'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')

    # 删除临时pdf
    if remove_temp and os.path.exists(temp_input):
        os.remove(temp_input)

    return result.stdout, result.stderr

# 示例调用
if __name__ == "__main__":
    stdout, stderr = convert_with_inkscape('g2.pdf', output_type='emf')
    print(stdout)
    print(stderr)
