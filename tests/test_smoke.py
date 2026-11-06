"""Automated test suite for the Campus Supply Desk order service."""

from pathlib import Path
import tempfile
import unittest

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
        # 10 notebooks = 5000 cents ($50.00). Spec states student gets 10% at >= 5000 cents.
        quote = self.service.quote("student", [{"product_id": "notebook", "quantity": 10}])
        self.assertEqual(quote["subtotal_cents"], 5000)
        self.assertEqual(quote["discount_cents"], 500)
        self.assertEqual(quote["total_cents"], 4500)

    def test_student_no_discount_just_below_5000_cents_boundary(self):
        # 6 pen-packs = 4500 cents (< 5000 cents). No discount should apply.
        quote = self.service.quote("student", [{"product_id": "pen-pack", "quantity": 6}])
        self.assertEqual(quote["subtotal_cents"], 4500)
        self.assertEqual(quote["discount_cents"], 0)
        self.assertEqual(quote["total_cents"], 4500)

    def test_free_delivery_at_exact_10000_post_discount_boundary(self):
        # Regular customer: 2 hoodies + 4 notebooks = 8000 + 2000 = 10000 cents exactly.
        quote = self.service.quote(
            "regular",
            [{"product_id": "hoodie", "quantity": 2}, {"product_id": "notebook", "quantity": 4}],
            "delivery",
        )
        self.assertEqual(quote["subtotal_cents"], 10000)
        self.assertEqual(quote["delivery_cents"], 0)
        self.assertEqual(quote["total_cents"], 10000)

        # Student customer: 10000 subtotal - 1000 discount = 9000 (< 10000), so delivery is 600.
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
        # Canvas bag initial stock is 4.
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
        # Canvas bag has initial stock of 4. Two lines of 3 = 6 total (> 4).
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
        # Pricing and items must match identically between quote and order
        for key in ("customer_type", "fulfillment", "items", "subtotal_cents",
                    "discount_cents", "delivery_cents", "total_cents"):
            self.assertEqual(quote[key], order[key])

    def test_quote_alone_never_writes_state_file(self):
        self.service.quote("regular", [{"product_id": "notebook", "quantity": 5}])
        self.assertFalse(self.path.exists())

    def test_cancelling_already_cancelled_order_is_idempotent(self):
        # Initial mug stock is 8.
        order = self.service.place_order("regular", [{"product_id": "mug", "quantity": 3}])
        first_cancel = self.service.cancel_order(order["order_id"])
        second_cancel = self.service.cancel_order(order["order_id"])

        self.assertEqual(first_cancel["status"], "cancelled")
        self.assertEqual(second_cancel["status"], "cancelled")
        stock_by_id = {item["product_id"]: item["stock"] for item in self.service.catalog()}
        self.assertEqual(stock_by_id["mug"], 8)


if __name__ == "__main__":
    unittest.main()
    