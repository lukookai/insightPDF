import time

def extract_tables_from_page(page):
    """
    输入: page (PyMuPDF page对象)
    输出:
        {
            "table_count": int,
            "tables": [
                {
                    "bbox": (x0, y0, x1, y1),
                    "cells": [
                        (cell_x0, cell_y0, cell_x1, cell_y1),
                        ...
                    ]
                },
                ...
            ],
            "time_cost": float(秒),
        }
    """
    table_finder = page.find_tables(strategy='lines_strict')
    start_time = time.time()
    tables = table_finder.tables
    elapsed = time.time() - start_time

    result = {
        "table_count": len(tables),
        "tables": [],
        "time_cost": elapsed
    }
    for table in tables:
        table_info = {
            "bbox": table.bbox,
            "cells": [tuple(cell) for cell in table.cells]
        }
        result["tables"].append(table_info)
    return result

if __name__ == "__main__":
    import fitz

    pdf_file = "nd1.pdf"
    page_number = 126

    doc = fitz.open(pdf_file)
    page = doc.load_page(page_number)

    result = extract_tables_from_page(page)
    print(result)

    doc.close()