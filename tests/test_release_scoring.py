from __future__ import annotations

import unittest
import itertools
import random

from docwork.contracts import HEADER_FIELDS
from docwork.release_scoring import _assignment, score_invoice, summarize_invoices


def gold() -> dict:
    fields = {name: None for name in HEADER_FIELDS}
    fields.update({"supplier_name": "Aster Studio LLC", "invoice_number": "AST-1",
                   "issue_date": "2026-09-12", "currency": "USD", "subtotal": "30.00",
                   "total": "30.00"})
    return {"id": "invoice-1", "family_group": "family-1", "fields": fields,
            "line_items": [
                {"description": "Design review", "quantity": "1", "unit_price": "10.00",
                 "line_total": "10.00", "tax": None},
                {"description": "Design review", "quantity": "2", "unit_price": "10.00",
                 "line_total": "20.00", "tax": None},
            ]}


def candidate(source: dict, rows: list[dict] | None = None) -> dict:
    rows = source["line_items"] if rows is None else rows
    return {"fields": {name: {"value": value} for name, value in source["fields"].items()},
            "line_items": [{name: {"value": value} for name, value in row.items()} for row in rows]}


class ReleaseScoringTests(unittest.TestCase):
    def test_duplicate_rows_match_one_to_one_without_order_assumption(self):
        source = gold()
        prediction = candidate(source, list(reversed(source["line_items"])))
        score = score_invoice(source, prediction)
        self.assertEqual(score["row_matches"], [
            {"gold_index": 0, "predicted_index": 1},
            {"gold_index": 1, "predicted_index": 0},
        ])
        self.assertEqual(score["exact_rows"], 2)
        self.assertTrue(score["required_all_exact"])
        summary = summarize_invoices([score])
        self.assertEqual(summary["row_detection"]["f1"], 1)
        self.assertEqual(summary["row_exact"]["f1"], 1)
        self.assertIsNone(summary["header_fields"]["tax"]["f1"])

    def test_extra_duplicate_and_missing_value_count_as_errors(self):
        source = gold()
        rows = [*source["line_items"], source["line_items"][0]]
        prediction = candidate(source, rows)
        prediction["fields"]["total"]["value"] = None
        prediction["fields"]["tax"]["value"] = "0.00"
        score = score_invoice(source, prediction)
        summary = summarize_invoices([score])
        self.assertEqual((score["matched_rows"], score["exact_rows"]), (2, 2))
        self.assertEqual(summary["row_detection"]["fp"], 1)
        self.assertEqual(summary["row_exact"]["fp"], 1)
        self.assertEqual(summary["header_fields"]["total"]["fn"], 1)
        self.assertEqual(summary["header_fields"]["tax"]["fp"], 1)
        self.assertEqual(summary["all_required_exact"]["correct"], 0)

    def test_failed_document_stays_in_all_denominators(self):
        score = score_invoice(gold(), None)
        summary = summarize_invoices([score])
        self.assertEqual(summary["documents_processed"], 0)
        self.assertEqual(summary["documents_scheduled"], 1)
        self.assertEqual(summary["header_fields"]["supplier_name"]["fn"], 1)
        self.assertEqual(summary["row_detection"]["fn"], 2)
        self.assertEqual(summary["row_fields"]["line_total"]["fn"], 2)
        self.assertEqual(summary["failures_by_type"], {"MissingPrediction": 1})

    def test_numeric_format_and_whitespace_normalization(self):
        source = gold()
        prediction = candidate(source)
        prediction["fields"]["supplier_name"]["value"] = "  ASTER   studio LLC "
        prediction["fields"]["total"]["value"] = "30"
        prediction["line_items"][0]["quantity"]["value"] = "1.0"
        self.assertEqual(score_invoice(source, prediction)["exact_rows"], 2)
        self.assertEqual(summarize_invoices([score_invoice(source, prediction)])["header_fields"]["total"]["tp"], 1)

    def test_unrelated_rows_are_not_matched_by_one_shared_number(self):
        source = gold()
        row = {"description": "Other service", "quantity": "7", "unit_price": "99.00",
               "line_total": "10.00", "tax": None}
        score = score_invoice(source, candidate(source, [row]))
        self.assertEqual(score["matched_rows"], 0)
        self.assertEqual(score["row_cells"]["line_total"], {"tp": 0, "fp": 1, "fn": 2})

    def test_duplicate_document_ids_are_rejected(self):
        score = score_invoice(gold(), None)
        with self.assertRaises(ValueError):
            summarize_invoices([score, score])

    def test_invalid_gold_amount_is_rejected(self):
        source = gold()
        source["fields"]["total"] = "NaN"
        with self.assertRaisesRegex(ValueError, "Invalid gold amount"):
            score_invoice(source, None)

    def test_global_assignment_matches_exhaustive_small_cases(self):
        rng = random.Random(1729)
        for size in range(1, 6):
            for _ in range(12):
                costs = [[rng.randrange(-9, 5) for _ in range(size)] for _ in range(size)]
                selected = _assignment(costs)
                observed = sum(costs[row][column] for row, column in enumerate(selected))
                expected = min(sum(costs[row][column] for row, column in enumerate(columns))
                               for columns in itertools.permutations(range(size)))
                self.assertEqual(observed, expected)


if __name__ == "__main__":
    unittest.main()
