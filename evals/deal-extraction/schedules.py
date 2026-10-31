"""Build synthetic schedule cases without duplicating document-type fixtures."""

import copy


def build_corpus():
    rows = ["Year 1 | $24 | USD/SF/year | NNN | none",
            "Year 2 | $24.72 | USD/SF/year | NNN | 3%"]
    expected = [{"start_month": 1, "end_month": 12, "rate": "24", "unit": "USD/SF/year",
                 "expense_basis": "nnn", "escalation_percent": None},
                {"start_month": 13, "end_month": 24, "rate": "24.72", "unit": "USD/SF/year",
                 "expense_basis": "nnn", "escalation_percent": "3"}]
    cases = []
    for kind in ("loi", "lease", "counter", "amendment"):
        for mode in ("text", "scan", "mixed", "ocr-table"):
            pages = (["Rent schedule\n" + "\n".join(rows)] if mode == "text" else
                     ["Base rent is $30 per SF per year NNN.", ""] if mode == "mixed" else [""])
            regions = [] if mode == "text" else [
                {"page": len(pages), "region_id": f"table-row-{n}", "method": "ocr", "text": row}
                for n, row in enumerate(rows)]
            cases.append({"id": f"{kind}-{mode}", "document_type": kind, "ocr": mode != "text",
                          "pages": pages, "regions": regions,
                          "expected": {"rent_schedule": copy.deepcopy(expected)}, "expected_route": "normal"})
    cases.append({"id": "scan-disagreement", "document_type": "lease", "ocr": True, "pages": [""],
                  "regions": [{"page": 1, "region_id": "row", "method": "ocr", "text": rows[0],
                               "alternative_text": "Year 1 | $29 | USD/SF/year | NNN | none"}],
                  "expected": {}, "expected_route": "high"})
    return {"schema_version": "deal-schedule-synthetic.v1", "synthetic_only": True,
            "adjudicated_real_documents": 0, "cases": cases}
