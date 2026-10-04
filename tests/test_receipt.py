from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.cord import amount, quantity, map_labels
from docwork.local_model import LocalModelConfig, ModelOutputInvalid
from docwork.receipt import (HEADERS, ROWS, _value, extract_receipt_rules, extract_receipt_model,
                             receipt_evidence, score_receipt, summarize_receipts)


def page():
    lines = ("Bakery", "REAL GANACHE 1 16,500", "EGG TART 1 13.000",
             "Subtotal 29,500", "Tax 2,950", "TOTAL 32.450", "Cash 50,000", "Change 17,550")
    return DocumentPage(1, 1000, 1000, tuple(TextSpan(f"s{i}", 1, line,
        Box(.1, i * .06, .9, i * .06 + .04), "fixture") for i, line in enumerate(lines)))


class ReceiptTests(unittest.TestCase):
    def labels(self):
        return map_labels({"gt_parse": {"menu": [
            {"nm": "REAL GANACHE", "cnt": "1 x", "price": "16,500"},
            {"nm": "EGG TART", "cnt": "1", "price": "13.000"}],
            "sub_total": {"subtotal_price": "29,500", "tax_price": "2,950"},
            "total": {"total_price": "32.450", "cashprice": "50,000"}}}, "receipt-1")

    def test_rupiah_convention_does_not_guess_ambiguous_separators(self):
        for raw, expected in (("Rp 1.500", "1500"), ("1,500", "1500"), ("1.500,00", "1500"),
                              ("0.000", "0"), ("@20.000", "20000"), ("1,50", None), ("1.234,56", None)):
            with self.subTest(raw=raw):
                self.assertEqual(amount(raw), expected)
        self.assertEqual(quantity("1 x"), "1")
        self.assertEqual(quantity("x2"), "2")
        self.assertIsNone(quantity("1.00xITEMS"))

    def test_mapping_masks_unreleased_fields_and_excludes_submenus(self):
        gold = self.labels()
        self.assertEqual(set(gold["fields"]), set(HEADERS))
        self.assertEqual(gold["fields"]["total"], "32450")
        self.assertIn("service", gold["field_exclusions"])
        self.assertIn("unit_price", gold["line_items"][0]["exclusions"])
        gold = map_labels({"gt_parse": {"menu": {"nm": "Tea", "price": "0.000", "sub": {"nm": "Hot"}}}}, "one")
        self.assertEqual(len(gold["line_items"]), 1)
        self.assertEqual(gold["line_items"][0]["line_total"], "0")
        self.assertEqual(set(gold["field_exclusions"]), set(HEADERS))

    def test_rules_extract_rows_and_do_not_turn_cash_into_an_item(self):
        record = extract_receipt_rules(page())
        self.assertEqual(record["fields"]["total"]["value"], "32450")
        self.assertEqual(len(record["line_items"]), 2)
        self.assertEqual(record["line_items"][0]["description"]["value"], "REAL GANACHE")
        self.assertEqual(record["line_items"][0]["quantity"]["value"], "1")
        score = score_receipt(self.labels(), {"record": record})
        self.assertEqual(score["exact_rows"], 2)
        self.assertEqual(summarize_receipts([score])["eligible_exact_rows"]["f1"], 1)

    def test_missing_prediction_counts_all_eligible_amounts_and_rows(self):
        score = score_receipt(self.labels(), {"record": None, "failure_type": "ModelUnavailable"})
        summary = summarize_receipts([score])
        self.assertEqual(summary["documents_processed"], 0)
        self.assertEqual(summary["row_detection"]["fn"], 2)
        self.assertEqual(summary["header_fields"]["total"]["fn"], 1)
        self.assertIsNone(summary["header_fields"]["service"]["f1"])

    def test_unlabeled_optional_cells_do_not_count_as_known_empty_values(self):
        record = extract_receipt_rules(page())
        record["line_items"][0]["unit_price"] = _value("999", ("s1",), numeric="amount")
        record["fields"]["service"] = _value("999", ("s1",), numeric="amount")
        score = score_receipt(self.labels(), {"record": record})
        self.assertEqual(score["exact_rows"], 2)
        self.assertEqual(score["row_cells"]["unit_price"]["fp"], 0)
        self.assertNotIn("service", score["header"])

    def test_duplicate_rows_keep_multiplicity_and_wrong_amount_is_not_exact(self):
        gold = self.labels()
        gold["line_items"].append(dict(gold["line_items"][0]))
        record = extract_receipt_rules(page())
        record["line_items"][0]["line_total"]["value"] = "999"
        score = score_receipt(gold, {"record": record})
        self.assertEqual(score["matched_rows"], 2)
        self.assertEqual(score["exact_rows"], 1)
        self.assertEqual(score["gold_rows"], 3)

    def test_model_input_has_only_spans_and_bounded_schema_repair(self):
        record = extract_receipt_rules(page())
        response = {"fields": {name: {"value": field["raw"], "evidence_ids": field["evidence_ids"]}
                               for name, field in record["fields"].items()},
                    "line_items": [{name: {"value": row[name]["raw"], "evidence_ids": row[name]["evidence_ids"]}
                                    for name in ROWS} for row in record["line_items"]]}
        request = Mock(side_effect=["{}", json.dumps(response)])
        extracted = extract_receipt_model(page(), LocalModelConfig("http://127.0.0.1:8080", "model"), request)
        self.assertEqual(extracted["fields"]["total"]["value"], "32450")
        self.assertEqual(request.call_count, 2)
        payload = json.loads(request.call_args_list[0].args[1]["messages"][1]["content"])
        self.assertEqual(set(payload), {"page", "spans"})
        failing = Mock(return_value="{}")
        with self.assertRaises(ModelOutputInvalid):
            extract_receipt_model(page(), LocalModelConfig("http://127.0.0.1:8080", "model"), failing)
        self.assertEqual(failing.call_count, 2)

    def test_valid_id_and_value_alignment_are_separate(self):
        record = extract_receipt_rules(page())
        evidence = receipt_evidence(record, page())
        self.assertEqual(evidence["observed_values"], evidence["aligned_values"])
        record["fields"]["total"]["value"] = "123"
        misaligned = receipt_evidence(record, page())
        self.assertEqual(evidence["valid_reference_values"], misaligned["valid_reference_values"])
        self.assertEqual(misaligned["aligned_values"], evidence["aligned_values"] - 1)
        record["fields"]["total"]["evidence_ids"] = ["invented"]
        self.assertEqual(receipt_evidence(record, page())["valid_reference_values"], evidence["valid_reference_values"] - 1)


if __name__ == "__main__":
    unittest.main()
