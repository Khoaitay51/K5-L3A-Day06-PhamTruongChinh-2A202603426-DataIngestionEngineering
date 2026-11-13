# Báo cáo cá nhân — Day 06 Data Ingestion Engineering

**Học viên** Phạm Trường Chinh · **MSSV** 2A202603426 · **Lớp** L3A

## Dữ liệu và pipeline

Corpus gồm **35 trang nội dung** từ 16 tài liệu của 3M, Amcor, AMD, American Express, Best Buy và Boeing. Mỗi file Markdown ứng với một trang PDF, đếm từ 0 theo FinanceBench. Hai trang bìa ở offset 0 được loại khỏi corpus. [Manifest](../../data/manifest.json) ghi URL nguồn, số trang và độ dài sau chuẩn hóa. Mẫu dữ liệu công khai của PatronusAI được ghi CC BY-NC 4.0 trên [dataset card](https://huggingface.co/datasets/PatronusAI/financebench). Bài lab dùng các trích đoạn cho mục đích học tập phi thương mại. Tài liệu gốc của doanh nghiệp có điều khoản riêng.

Pipeline đọc UTF‑8, chuẩn hóa Unicode NFC và khoảng trắng, giữ ranh giới đoạn. Lần chạy không gặp file thiếu hay trang rỗng. Bảng tài chính được trích thành nhiều dòng rời nên cả hai cách chia chunk có thể cắt nhãn hoặc cột số liệu. Metadata gồm `document_id`, `source`, `page`, `company`, `year` và `doc_type`. `year` là niên độ theo nguồn, ví dụ Amcor FY2023 Q2 kết thúc vào tháng 12/2022.

- Embedding local bằng Ollama 0.35.0, `nomic-embed-text:latest` (Nomic Embed Text v1.5, 768 chiều, digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`). Prefix cho câu hỏi là `search_query: ` và cho nội dung là `search_document: `.
- PostgreSQL 16.16 và pgvector 0.8.7, tìm kiếm cosine chính xác. Bộ lọc dùng JSONB `@>` trên `company` và `year` đã có trong câu hỏi. Nạp lại thay các chunk cũ trong phạm vi cấu hình và kiểm tra số dòng.
- Lần chạy ngày 13/11/2026, Python 3.12.3, psycopg 3.3.8, pypdf 6.21.4 và python-dotenv 1.2.4. Chi phí dịch vụ là **0 USD**. Xem [lệnh setup và chạy](../../starter/README.md) và [log lần chạy](../../results/run.log).

## Cấu hình và benchmark

| Cấu hình | Chunking | Truy xuất |
|---|---|---|
| A | Cửa sổ 500 ký tự, overlap 50 | Cosine, không filter |
| B | Gom theo đoạn, tối đa 800 ký tự. Dòng riêng lẻ dài hơn 800 được chia với overlap 80 | Cosine, không filter |
| C | Cùng chunk và embedding với B | Cosine, có filter nếu câu hỏi cung cấp công ty và niên độ |

Bộ benchmark gồm 20 câu soạn lại từ nội dung trang nguồn, không nhập câu hỏi hay nhãn gold từ FinanceBench. Có 15 dev, 5 test và 6 câu có filter, trong đó 2 câu thuộc test. Nguồn gold của dev và test thuộc các tài liệu khác nhau. Cả ba cấu hình dùng chung corpus, model và câu hỏi. Hit@1/Hit@3 yêu cầu một chunk đúng nguồn/trang và chứa đủ các trích đoạn được ghi cho một bộ gold. Nếu một trang khác cũng có đủ bằng chứng, chấp nhận bộ gold ở trang đó. Sau lượt truy xuất, bổ sung trang 95 của American Express cho `q19` và chấm lại từ trace lưu sẵn. Thay đổi này không làm đổi số hit, nhưng tập test đã được xem trong khi rà gold nên kết quả test chỉ mang tính minh họa.

Tiêu chí chọn đã ghi trong bản báo cáo ban đầu là ưu tiên Evidence Hit@3 trên dev, sau đó xét coherence, grounding và tài nguyên.

## Kết quả

| Cấu hình | Split | Hit@1 | Evidence Hit@3 | Chunk | Ingest | Chi phí |
|---|---|---|---|---:|---:|---:|
| A | dev | 2/15 | 3/15 | 258 | 48,36 s | 0 USD |
| A | test | 1/5 | 2/5 | 258 | 48,36 s | 0 USD |
| B | dev | 6/15 | 6/15 | 185 | 49,11 s | 0 USD |
| B | test | 2/5 | 4/5 | 185 | 49,11 s | 0 USD |
| C | dev | 6/15 | 7/15 | 185 | 0,34 s | 0 USD |
| C | test | 2/5 | 4/5 | 185 | 0,34 s | 0 USD |

Thời gian là một lần đo ingest cho mỗi cấu hình. C dùng lại vector B, nên 0,34 s chủ yếu là thời gian ghi vào PostgreSQL. Số dòng sau khi nạp lại lần lượt là 258, 185 và 185. Thống kê chiều dài và số ký tự có trong [summary](../../results/summary.json).

| Cấu hình | Coherence /2, 10 vị trí | Metadata khớp manifest | Tuân thủ filter | Grounding 0 / 1 / 2, TB /2 |
|---|---:|---:|---|---|
| A | 1,00 | 15/15 | N/A | 1 / 1 / 3, TB 1,40 |
| B | 0,80 | 15/15 | N/A | 1 / 0 / 4, TB 1,60 |
| C | 0,80 | 15/15 | dev 4/4, test 2/2 | 1 / 0 / 4, TB 1,60 |

Coherence chấm chunk chứa ký tự đầu tiên của mỗi trang trong 10 trang đầu theo thứ tự ID tài liệu và trang. Mẫu này nhạy với chunk chỉ có tiêu đề. Metadata kiểm tra toàn bộ 15 kết quả top‑3 trên test theo manifest. Grounding xem cả ba chunk của từng câu và kiểm tra chúng có đủ thông tin để trả lời, truy về nguồn hay không. [Nhãn, chunk ID và lý do từng mục](../../results/manual_audit.json) được rà với sự hỗ trợ của Codex và còn cần học viên tự xác nhận theo yêu cầu chấm thủ công.

## Phân tích và kết luận

- **A–B.** B giảm số chunk từ 258 xuống 185, đồng thời tăng Evidence Hit@3 trên dev từ 3/15 lên 6/15. B lại thấp hơn ở mẫu coherence đầu trang, vì một số chunk đầu chỉ có tiêu đề khi khối nội dung sau không vừa phần còn lại của cửa sổ 800 ký tự.
- **B–C.** Lọc theo công ty và niên độ đưa Evidence Hit@3 trên dev từ 6/15 lên 7/15. Với `q03`, B trả về hai chunk từ 3M 2023 Q2 ở rank 2 và 3, trong khi C lọc về 3M FY2022 và tìm được hàng biên lợi nhuận hoạt động ở rank 3. `source` và `page` giúp truy lại bảng gốc, nhưng filter phụ thuộc việc có đúng công ty và niên độ từ đầu.
- **Lỗi thiếu số liệu.** Ở `q19`, B và C lấy được đầu trang American Express nhưng chunk chỉ có tiêu đề hoặc phần khác của báo cáo. Top‑3 thiếu hai con số lợi nhuận ròng, dù truy xuất đúng trang nguồn. Ở `q18`, A chứa đủ hai mốc tháng để trả lời nhưng thiếu phần đầu của trích đoạn gold. Câu này đạt grounding 2/2 và trượt phép so khớp chuỗi chính xác.
- **Lựa chọn.** C đạt điểm dev Hit@3 cao nhất trong lần chạy này. Giữ B làm chế độ truy xuất khi không có filter. Mẫu test chỉ có 5 câu, số đo thời gian chỉ có một lượt và bảng trích xuất còn mất bố cục cột. Bước cải tiến tiếp theo là gắn tiêu đề vào chunk nội dung đầu tiên và thử chunking theo hàng bảng.

## Công cụ AI và kiểm chứng

Codex hỗ trợ soạn câu hỏi, chuẩn hóa manifest, chỉnh pipeline, chạy lại ba cấu hình và rà trace. Đối chiếu bằng chứng với trang nguồn, kiểm tra số dòng trong PostgreSQL và tính lại chỉ số từ trace đã lưu. Các nhãn định tính trong lần chạy này cần Phạm Trường Chinh xem và xác nhận trước khi coi là phần chấm thủ công của học viên.
