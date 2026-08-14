待更新，更强壮的color采样，文本颜色采样。
对于xml类，切换到media文件夹，构建html，调用接口生成对应译文文件
使用端口号为8870
启动image_structure_json_api.py 端口8870
api接口请求测试，详情测试见test_app.py
3步法，使用步骤1后翻译后步骤3
8870图片image结构化总接口
git clone https://git.piggydamn.club:99/aiproject/image_structure_json.git
pip install -r requirements.txt
sudo apt-get install libasound2t64  

playwright install
playwright install-deps
更换ocr api请求地址直接修改configs.py
nohup gunicorn image_structure_json_api:app -k uvicorn.workers.UvicornWorker --workers $(($(nproc) * 1 + 1)) --bind 0.0.0.0:8870 --timeout 3600 > im_api_server.log 2>&1 &


sudo fuser -k -n tcp 8870
kill -9 $(lsof -t -i:8870)
git fetch origin
git reset --hard origin/main

内存，显存不够换位：
nohup gunicorn image_structure_json_api:app -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:8870 --timeout 3600 > im_api_server.log 2>&1 &

cat im_api_server.log
nohup env PYTHONPATH=. gunicorn image_structure_json_api:app -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:8870 --timeout 3600 >> im_api_server.log 2>&1 &
传入图片地址，将根据地址进行文件覆盖
本地mini ocr模型:

nohup uvicorn ocr_model_api:app --host 0.0.0.0 --port 7866 > server.log 2>&1 &
传入图片地址，将根据地址进行文件覆盖

conda install nvidia::cuda-toolkit==11.8.0
conda install anaconda::cudnn==8.9.2.26
conda install nvidia::cuda-toolkit==12.3.0
conda install main::cudnn==9.1.1.17
下图paddle依赖项进行选择：
30tf算力处理20张图一次，7900xtx一次80张，1秒40张


# CPU 版本
python -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/

# GPU 版本，需显卡驱动程序版本 ≥450.80.02（Linux）或 ≥452.39（Windows）
python -m pip install paddlepaddle-gpu==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/

# GPU 版本，需显卡驱动程序版本 ≥550.54.14（Linux）或 ≥550.54.14（Windows）
 python -m pip install paddlepaddle-gpu==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
 
python -m pip install paddlepaddle-gpu==3.1.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu123/
