"""Automated test suite for the Campus Supply Desk order service."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from supplydesk.cli import main as cli_main
from supplydesk.menu import read_request
from supplydesk.service import OrderService


class OrderSmokeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.service = OrderService(self.path)

    # --- Existing Starter Tests ---

    def test_regular_pickup_quote(self):
        quote = self.service.quote("regular", [{"product_id": "notebook", "quantity": 2}])
        self.assertEqual(quote["subtotal_cents"], 1000)
        self.assertEqual(quote["discount_cents"], 0)
        self.assertEqual(quote["delivery_cents"], 0)
        self.assertEqual(quote["total_cents"], 1000)

    def test_student_delivery_quote(self):
        quote = self.service.quote("student", [{"product_id": "hoodie", "quantity": 2}], "delivery")
        self.assertEqual(quote["discount_cents"], 800)
        self.assertEqual(quote["delivery_cents"], 600)
        self.assertEqual(quote["total_cents"], 7800)

    def test_order_can_be_reopened(self):
        order = self.service.place_order("regular", [{"product_id": "mug", "quantity": 1}])
        self.assertEqual(order["order_id"], "ORD-0001")
        self.assertEqual(order["status"], "placed")
        self.assertEqual(OrderService(self.path).get_order(order["order_id"]), order)

    def test_cancel_order_restores_stock(self):
        before = self.service.catalog()
        order = self.service.place_order("regular", [{"product_id": "pen-pack", "quantity": 2}])
        cancelled = self.service.cancel_order(order["order_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.service.catalog(), before)

    # --- Category 1: Normal Behavior ---

    def test_multi_item_order_deducts_stock_and_increments_order_ids(self):
        first = self.service.place_order(
            "regular",
            [{"product_id": "notebook", "quantity": 3}, {"product_id": "mug", "quantity": 2}],
            "delivery",
        )
        second = self.service.place_order(
            "student",
            [{"product_id": "hoodie", "quantity": 2}],
            "pickup",
        )
        self.assertEqual(first["order_id"], "ORD-0001")
        self.assertEqual(first["subtotal_cents"], 4500)
        self.assertEqual(first["delivery_cents"], 600)
        self.assertEqual(first["total_cents"], 5100)
        self.assertEqual(second["order_id"], "ORD-0002")
        self.assertEqual(second["total_cents"], 7200)

        stock_by_id = {item["product_id"]: item["stock"] for item in self.service.catalog()}
        self.assertEqual(stock_by_id["notebook"], 17)
        self.assertEqual(stock_by_id["mug"], 6)
        self.assertEqual(stock_by_id["hoodie"], 3)
        self.assertEqual([o["order_id"] for o in self.service.list_orders()], ["ORD-0001", "ORD-0002"])

    def test_regular_delivery_free_when_threshold_reached(self):
        quote = self.service.quote("regular", [{"product_id": "hoodie", "quantity": 3}], "delivery")
        self.assertEqual(quote["subtotal_cents"], 12000)
        self.assertEqual(quote["discount_cents"], 0)
        self.assertEqual(quote["delivery_cents"], 0)
        self.assertEqual(quote["total_cents"], 12000)

    # --- Category 2: Boundary Behavior ---

    def test_student_discount_at_exact_5000_cents_boundary(self):
        quote = self.service.quote("student", [{"product_id": "notebook", "quantity": 10}])
        self.assertEqual(quote["subtotal_cents"], 5000)
        self.assertEqual(quote["discount_cents"], 500)
        self.assertEqual(quote["total_cents"], 4500)

    def test_student_no_discount_just_below_5000_cents_boundary(self):
        quote = self.service.quote("student", [{"product_id": "pen-pack", "quantity": 6}])
        self.assertEqual(quote["subtotal_cents"], 4500)
        self.assertEqual(quote["discount_cents"], 0)
        self.assertEqual(quote["total_cents"], 4500)

    def test_free_delivery_at_exact_10000_post_discount_boundary(self):
        quote = self.service.quote(
            "regular",
            [{"product_id": "hoodie", "quantity": 2}, {"product_id": "notebook", "quantity": 4}],
            "delivery",
        )
        self.assertEqual(quote["subtotal_cents"], 10000)
        self.assertEqual(quote["delivery_cents"], 0)
        self.assertEqual(quote["total_cents"], 10000)

        student_quote = self.service.quote(
            "student",
            [{"product_id": "hoodie", "quantity": 2}, {"product_id": "notebook", "quantity": 4}],
            "delivery",
        )
        self.assertEqual(student_quote["subtotal_cents"], 10000)
        self.assertEqual(student_quote["discount_cents"], 1000)
        self.assertEqual(student_quote["delivery_cents"], 600)
        self.assertEqual(student_quote["total_cents"], 9600)

    def test_ordering_exact_available_stock_succeeds_and_reaches_zero(self):
        order = self.service.place_order("regular", [{"product_id": "bag", "quantity": 4}])
        self.assertEqual(order["subtotal_cents"], 10000)
        stock_by_id = {item["product_id"]: item["stock"] for item in self.service.catalog()}
        self.assertEqual(stock_by_id["bag"], 0)

    # --- Category 3: Invalid or Exceptional Behavior ---

    def test_invalid_customer_or_fulfillment_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.quote("vip", [{"product_id": "notebook", "quantity": 1}])
        with self.assertRaises(ValueError):
            self.service.quote("regular", [{"product_id": "notebook", "quantity": 1}], "drone")

    def test_empty_items_or_unknown_product_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.quote("regular", [])
        with self.assertRaises(ValueError):
            self.service.place_order("regular", [{"product_id": "laptop", "quantity": 1}])
        self.assertFalse(self.path.exists())

    def test_invalid_quantities_rejected_including_booleans_strings_and_out_of_range(self):
        invalid_quantities = [0, -1, 100, True, False, 1.5, "2", None]
        for bad_qty in invalid_quantities:
            with self.subTest(quantity=bad_qty):
                with self.assertRaises(ValueError):
                    self.service.quote("regular", [{"product_id": "notebook", "quantity": bad_qty}])

    def test_duplicate_product_lines_exceeding_combined_stock_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.place_order(
                "regular",
                [{"product_id": "bag", "quantity": 3}, {"product_id": "bag", "quantity": 3}],
            )
        stock_by_id = {item["product_id"]: item["stock"] for item in self.service.catalog()}
        self.assertEqual(stock_by_id["bag"], 4)
        self.assertEqual(self.service.list_orders(), [])

    def test_unknown_order_id_raises_value_error_on_get_and_cancel(self):
        with self.assertRaises(ValueError):
            self.service.get_order("ORD-9999")
        with self.assertRaises(ValueError):
            self.service.cancel_order("ORD-9999")

    # --- Category 4: Behavior Important to Preserve ---

    def test_quote_does_not_create_save_file_or_mutate_stock(self):
        quote = self.service.quote("student", [{"product_id": "hoodie", "quantity": 2}], "delivery")
        order = self.service.place_order("student", [{"product_id": "hoodie", "quantity": 2}], "delivery")
        for key in ("customer_type", "fulfillment", "items", "subtotal_cents",
                    "discount_cents", "delivery_cents", "total_cents"):
            self.assertEqual(quote[key], order[key])

    def test_quote_alone_never_writes_state_file(self):
        self.service.quote("regular", [{"product_id": "notebook", "quantity": 5}])
        self.assertFalse(self.path.exists())

    def test_cancelling_already_cancelled_order_is_idempotent(self):
        order = self.service.place_order("regular", [{"product_id": "mug", "quantity": 3}])
        first_cancel = self.service.cancel_order(order["order_id"])
        second_cancel = self.service.cancel_order(order["order_id"])

        self.assertEqual(first_cancel["status"], "cancelled")
        self.assertEqual(second_cancel["status"], "cancelled")
        stock_by_id = {item["product_id"]: item["stock"] for item in self.service.catalog()}
        self.assertEqual(stock_by_id["mug"], 8)

    # --- CR-01: Express Delivery Tests ---

    def test_regular_express_quote_adds_1200_cents(self):
        quote = self.service.quote("regular", [{"product_id": "notebook", "quantity": 2}], "express")
        self.assertEqual(quote["fulfillment"], "express")
        self.assertEqual(quote["subtotal_cents"], 1000)
        self.assertEqual(quote["discount_cents"], 0)
        self.assertEqual(quote["delivery_cents"], 1200)
        self.assertEqual(quote["total_cents"], 2200)

    def test_student_express_quote_discounts_merchandise_only(self):
        quote = self.service.quote("student", [{"product_id": "hoodie", "quantity": 2}], "express")
        self.assertEqual(quote["subtotal_cents"], 8000)
        self.assertEqual(quote["discount_cents"], 800)
        self.assertEqual(quote["delivery_cents"], 1200)
        self.assertEqual(quote["total_cents"], 8400)

    def test_express_fee_is_never_waived_above_free_delivery_threshold(self):
        standard = self.service.quote("regular", [{"product_id": "hoodie", "quantity": 3}], "delivery")
        express = self.service.quote("regular", [{"product_id": "hoodie", "quantity": 3}], "express")
        self.assertEqual(standard["delivery_cents"], 0)
        self.assertEqual(standard["total_cents"], 12000)
        self.assertEqual(express["delivery_cents"], 1200)
        self.assertEqual(express["total_cents"], 13200)

    def test_express_order_agrees_with_quote_persists_and_cancels(self):
        items = [{"product_id": "hoodie", "quantity": 2}]
        quote = self.service.quote("student", items, "express")
        order = self.service.place_order("student", items, "express")
        for key in ("customer_type", "fulfillment", "items", "subtotal_cents",
                    "discount_cents", "delivery_cents", "total_cents"):
            self.assertEqual(quote[key], order[key])

        reopened = OrderService(self.path).get_order(order["order_id"])
        self.assertEqual(reopened["fulfillment"], "express")
        self.assertEqual(reopened["delivery_cents"], 1200)
        self.assertEqual(reopened["total_cents"], 8400)

        cancelled = self.service.cancel_order(order["order_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["fulfillment"], "express")
        self.assertEqual(cancelled["delivery_cents"], 1200)
        self.assertEqual(cancelled["total_cents"], 8400)

    def test_cli_and_menu_support_express_fulfillment(self):
        stdout_buffer = io.StringIO()
        with contextlib.redirect_stdout(stdout_buffer):
            exit_code = cli_main([
                "--data", str(self.path),
                "quote", "--customer", "student", "--fulfillment", "express", "--item", "hoodie:2",
            ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertEqual(payload["fulfillment"], "express")
        self.assertEqual(payload["delivery_cents"], 1200)
        self.assertEqual(payload["total_cents"], 8400)

        with patch("builtins.input", side_effect=["regular", "notebook", "2", "", "express"]), \
                contextlib.redirect_stdout(io.StringIO()):
            customer, items, fulfillment = read_request(self.service)
        self.assertEqual((customer, items, fulfillment),
                         ("regular", [{"product_id": "notebook", "quantity": 2}], "express"))


if __name__ == "__main__":
    unittest.main()
    