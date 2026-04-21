# AI Benchmark Suite

Benchmark tập trung cho 3 chỉ số chính:

1. Intent routing accuracy
2. Triage quality accuracy
3. Booking success rate

## Chạy benchmark

Backend cần chạy sẵn (mặc định `http://localhost:8000`), đồng thời DB/Redis cần sẵn sàng.

```bash
cd backend
python3 evals/run_benchmark.py --base-url http://localhost:8000
```

Report mặc định:

- `backend/evals/reports/latest_report.json`

## Dataset

- `evals/datasets/intent_routing.jsonl`
- `evals/datasets/triage_quality.jsonl`
- `evals/datasets/booking_success.jsonl`

Bạn có thể mở rộng JSONL theo format hiện có để tăng độ bao phủ test.
