#!/bin/bash

# 修复 Python 3.12 导致的所有包安装问题
python3.10 -m venv venv
source venv/bin/activate

pip install --no-cache-dir -r requirements.txt
gunicorn app:app --bind 0.0.0.0:$PORT
