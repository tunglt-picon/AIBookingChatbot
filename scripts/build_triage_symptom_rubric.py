#!/usr/bin/env python3
"""
Sinh backend/data/mock/triage_symptom_rubric_vi.json từ bảng TSV (user message / triệu chứng / hỏi thêm).

Chạy: python scripts/build_triage_symptom_rubric.py
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "backend" / "data" / "mock" / "triage_symptom_rubric_vi.json"

# Ưu tiên TSV trong repo (ổn định CI); không có thì đọc transcript Cursor (máy dev).
TSV_LOCAL = REPO / "backend" / "data" / "mock" / "triage_examples.tsv"
TRANSCRIPT = Path.home() / ".cursor/projects/home-tungnui-DEV-AI-AIBookingChatbot/agent-transcripts/6603a5a1-b8dd-40d6-be75-2c473e1c6ebb/6603a5a1-b8dd-40d6-be75-2c473e1c6ebb.jsonl"


def _load_tsv_source() -> str:
    if TSV_LOCAL.is_file():
        return TSV_LOCAL.read_text(encoding="utf-8")
    return _extract_tsv_from_transcript()


def _extract_tsv_from_transcript() -> str:
    import json as js

    with open(TRANSCRIPT, encoding="utf-8") as f:
        for line in f:
            if "chấm đen nhỏ" not in line or "user_query" not in line:
                continue
            obj = js.loads(line)
            text = obj["message"]["content"][0]["text"]
            if "<user_query>" in text:
                text = text.split("<user_query>", 1)[1].split("</user_query>", 1)[0]
            idx = text.find("Câu Khách Hàng Chat")
            tail = text[idx:]
            end = tail.find("\n\nTôi có 1 bô")
            return tail[:end] if end > 0 else tail
    raise SystemExit("Không tìm thấy TSV trong transcript.")


def _split_symptoms(s: str) -> list[str]:
    parts = []
    for p in (s or "").split(","):
        p = p.strip()
        if p:
            parts.append(p)
    return parts


def suggest_case_code(user_message: str, extracted: str, missing: str) -> str:
    u = (user_message or "").lower()
    e = (extracted or "").lower()
    m = (missing or "").lower()
    combo = u + " " + e

    if any(k in combo for k in ("implant", "cấy ghép", "trồng răng")):
        return "IMPLANT"

    if "cạo vôi" in combo or "vệ sinh định kỳ" in combo or "khám dự phòng" in combo:
        return "SCALING"

    emerg_msg = (
        "sốt hầm hập",
        "sốt",
        "hầm hập",
        "sưng vù cả má",
        "sưng vù",
        "méo cả miệng",
        "sưng cả mắt",
        "mụn mủ",
        "nhức kinh khủng",
        "cảm giác như răng nó dài ra",
        "cắn hai hàm không khít được",
        "thuốc lào",
        "không xi nhê",
        "chả ăn thua",
        "uống kháng sinh uống 3 ngày",
        "nhức bung cái đầu",
        "tủy chết",
        "mụn mủ bự",
        "sưng to làm bà méo",
        "sáng dậy thấy răng hàm sưng vù",
    )
    if any(k in u for k in emerg_msg):
        return "EMERGENCY"
    if "cấp cứu ngay" in m or "cấp cứu nha khoa" in m:
        return "EMERGENCY"
    if "thuốc giảm đau mà vẫn không đỡ" in u:
        return "EMERGENCY"
    if "panadol" in u and "không xi nhê" in u:
        return "EMERGENCY"
    if "trám tạm" in combo and "nhức chịu không nổi" in u:
        return "EMERGENCY"
    if "đặt thuốc diệt tủy" in u and "nhức bung" in u:
        return "EMERGENCY"
    if "bầu 8 tháng" in u and "sưng cả mắt" in u:
        return "EMERGENCY"

    if "tụt lợi" in e and "chân răng" in combo:
        return "GINGIVITIS"
    if "viêm nướu" in combo:
        return "GINGIVITIS"

    return "CAVITY"


CATEGORIES = [
    {
        "code": "CAVITY",
        "label_vi": "Sâu răng / khắc phục sâu răng",
        "symptom_signals": [
            "lỗ sâu",
            "sâu răng",
            "trám",
            "ê buốt",
            "nhức khi ăn ngọt",
            "chấm đen",
            "đốm đen",
            "mẻ men",
            "mòn men",
            "cổ răng",
            "răng khôn sâu",
            "sún",
            "vệt xám",
            "lủng",
            "thủng",
            "nhét thức ăn",
            "rớt trám",
            "bọc sứ",
            "lấy tủy",
            "răng cối",
            "răng cấm",
        ],
        "typical_missing_slots_to_ask": [
            "Vị trí răng (hàm trên/dưới, răng số mấy nếu biết)",
            "Mức độ đau 1–10",
            "Đau kích thích (nóng/lạnh/ngọt/chua) hay tự phát",
            "Có đau về đêm / mất ngủ không",
            "Có sưng nướu cục bộ hay mủ quanh chân răng không",
        ],
        "boundary_notes_vi": "Đa số mô tả lỗ đen, ê buốt, nhức khi ăn, mẻ, trám hỏng → CAVITY. Chuyển EMERGENCY nếu kèm sốt, sưng má lan rộng, áp xe rõ, kháng thuốc giảm đau + đau cực độ, hoặc hướng dẫn cấp cứu trong cột hỏi thêm.",
    },
    {
        "code": "IMPLANT",
        "label_vi": "Trồng răng / implant",
        "symptom_signals": ["implant", "cấy ghép", "trồng răng", "mất răng lâu", "cần thay răng"],
        "typical_missing_slots_to_ask": [
            "Vị trí răng mất / cần phục hình",
            "Đã mất răng được bao lâu",
            "Có bệnh nền (tiểu đường, hút thuốc) cần ghi nhận không",
        ],
        "boundary_notes_vi": "Chỉ khi BN nói rõ nhu cầu implant/trồng răng; không đoán từ đau sâu thông thường.",
    },
    {
        "code": "GINGIVITIS",
        "label_vi": "Viêm nướu / nha chu",
        "symptom_signals": [
            "chảy máu chân răng",
            "viêm nướu",
            "nha chu",
            "tụt lợi",
            "lộ chân răng",
            "hôi miệng kèm nướu",
        ],
        "typical_missing_slots_to_ask": [
            "Chảy máu khi đánh răng hay tự chảy",
            "Nướu có sưng đỏ, có mủ không",
            "Ê buốt lan nhiều răng hay một răng",
        ],
        "boundary_notes_vi": "Ưu tiên khi trọng tâm là nướu/tụt lợi/hội chứng nha chu; nếu chủ yếu là lỗ sâu đơn thuần → CAVITY.",
    },
    {
        "code": "SCALING",
        "label_vi": "Cạo vôi / vệ sinh răng miệng",
        "symptom_signals": [
            "cạo vôi",
            "vệ sinh răng",
            "khám định kỳ",
            "cao răng",
            "hôi nhẹ",
        ],
        "typical_missing_slots_to_ask": [
            "Lần khám / cạo vôi gần nhất",
            "Có chảy máu nướu thường xuyên không",
        ],
        "boundary_notes_vi": "Khi BN chỉ muốn vệ sinh/định kỳ, không có cơn đau cấp hay lỗ sâu rõ.",
    },
    {
        "code": "EMERGENCY",
        "label_vi": "Đau cấp / sưng nướu / cần xử lý nhanh",
        "symptom_signals": [
            "sốt",
            "sưng má",
            "áp xe",
            "mủ",
            "đau dữ dội",
            "sưng mặt",
            "không đỡ thuốc",
            "cấp cứu",
        ],
        "typical_missing_slots_to_ask": [
            "Có sốt / rét run / mệt không",
            "Sưng lan nhanh không, có nuốt/khó thở không (nếu có → hướng dẫn đến cơ sở y tế ngay)",
            "Mức độ đau hiện tại 1–10",
        ],
        "boundary_notes_vi": "Nghi ngờ nhiễm trùng lan rộng, biến chứng cấp, hoặc đau cực độ + dấu hiệu toàn thân → EMERGENCY để xếp khung sớm hơn.",
    },
]


def main() -> None:
    raw = _load_tsv_source()
    reader = csv.reader(io.StringIO(raw), delimiter="\t")
    rows = list(reader)
    if not rows:
        raise SystemExit("TSV rỗng")
    header = rows[0]
    if len(header) < 3:
        raise SystemExit(f"Header không đủ cột: {header}")

    examples: list[dict] = []
    for i, parts in enumerate(rows[1:], start=1):
        while len(parts) < 3:
            parts.append("")
        um, ext, miss = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not um:
            continue
        code = suggest_case_code(um, ext, miss)
        examples.append(
            {
                "id": f"ex{i:03d}",
                "user_message": um,
                "extracted_symptoms": _split_symptoms(ext),
                "missing_slots_questions": miss,
                "suggested_case_code": code,
            }
        )

    doc = {
        "schema_version": 1,
        "description": "Mapping mock: dental_case_code (CAVITY|IMPLANT|GINGIVITIS|SCALING|EMERGENCY) ↔ tín hiệu triệu chứng ↔ gợi ý câu hỏi thiếu. Dùng cho AI tiếp nhận: bóc tách → hỏi bổ sung → so khớp → BN xác nhận loại khám → get_mock_schedule.",
        "categories": CATEGORIES,
        "examples": examples,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(examples)} examples -> {OUT}")


if __name__ == "__main__":
    main()
