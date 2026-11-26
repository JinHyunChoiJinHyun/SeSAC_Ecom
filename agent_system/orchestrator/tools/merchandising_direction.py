"""V4.29 — 과거 판매 사례와 향후 기획전 편성 조건을 분리한다."""
from __future__ import annotations

ROLE = "historical_reference"


def is_reference(run_or_summary: dict | None) -> bool:
    data = run_or_summary or {}
    return data.get("evidence_role") == ROLE or data.get("reference_product_role") == ROLE


def build_direction(summary: dict, selected: dict | None = None, *, subtype: str = "", colors=None) -> dict:
    """상품 ID는 실적 근거이며 판매 SKU가 아님. 선택 소구점은 향후 편성에 쓸 검토 조건."""
    selected = selected or {}
    known = {x["label"]: x for x in summary.get("candidates") or []}
    points = list(selected.get("points") or [])
    if any(point not in known for point in points):
        raise ValueError("근거에 없는 디자인 방향은 확정할 수 없습니다.")
    entries = []
    for point in points:
        row = known[point]
        ids = list((row.get("matched") or {}).keys())
        entries.append({"feature": point, "scope_in_examples": row.get("scope"),
                        "reference_ids": ids, "reference_count": len(ids),
                        "requires_current_sku_verification": True})
    if isinstance(colors, str):
        colors = [x.strip() for x in colors.split(",") if x.strip()]
    return {"schema_version": 1, "role": "proposed_merchandising_direction",
            "subtype": subtype or summary.get("subtype") or "", "colors": list(colors or []),
            "design_features": entries, "reference_article_ids": list(summary.get("ids") or []),
            "saleable_article_ids": [], "inventory_verified": False,
            "notice": "H&M 과거 사례의 디자인 특징을 향후 상품 편성 조건으로 검토합니다. 현재 판매 상품·재고·성능은 미확정입니다."}


def direction_text(direction: dict) -> str:
    features = direction.get("design_features") or []
    lines = ["■ 기획전 상품 구성 방향 (실제 판매 상품 미확정)",
             f"상품 종류: {direction.get('subtype') or '미확정'}",
             "검토 색상: " + (", ".join(direction.get("colors") or []) or "미확정"),
             "검토 디자인 특징: " + (" / ".join(x["feature"] for x in features) or "소구점 선택 전"),
             "과거 H&M 상품 ID는 참고 사례이며 현재 판매 상품 목록이 아닙니다.",
             "현재 판매할 상품·재고·가격·혜택·실제 특징 적용 여부: 추후 확인"]
    return "\n".join(lines)
