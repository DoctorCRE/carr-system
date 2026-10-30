"""Exercise real Apple Vision on generated synthetic scanned and mixed PDF fixtures."""

import hashlib
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.deal_extraction import ExtractionJob, run_job
from lib.deal_ocr import ocr_regions


@unittest.skipUnless(sys.platform == "darwin", "Apple Vision requires macOS")
class VisionTests(unittest.TestCase):
    def test_real_pdf_regions_keep_native_rent_and_read_scan_schedule(self):
        fixture = runpy.run_path("tools/test-deal-extraction.py")
        cli = runpy.run_path("bin/deal-extraction-job.py")
        build = runpy.run_path("evals/deal-extraction/build_scans.py")["build"]
        with tempfile.TemporaryDirectory(prefix="deal-vision-proof-") as folder:
            build(Path(folder))
            for mode in ("scan-only", "mixed", "ocr-table"):
                data = (Path(folder) / f"{mode}.pdf").read_bytes()
                regions = ocr_regions(data, pages=[n + 1 for n, page in enumerate(cli["text_pages"](data)) if not page.strip()])
                self.assertFalse(any("Base rent" in r["text"] for r in regions))
                store = fixture["Source"]()

                def call(verb, args):
                    if verb == "read-deal-extraction-source":
                        reply = store(verb, args)
                        reply["document"].update(
                            content_hash=hashlib.sha256(data).hexdigest(),
                            bytes=len(data),
                        )
                        return reply
                    return store(verb, args)

                def model(*args, **kwargs):
                    reply = fixture["output"]("30")
                    reply["fields"][0]["cited_text"] = (
                        "Base rent is $30 per SF per year NNN."
                    )
                    reply["fields"][1] = {
                        "fact_key": "term_months",
                        "status": "not_present",
                        "reason": "absent",
                    }
                    self.assertEqual(mode, "mixed")
                    return {"output": reply, "cost_usd": "0", "model": "haiku"}

                run_job(
                    ExtractionJob(
                        UUID(fixture["KEY"]),
                        UUID(fixture["ENTITY"]),
                        UUID(fixture["DOC"]),
                        "lease",
                        "haiku",
                    ),
                    call=call,
                    read_bytes=lambda d: data,
                    text_pages=cli["text_pages"],
                    model=model,
                    ocr_regions=lambda d, **kwargs: regions,
                )
                observations = {
                    o["fact_key"]: o for o in store.submissions[0]["observations"]
                }
                self.assertEqual(
                    observations["rent_schedule"]["value"][1]["rate"]["value"], "24.72"
                )
                self.assertEqual(
                    observations["rent_schedule"]["value"][1]["escalation_percent"][
                        "value"
                    ],
                    "3",
                )
                self.assertEqual(observations["rent_schedule"]["method"], "ocr")
                self.assertEqual(observations["term_months"]["method"], "ocr")
                if mode == "mixed":
                    self.assertEqual(
                        (
                            observations["base_rent_per_sf"]["value"],
                            observations["base_rent_per_sf"]["method"],
                        ),
                        ("30", "text_layer"),
                    )


if __name__ == "__main__":
    unittest.main()
