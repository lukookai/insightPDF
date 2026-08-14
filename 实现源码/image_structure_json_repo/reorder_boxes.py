def format_detection_result(raw_results):
    """
    raw_results: List[dict], 每个dict形如
        {
          "filename": xxx,
          "boxes": [
              {"label": ..., "coordinate": ..., "score": ..., "cls_id": ...},
              ...
          ]
        }
    返回: List[dict], 每个dict形如
        {
          "filename": xxx,
          "detections": [
              {"box": ..., "conf": ..., "class": ..., "class_name": ..., "is_first": ..., "is_last": ...},
              ...
          ]
        }
    """
    result_list = []
    for item in raw_results:
        filename = item.get("filename")
        boxes = item.get("boxes", [])
        detections = []
        for idx, box in enumerate(boxes):
            detection = {
                "box": box["coordinate"],
                "conf": box["score"],
                "class": box["cls_id"],
                "class_name": box["label"],
                "reordered": box.get("reordered", False),
                'read_index': idx+1
            }
            # 标记第一个和最后一个
            if idx == 0:
                detection["is_edge"] = "first"
            if idx == len(boxes) - 1:
                detection["is_edge"] = "last"
            detections.append(detection)
        result_list.append({
            "filename": filename,
            "detections": detections
        })
    print('89',result_list)
    return result_list
