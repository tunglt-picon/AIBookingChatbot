import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        rows.append(json.loads(raw))
    return rows


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    idx = int(round((q / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
    t0 = time.monotonic()
    resp = client.post(path, json=payload)
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    resp.raise_for_status()
    return resp.json(), elapsed_ms


def run_intent_benchmark(client: httpx.Client) -> dict[str, Any]:
    rows = _read_jsonl(DATASETS / "intent_routing.jsonl")
    latencies: list[float] = []
    hit = 0
    details = []

    for i, row in enumerate(rows, start=1):
        payload = {
            "agent": "classify_intent",
            "message": row["message"],
            "session_id": 10000 + i,
            "patient_user_id": 1,
            "state_patch": row.get("state_patch", {}),
        }
        body, elapsed_ms = _post(client, "/api/v1/admin/lab/agents/invoke", payload)
        updates = body.get("updates", {})
        pred = updates.get("intent")
        ok = pred == row["expected_intent"]
        if ok:
            hit += 1
        latencies.append(elapsed_ms)
        details.append(
            {
                "id": row["id"],
                "expected_intent": row["expected_intent"],
                "predicted_intent": pred,
                "ok": ok,
                "latency_ms": round(elapsed_ms, 2),
            }
        )

    total = max(len(rows), 1)
    return {
        "total": len(rows),
        "correct": hit,
        "accuracy": round(hit / total, 4),
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": round(_pct(latencies, 50), 2),
            "p95": round(_pct(latencies, 95), 2),
        },
        "details": details,
    }


def run_triage_benchmark(client: httpx.Client) -> dict[str, Any]:
    rows = _read_jsonl(DATASETS / "triage_quality.jsonl")
    latencies: list[float] = []
    hit = 0
    details = []

    for i, row in enumerate(rows, start=1):
        human_messages = [{"role": "human", "content": m} for m in row.get("messages", [])]
        payload = {
            "agent": "dental_specialist",
            "message": "",
            "session_id": 20000 + i,
            "patient_user_id": 1,
            "state_patch": {
                "messages": human_messages,
                "follow_up_count": 99,
                "symptoms_summary": " ".join(row.get("messages", [])),
            },
        }
        body, elapsed_ms = _post(client, "/api/v1/admin/lab/agents/invoke", payload)
        updates = body.get("updates", {})
        pred = updates.get("category_code")
        ok = pred == row["expected_category_code"]
        if ok:
            hit += 1
        latencies.append(elapsed_ms)
        details.append(
            {
                "id": row["id"],
                "expected_category_code": row["expected_category_code"],
                "predicted_category_code": pred,
                "ok": ok,
                "latency_ms": round(elapsed_ms, 2),
            }
        )

    total = max(len(rows), 1)
    return {
        "total": len(rows),
        "correct": hit,
        "accuracy": round(hit / total, 4),
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": round(_pct(latencies, 50), 2),
            "p95": round(_pct(latencies, 95), 2),
        },
        "details": details,
    }


def run_booking_benchmark(client: httpx.Client) -> dict[str, Any]:
    rows = _read_jsonl(DATASETS / "booking_success.jsonl")
    latencies: list[float] = []
    success = 0
    details = []

    for i, row in enumerate(rows, start=1):
        intake_payload = {
            "tool": "save_consult_intake",
            "args": {
                "patient_user_id": 1,
                "session_id": 30000 + i,
                "symptoms": "Benchmark synthetic symptoms",
                "ai_diagnosis": "Benchmark only",
                "needs_visit": True,
                "category_code": row["category_code"],
            },
        }
        intake_body, _ = _post(client, "/api/v1/admin/lab/tools/invoke", intake_payload)
        intake_id = (intake_body.get("result") or {}).get("intake_id")

        slot_payload = {
            "tool": "get_mock_schedule",
            "args": {
                "scope": "day",
                "date_str": row["date_str"],
                "category_code": row["category_code"],
            },
        }
        slot_body, _ = _post(client, "/api/v1/admin/lab/tools/invoke", slot_payload)
        slots = (slot_body.get("result") or {}).get("slots", [])
        if not slots or not intake_id:
            details.append({"id": row["id"], "ok": False, "reason": "missing intake or slots"})
            continue

        first_slot = slots[0]
        book_payload = {
            "tool": "book_appointment",
            "args": {
                "patient_user_id": 1,
                "intake_id": intake_id,
                "datetime_str": first_slot["datetime_str"],
            },
        }
        book_body, elapsed_ms = _post(client, "/api/v1/admin/lab/tools/invoke", book_payload)
        out = book_body.get("result", {})
        ok = bool(out.get("reservation_id"))
        if ok:
            success += 1
        latencies.append(elapsed_ms)
        details.append(
            {
                "id": row["id"],
                "reservation_id": out.get("reservation_id"),
                "slot_display": out.get("display"),
                "ok": ok,
                "latency_ms": round(elapsed_ms, 2),
            }
        )

    total = max(len(rows), 1)
    return {
        "total": len(rows),
        "success": success,
        "success_rate": round(success / total, 4),
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": round(_pct(latencies, 50), 2),
            "p95": round(_pct(latencies, 95), 2),
        },
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark intent/triage/booking for SmileCare AI")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", default=str(ROOT / "reports" / "latest_report.json"))
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        report = {
            "base_url": args.base_url,
            "generated_at_unix": int(time.time()),
            "benchmarks": {
                "intent_routing_accuracy": run_intent_benchmark(client),
                "triage_quality_accuracy": run_triage_benchmark(client),
                "booking_success_rate": run_booking_benchmark(client),
            },
        }

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Benchmark completed. Report: {out_path}")
    print(json.dumps(report["benchmarks"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
