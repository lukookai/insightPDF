def get_page_number_from_filename_double(filename):
    """ 从文件名如 2_1_225.31915283203125_49.999298095703125.png 提取page_number=2 """
    # 假设文件名格式：2_1_xxx_xxx.png
    last_part = filename.split('_')[-1]        # '49.png'
    number_str = last_part.split('.')[0]

    return int(filename.split('_')[0]),int(number_str)

c = '1_1_18.921913146972656_58.18840026855469_13.png'
a ,b= get_page_number_from_filename_double(c)
print(a,b)

