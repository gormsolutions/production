import frappe
from frappe.utils import flt, getdate


@frappe.whitelist()
def make_return_material_entry(work_order_id):
    """
    Create a Material Transfer Stock Entry for returned raw materials.

    Batch Work Order:
        FG Warehouse -> Source Warehouse

    For every Required Item where return_qty > 0.
    """

    if not work_order_id:
        frappe.throw("Batch Work Order is required.")

    # ---------------------------------------------------------
    # LOAD CUSTOM BATCH WORK ORDER
    # ---------------------------------------------------------
    work_order = frappe.get_doc(
        "Batch Work Order",
        work_order_id
    )

    if not work_order:
        frappe.throw(
            f"Could not find Batch Work Order: {work_order_id}"
        )

    # ---------------------------------------------------------
    # VALIDATE WAREHOUSES
    # ---------------------------------------------------------
    if not work_order.fg_warehouse:
        frappe.throw(
            f"FG Warehouse is not set on Batch Work Order "
            f"{work_order.name}."
        )

    if not work_order.source_warehouse:
        frappe.throw(
            f"Source Warehouse is not set on Batch Work Order "
            f"{work_order.name}."
        )

    fg_warehouse = work_order.wip_warehouse
    source_warehouse = work_order.source_warehouse

    return_items = []

    # ---------------------------------------------------------
    # FIND ITEMS WITH RETURN QTY
    # ---------------------------------------------------------
    for item in work_order.required_items:

        if not item.item_code:
            continue

        return_qty = flt(item.return_qty)

        # No return
        if return_qty <= 0:
            continue

        # Only stock items
        if not item.is_stock_item:
            continue

        transferred_qty = flt(item.transferred_qty)

        # -----------------------------------------------------
        # RETURN CANNOT EXCEED TRANSFERRED
        # -----------------------------------------------------
        if return_qty > transferred_qty:

            frappe.throw(
                f"Return quantity for {item.item_code} "
                f"({return_qty}) cannot be greater than "
                f"transferred quantity ({transferred_qty})."
            )

        # -----------------------------------------------------
        # GET STOCK UOM FROM ITEM
        # -----------------------------------------------------
        stock_uom = frappe.db.get_value(
            "Item",
            item.item_code,
            "stock_uom"
        )

        if not stock_uom:
            frappe.throw(
                f"Stock UOM is not set for Item "
                f"{item.item_code}."
            )

        return_items.append({
            "required_item_name": item.name,
            "item_code": item.item_code,
            "item_name": item.item_name,
            "qty": return_qty,
            "uom": stock_uom,
            "rate": flt(item.rate),
        })

    # ---------------------------------------------------------
    # NOTHING TO RETURN
    # ---------------------------------------------------------
    if not return_items:

        return {
            "success": True,
            "message": "No items with return quantity.",
            "stock_entry": None,
            "items": []
        }

    # ---------------------------------------------------------
    # CREATE STOCK ENTRY
    # ---------------------------------------------------------
    stock_entry = frappe.new_doc("Stock Entry")

    stock_entry.stock_entry_type = "Material Transfer"
    stock_entry.purpose = "Material Transfer"

    stock_entry.company = work_order.company
    stock_entry.posting_date = getdate(
        work_order.posting_date
    )

    # ---------------------------------------------------------
    # IMPORTANT:
    #
    # DO NOT DO THIS:
    #
    # stock_entry.work_order = work_order.name
    #
    # Because Stock Entry.work_order links to ERPNext
    # "Work Order", while this document is "Batch Work Order".
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # ADD RETURN ITEMS
    # ---------------------------------------------------------
    for return_item in return_items:

        se_item = stock_entry.append(
            "items",
            {}
        )

        se_item.item_code = return_item["item_code"]

        se_item.qty = return_item["qty"]

        se_item.uom = return_item["uom"]

        se_item.stock_uom = return_item["uom"]

        # -----------------------------------------------------
        # RETURN:
        #
        # FG Warehouse
        #       ↓
        # Source Warehouse
        # -----------------------------------------------------
        se_item.s_warehouse = fg_warehouse
        se_item.t_warehouse = source_warehouse

        if return_item["rate"]:
            se_item.basic_rate = return_item["rate"]

    # ---------------------------------------------------------
    # INSERT
    # ---------------------------------------------------------
    stock_entry.insert(
        ignore_permissions=True
    )

    # ---------------------------------------------------------
    # SUBMIT
    # ---------------------------------------------------------
    stock_entry.submit()

    # ---------------------------------------------------------
    # UPDATE CONSUMED QUANTITY
    #
    # consumed_qty =
    # transferred_qty - return_qty
    #
    # amount =
    # consumed_qty * rate
    # ---------------------------------------------------------
    for item in work_order.required_items:

        return_qty = flt(item.return_qty)

        if return_qty <= 0:
            continue

        if not item.is_stock_item:
            continue

        transferred_qty = flt(
            item.transferred_qty
        )

        item.consumed_qty = max(
            transferred_qty - return_qty,
            0
        )

        item.amount = (
            flt(item.consumed_qty) *
            flt(item.rate)
        )

    # ---------------------------------------------------------
    # SAVE BATCH WORK ORDER
    # ---------------------------------------------------------
    work_order.save(
        ignore_permissions=True
    )

    frappe.db.commit()

    # ---------------------------------------------------------
    # RESPONSE
    # ---------------------------------------------------------
    return {
        "success": True,
        "message": (
            f"Return Material Stock Entry "
            f"{stock_entry.name} created successfully."
        ),
        "stock_entry": stock_entry.name,
        "items": return_items,
        "from_warehouse": fg_warehouse,
        "to_warehouse": source_warehouse
    }