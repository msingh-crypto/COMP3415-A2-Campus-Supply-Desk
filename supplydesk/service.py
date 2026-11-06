"""Operations used by the command-line order desk."""

from supplydesk.storage import JsonStore

VALID_CUSTOMER_TYPES = ("regular", "student")
VALID_FULFILLMENT_OPTIONS = ("pickup", "delivery", "express")
MIN_ITEM_QUANTITY = 1
MAX_ITEM_QUANTITY = 99
STUDENT_DISCOUNT_THRESHOLD_CENTS = 5000
STUDENT_DISCOUNT_PERCENT = 10
HALF_CENT_ROUNDING_OFFSET = 50
PERCENT_DIVISOR = 100
FREE_DELIVERY_THRESHOLD_CENTS = 10000
DELIVERY_FEE_CENTS = 600
EXPRESS_DELIVERY_FEE_CENTS = 1200
ORDER_ID_PREFIX = "ORD-"
ORDER_ID_WIDTH = 4


class OrderService:
    def __init__(self, state_path=".supplydesk/state.json"):
        self.store = JsonStore(state_path)

    def catalog(self):
        return self.store.load()["catalog"]

    def quote(self, customer_type, items, fulfillment="pickup"):
        state = self.store.load()
        return self._evaluate_request(state, customer_type, items, fulfillment)

    def place_order(self, customer_type, items, fulfillment="pickup"):
        state = self.store.load()
        summary = self._evaluate_request(state, customer_type, items, fulfillment)
        order = {
            "order_id": self._format_order_id(state["next_order_number"]),
            "status": "placed",
            **summary,
        }
        self._deduct_stock(state["catalog"], summary["items"])
        state["next_order_number"] += 1
        state["orders"].append(order)
        self.store.save(state)
        return order

    def list_orders(self):
        return self.store.load()["orders"]

    def get_order(self, order_id):
        state = self.store.load()
        return self._find_order(state["orders"], order_id)

    def cancel_order(self, order_id):
        state = self.store.load()
        order = self._find_order(state["orders"], order_id)
        self._restore_stock(state["catalog"], order["items"])
        order["status"] = "cancelled"
        self.store.save(state)
        return order

    def _evaluate_request(self, state, customer_type, items, fulfillment):
        self._validate_request_options(customer_type, items, fulfillment)
        lines, subtotal = self._build_order_lines(state["catalog"], items)
        discount = self._calculate_discount(customer_type, subtotal)
        delivery = self._calculate_delivery(fulfillment, subtotal, discount)
        return {
            "customer_type": customer_type,
            "fulfillment": fulfillment,
            "items": lines,
            "subtotal_cents": subtotal,
            "discount_cents": discount,
            "delivery_cents": delivery,
            "total_cents": subtotal - discount + delivery,
        }

    def _validate_request_options(self, customer_type, items, fulfillment):
        if customer_type not in VALID_CUSTOMER_TYPES:
            raise ValueError("Customer type must be regular or student.")
        if fulfillment not in VALID_FULFILLMENT_OPTIONS:
            raise ValueError("Fulfillment must be pickup, delivery, or express.")
        if not isinstance(items, list) or not items:
            raise ValueError("An order must contain at least one item.")

    def _build_order_lines(self, catalog, items):
        lines = []
        subtotal = 0
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each item must contain a product_id and quantity.")
            product = self._find_product(catalog, item.get("product_id"))
            quantity = item.get("quantity")
            if type(quantity) is not int or quantity < MIN_ITEM_QUANTITY or quantity > MAX_ITEM_QUANTITY:
                raise ValueError("Quantity must be an integer from 1 to 99.")
            if quantity > product["stock"]:
                raise ValueError("Not enough stock for " + product["product_id"] + ".")
            line_total = product["unit_price_cents"] * quantity
            subtotal += line_total
            lines.append({
                "product_id": product["product_id"],
                "name": product["name"],
                "unit_price_cents": product["unit_price_cents"],
                "quantity": quantity,
                "line_total_cents": line_total,
            })
        return lines, subtotal

    def _calculate_discount(self, customer_type, subtotal):
        if customer_type == "student" and subtotal > STUDENT_DISCOUNT_THRESHOLD_CENTS:
            return (subtotal * STUDENT_DISCOUNT_PERCENT + HALF_CENT_ROUNDING_OFFSET) // PERCENT_DIVISOR
        return 0

    def _calculate_delivery(self, fulfillment, subtotal, discount):
        if fulfillment == "express":
            return EXPRESS_DELIVERY_FEE_CENTS
        if fulfillment == "delivery" and subtotal - discount < FREE_DELIVERY_THRESHOLD_CENTS:
            return DELIVERY_FEE_CENTS
        return 0

    def _find_product(self, catalog, product_id):
        for candidate in catalog:
            if candidate["product_id"] == product_id:
                return candidate
        raise ValueError("Unknown product.")

    def _find_order(self, orders, order_id):
        for candidate in orders:
            if candidate["order_id"] == order_id:
                return candidate
        raise ValueError("Unknown order: " + order_id)

    def _deduct_stock(self, catalog, lines):
        for line in lines:
            product = self._find_product(catalog, line["product_id"])
            product["stock"] -= line["quantity"]

    def _restore_stock(self, catalog, lines):
        for line in lines:
            product = self._find_product(catalog, line["product_id"])
            product["stock"] += line["quantity"]

    def _format_order_id(self, order_number):
        return ORDER_ID_PREFIX + str(order_number).zfill(ORDER_ID_WIDTH)
        