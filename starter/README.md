# Chạy pipeline Data Ingestion Engineering

Cần Python 3.11 trở lên, Docker với Compose và Ollama. Cần mạng để tải image PostgreSQL và model trong lần đầu. Ollama chạy local qua HTTP với model Nomic Embed Text v1.5, 768 chiều.

## macOS / Linux / WSL

Mở Ollama. Nếu chưa chạy nền, chạy `ollama serve` trong một terminal riêng. Từ thư mục gốc của repo, chạy:

```bash
ollama pull nomic-embed-text:v1.5
cd starter
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
docker compose -p day6_student_starter up -d --wait
.venv/bin/python scripts/check_setup.py
.venv/bin/python -m src.run
```

## Windows PowerShell

Khởi động Docker Desktop và Ollama. Từ thư mục gốc của repo, chạy:

```powershell
ollama pull nomic-embed-text:v1.5
Set-Location starter
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose -p day6_student_starter up -d --wait
.\.venv\Scripts\python.exe scripts/check_setup.py
.\.venv\Scripts\python.exe -m src.run
```

PostgreSQL lắng nghe tại `127.0.0.1:55433`, Ollama tại `localhost:11434` theo mặc định. Nếu Ollama chạy trên máy chủ khác, sửa `OLLAMA_BASE_URL` trong `.env`.

## Dữ liệu và kết quả

Pipeline đọc danh sách trang trong [manifest](../data/manifest.json), đọc [benchmark](../benchmark/questions.json) và ghi trace, summary cùng các mẫu kiểm tra vào [results](../results/). Lần chạy mới ghi đè các file tự động trong thư mục kết quả. Nhãn trong `manual_audit.json` cần kiểm tra lại nếu trace đổi. Xem [báo cáo cá nhân](../report/individual/2A202603426_PhamTruongChinh.md) để biết cấu hình và cách tính điểm.

Từ thư mục `starter`, dùng `docker compose -p day6_student_starter stop` để dừng PostgreSQL và giữ dữ liệu.
