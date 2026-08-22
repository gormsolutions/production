# Copyright (c) 2021, Totrox Technology and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from erpnext.utilities.transaction_base import validate_uom_is_integer
from erpnext.stock.doctype.item.item import get_item_defaults
from erpnext.stock.utils import get_latest_stock_qty, validate_warehouse_company
from frappe.utils import flt, now_datetime, nowdate
from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict
from erpnext.stock.get_item_details import get_default_cost_center


class BatchWorkOrder(Document):
	def onload(self):
		ms = frappe.get_doc("Manufacturing Settings")
		self.set_onload("material_consumption", ms.material_consumption)
		self.set_onload(
			"backflush_raw_materials_based_on", ms.backflush_raw_materials_based_on
		)
		self.set_onload(
			"overproduction_percentage", ms.overproduction_percentage_for_work_order
		)

	def validate(self):
		self.validate_manufacture_items()
		self.set_default_warehouse()
		self.validate_warehouse_belongs_to_company()
		self.status = self.get_status()
		validate_uom_is_integer(self, "stock_uom", ["qty", "produced_qty"])
		self.set_required_items()

	def before_submit(self):
		if len(self.manufacture_items) == 0:
			frappe.throw(_("Please add atleast one item to Manufacture Items"))
		self.status = "Awaiting RM Transfer"

	def validate_manufacture_items(self):
		items_list = []
		for item in self.manufacture_items:
			if item.item_code not in items_list:
				items_list.append(item.item_code)
			else:
				frappe.throw(_(f"Item: {item.item_code} is repeated"))
			if not item.qty > 0:
				frappe.throw(_("Quantity to Manufacture must be greater than 0."))

	def set_default_warehouse(self):
		if not self.wip_warehouse:
			self.wip_warehouse = frappe.db.get_single_value(
				"Manufacturing Settings", "default_wip_warehouse"
			)
		if not self.fg_warehouse:
			self.fg_warehouse = frappe.db.get_single_value(
				"Manufacturing Settings", "default_fg_warehouse"
			)

	def validate_warehouse_belongs_to_company(self):
		warehouses = [self.fg_warehouse, self.wip_warehouse]
		for d in self.get("required_items"):
			if d.source_warehouse not in warehouses:
				warehouses.append(d.source_warehouse)

		for wh in warehouses:
			validate_warehouse_company(wh, self.company)

	def get_status(self, status=None):
		"""Return the status based on stock entries against this work order"""
		if not status:
			status = self.status

		if self.docstatus == 0:
			status = "Draft"
		elif self.docstatus == 1:
			if status != "Completed":
				status = "In Process"
		elif self.docstatus == 2:
			status = "Canceled"

		return status

	def set_available_qty(self):
		for d in self.get("required_items"):
			if d.source_warehouse:
				d.available_qty_at_source_warehouse = get_latest_stock_qty(
					d.item_code, d.source_warehouse
				)

			if self.wip_warehouse:
				d.available_qty_at_wip_warehouse = get_latest_stock_qty(
					d.item_code, self.wip_warehouse
				)

	def set_required_items(self):
		"""set required_items for production to keep track of reserved qty"""
		self.required_items = []

		# operation = None
		# if self.get("operations") and len(self.operations) == 1:
		#     operation = self.operations[0].operation
		scrap_items_list = frappe.get_all(
			"BOM Scrap Item",
			filters={"parent": ["in", [i.bom for i in self.manufacture_items]]},
			fields="*",
		)
		self.scrap_items = []
		for scrap_item in scrap_items_list:
			bom = next(
				(i for i in self.manufacture_items if i.bom == scrap_item.parent), None
			)
			qty = scrap_item.stock_qty * bom.qty * bom.boom_quantity
			if qty <= 0:
				continue
			exist_item = next(
				(i for i in self.scrap_items if i.item_code == scrap_item.item_code),
				None,
			)
			if exist_item:
				exist_item.qty += qty
				exist_item.amount = exist_item.rate * exist_item.qty
			else:
				self.append(
					"scrap_items",
					{
						"item_code": scrap_item.item_code,
						"item_name": scrap_item.item_name,
						"is_process_loss": scrap_item.is_process_loss,
						"qty": qty,
						"rate": scrap_item.rate,
						"amount": scrap_item.rate * qty,
						"stock_uom": scrap_item.stock_uom,
					},
				)

		for item in self.manufacture_items:
			if item.bom and item.qty:
				item_dict = get_bom_items_as_dict(
					item.bom,
					self.company,
					qty=item.qty,
					fetch_exploded=self.use_multi_level_bom,
					include_non_stock_items=True,
				)

				for item in sorted(
					item_dict.values(), key=lambda d: d["idx"] or float("inf")
				):
					exist_item = next(
						(
							i
							for i in self.required_items
							if i.item_code == item.item_code
						),
						None,
					)
					if exist_item:
						exist_item.required_qty += item.qty
						exist_item.transferred_qty += (
							item.qty if exist_item.is_stock_item else 0
						)
						exist_item.consumed_qty += (
							item.qty if exist_item.is_stock_item else 0
						)
						exist_item.amount = exist_item.rate * exist_item.required_qty
					else:
						is_stock_item = frappe.get_cached_value(
							"Item", item.item_code, "is_stock_item"
						)
						self.append(
							"required_items",
							{
								"rate": item.rate,
								"amount": item.rate * item.qty,
								"operation": item.operation,
								"item_code": item.item_code,
								"item_name": item.item_name,
								"description": item.description,
								"allow_alternative_item": item.allow_alternative_item,
								"required_qty": item.qty,
								"transferred_qty": item.qty if is_stock_item else 0,
								"consumed_qty": item.qty if is_stock_item else 0,
								"source_warehouse": self.source_warehouse
								or item.source_warehouse
								or item.default_warehouse,
								"include_item_in_manufacturing": item.include_item_in_manufacturing,
								"expense_account": item.expense_account,
								"is_stock_item": is_stock_item,
							},
						)

					if not self.project:
						self.project = item.get("project")

			self.set_available_qty()

	@frappe.whitelist()
	def set_items_from_orders(self):
		if self.docstatus != 0:
			return
		if len(self.sales_orders) == 0:
			return

		orders_list = [i.sales_order for i in self.sales_orders]

		items = frappe.db.get_all(
			"Sales Order Item",
			filters={"parent": ["in", orders_list], "docstatus": 1},
			fields=["item_code", "qty", "rate"],
		)
		for item in items:
			exist_item = next(
				(i for i in self.manufacture_items if i.item_code == item.item_code),
				None,
			)
			if exist_item:
				exist_item.qty += item.qty
			else:
				bom = get_item_bom(item.item_code)
				if bom:
					row = self.append("manufacture_items", {})
					row.bom = bom
					row.item_code = item.item_code
					row.qty = item.qty

		self.save()


@frappe.whitelist()
def make_transfer_stock_entry(work_order_id):
	work_order = frappe.get_doc("Batch Work Order", work_order_id)
	wip_warehouse = work_order.wip_warehouse
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.purpose = "Material Transfer"
	stock_entry.stock_entry_type = "Material Transfer"
	stock_entry.company = work_order.company
	stock_entry.use_multi_level_bom = work_order.use_multi_level_bom
	stock_entry.to_warehouse = wip_warehouse
	stock_entry.custom_approve_status = "Approved"
	stock_entry.from_warehouse = work_order.source_warehouse
	stock_entry.project = work_order.project
	stock_entry.posting_date = work_order.stock_transfer_date or nowdate()
	stock_entry.set_posting_time = 1
	stock_entry.docstatus = 1

	for item_row in work_order.required_items:
		if not item_row.is_stock_item:
			continue
		stock_uom = item_row.get("stock_uom") or frappe.db.get_value(
			"Item", item_row.item_code, "stock_uom"
		)
		if not item_row.transferred_qty:
			continue
		se_child = stock_entry.append("items")
		se_child.s_warehouse = work_order.source_warehouse or get_item_defaults(
			item_row.get("item_code"), work_order.company
		).get("default_warehouse")
		se_child.t_warehouse = wip_warehouse
		se_child.item_code = item_row.get("item_code")
		se_child.uom = item_row.get("uom") if item_row.get("uom") else stock_uom
		se_child.stock_uom = stock_uom
		se_child.qty = flt(
			item_row.get("transferred_qty"), se_child.precision("transferred_qty")
		)
		se_child.allow_alternative_item = item_row.get("allow_alternative_item", 0)
		se_child.subcontracted_item = item_row.get("main_item_code")
		se_child.basic_rate = item_row.get("rate")
		se_child.cost_center = item_row.get("cost_center") or get_default_cost_center(
			item_row, company=work_order.company
		)

		if se_child.s_warehouse == None:
			se_child.s_warehouse = stock_entry.from_warehouse
		if se_child.t_warehouse == None:
			se_child.t_warehouse = stock_entry.to_warehouse

		# in stock uom
		se_child.conversion_factor = flt(item_row.get("conversion_factor")) or 1
		se_child.transfer_qty = flt(
			item_row.get("transferred_qty") * se_child.conversion_factor,
			se_child.precision("transferred_qty"),
		)
	stock_entry.insert(ignore_permissions=True)
	url = frappe.utils.get_url_to_form(stock_entry.doctype, stock_entry.name)
	frappe.msgprint(
		"Material Transfer Created <a href='{0}'>{1}</a>".format(url, stock_entry.name)
	)
	work_order.material_transfer = stock_entry.name
	work_order.status = "Awaiting FG Transfer"
	work_order.actual_start_date = now_datetime()
	work_order.save()
	work_order.notify_update()
	return stock_entry.name


# @frappe.whitelist()
# def make_manufacture_entry(work_order_id):
# 	frappe.msgprint(f"🔍 Starting manufacture entry for {work_order_id}")
# 	cost = 0
# 	work_order = frappe.get_doc("Batch Work Order", work_order_id)
# 	frappe.msgprint(f"✅ Loaded Work Order: {work_order.name}")
# 	wip_warehouse = work_order.wip_warehouse
# 	stock_entry = frappe.new_doc("Stock Entry")
# 	stock_entry.purpose = "Repack"
# 	stock_entry.stock_entry_type = "Repack"
# 	stock_entry.company = work_order.company
# 	stock_entry.use_multi_level_bom = work_order.use_multi_level_bom
# 	stock_entry.to_warehouse = work_order.fg_warehouse
# 	stock_entry.from_warehouse = wip_warehouse
# 	stock_entry.project = work_order.project
# 	stock_entry.posting_date = work_order.production_date or nowdate()
# 	stock_entry.set_posting_time = 1
# 	stock_entry.docstatus = 1

# 	for item_row in work_order.required_items:
# 		# frappe.msgprint(f"📦 Processing Item Row: {item_row.item_code}")
# 		if item_row.is_stock_item:   
# 			# frappe.msgprint("🔹 Stock Item Detected")
# 			stock_uom = item_row.get("stock_uom") or frappe.db.get_value(
# 				"Item", item_row.item_code, "stock_uom"
# 			)
# 			if not item_row.consumed_qty:
# 				continue
# 			cost += item_row.consumed_qty * item_row.rate
# 			se_child = stock_entry.append("items")
# 			se_child.s_warehouse = wip_warehouse
# 			se_child.item_code = item_row.get("item_code")
# 			se_child.uom = item_row.get("uom") if item_row.get("uom") else stock_uom
# 			se_child.stock_uom = stock_uom
# 			se_child.qty = flt(item_row.get("consumed_qty"), se_child.precision("qty"))
# 			se_child.allow_alternative_item = item_row.get("allow_alternative_item", 0)
# 			se_child.subcontracted_item = item_row.get("main_item_code")
# 			se_child.basic_rate = item_row.get("rate")
# 			se_child.cost_center = (
# 				item_row.get("cost_center")
# 				or work_order.get("cost_center")
# 				or get_default_cost_center(item_row, company=work_order.company)
# 			)
# 			se_child.set_basic_rate_manually = 1

# 			# in stock uom
# 			se_child.conversion_factor = flt(item_row.get("conversion_factor")) or 1
# 			se_child.transfer_qty = flt(
# 				item_row.get("transferred_qty") * se_child.conversion_factor,
# 				se_child.precision("transferred_qty"),
# 			)
# 		# else:
# 		#     frappe.msgprint("⚠️ Non-Stock Item Detected")
# 		#     ad_child = stock_entry.append("additional_costs")
# 		#     ad_child.expense_account = item_row.get("expense_account")
# 		#     ad_child.account_currency = work_order.currency
# 		#     frappe.msgprint(f"🔍 About to calculate ad_child.amount for {item_row.item_code}")
# 		#     ad_child.amount = flt(
# 		#         item_row.get("required_qty"), se_child.precision("qty")
# 		#     ) * item_row.get("rate")
# 		#     frappe.msgprint(f"✅ ad_child.amount calculated: {ad_child.amount}")
# 		#     ad_child.description = item_row.item_name
# 		#     ad_child.cost_center = (
# 		#         item_row.get("cost_center")
# 		#         or work_order.get("cost_center")
# 		#         or get_default_cost_center(item_row, company=work_order.company)
# 		#     )

# 		else:
# 			# frappe.msgprint(f"⚠️ Non-Stock Item Detected for {item_row.item_code}")

# 			ad_child = stock_entry.append("additional_costs")
# 			ad_child.expense_account = item_row.get("expense_account")
# 			ad_child.account_currency = work_order.currency

# 			qty = flt(item_row.get("required_qty") or 0)
# 			rate = flt(item_row.get("rate") or 0)

# 			ad_child.amount = qty * rate
# 			# frappe.msgprint(f"✅ ad_child.amount calculated: {ad_child.amount}")

# 			ad_child.description = item_row.item_name
# 			ad_child.cost_center = (
# 				item_row.get("cost_center")
# 				or work_order.get("cost_center")
# 				or get_default_cost_center(item_row, company=work_order.company)
# 			)

# 	add_child = work_order.custom_additional_costs
# 	if add_child:
# 		for item_row in add_child:
# 			ad_child = stock_entry.append("additional_costs")
# 			ad_child.expense_account = item_row.get("expense_account")
# 			ad_child.account_currency = work_order.currency
# 			ad_child.amount = flt(item_row.get("amount") or 0)
# 			ad_child.description = item_row.get("description")
	

# 	work_order.total_incoming_value = cost
# 	for scrap_item in work_order.scrap_items:
# 		stock_uom = scrap_item.get("stock_uom") or frappe.db.get_value(
# 			"Item", scrap_item.item_code, "stock_uom"
# 		)
# 		if not scrap_item.qty:
# 			continue
# 		cost -= scrap_item.qty * (
# 			scrap_item.rate if not scrap_item.is_process_loss else 0
# 		)
# 		se_child = stock_entry.append("items")
# 		se_child.t_warehouse = work_order.scrap_warehouse or work_order.fg_warehouse
# 		se_child.item_code = scrap_item.get("item_code")
# 		se_child.uom = (
# 			scrap_item.get("stock_uom") if scrap_item.get("stock_uom") else stock_uom
# 		)
# 		se_child.stock_uom = stock_uom
# 		se_child.qty = flt(scrap_item.get("qty"), se_child.precision("qty"))
# 		se_child.subcontracted_item = scrap_item.get("main_item_code")
# 		se_child.basic_rate = scrap_item.rate if not scrap_item.is_process_loss else 0
# 		se_child.basic_amount = se_child.basic_rate * se_child.qty
# 		se_child.cost_center = (
# 			scrap_item.get("cost_center")
# 			or work_order.get("cost_center")
# 			or get_default_cost_center(item_row, company=work_order.company)
# 		)
# 		se_child.set_basic_rate_manually = 1
# 		se_child.is_scrap_item = 1
# 		se_child.is_finished_item = 0
# 		se_child.is_process_loss = scrap_item.is_process_loss

# 		# in stock uom
# 		se_child.conversion_factor = flt(scrap_item.get("conversion_factor")) or 1
# 		se_child.transfer_qty = flt(
# 			scrap_item.get("qty") * se_child.conversion_factor,
# 			se_child.precision("transferred_qty"),
# 		)

# 	base_manufacture_total_value = 0
# 	remain_cost_value = cost
# 	for i in work_order.manufacture_items:
# 		base_manufacture_total_value += i.qty * i.bom_cost

# 	for manufacture_item in work_order.manufacture_items:
# 		stock_uom = manufacture_item.get("stock_uom") or frappe.db.get_value(
# 			"Item", manufacture_item.item_code, "stock_uom"
# 		)
# 		if not manufacture_item.qty:
# 			continue
# 		se_child = stock_entry.append("items")
# 		amount = flt(
# 			flt(cost)
# 			/ flt(base_manufacture_total_value)
# 			* flt(manufacture_item.bom_cost)
# 			* flt(manufacture_item.qty),
# 			se_child.precision("basic_rate"),
# 		)
# 		rate = amount / manufacture_item.qty
# 		if rate >= remain_cost_value:
# 			rate = remain_cost_value
# 		remain_cost_value -= amount
# 		se_child.t_warehouse = work_order.fg_warehouse
# 		se_child.item_code = manufacture_item.get("item_code")
# 		se_child.uom = (
# 			manufacture_item.get("stock_uom")
# 			if manufacture_item.get("stock_uom")
# 			else stock_uom
# 		)
# 		se_child.stock_uom = stock_uom
# 		se_child.qty = flt(manufacture_item.get("qty"), se_child.precision("qty"))
# 		se_child.subcontracted_item = manufacture_item.get("main_item_code")
# 		se_child.basic_rate = rate
# 		se_child.basic_amount = rate * se_child.qty
# 		se_child.cost_center = (
# 			manufacture_item.get("cost_center")
# 			or work_order.get("cost_center")
# 			or get_default_cost_center(manufacture_item, company=work_order.company)
# 		)
# 		se_child.set_basic_rate_manually = 1

# 		# in stock uom
# 		se_child.conversion_factor = flt(manufacture_item.get("conversion_factor")) or 1
# 		se_child.transfer_qty = flt(
# 			manufacture_item.get("qty") * se_child.conversion_factor,
# 			se_child.precision("transfer_qty"),
# 		)

# 	stock_entry.insert(ignore_permissions=True)

# 	url = frappe.utils.get_url_to_form(stock_entry.doctype, stock_entry.name)
# 	frappe.msgprint(
# 		"Manufacture Entry Created <a href='{0}'>{1}</a>".format(url, stock_entry.name)
# 	)
# 	work_order.manufacture_entry = stock_entry.name
# 	work_order.status = "Completed"
# 	work_order.actual_end_date = now_datetime()
# 	work_order.save()
# 	work_order.notify_update()
# 	return stock_entry.name



@frappe.whitelist()
def make_manufacture_entry(work_order_id, manufacture_items=None):
	frappe.msgprint(f"🔍 Starting manufacture entry for {work_order_id}")

	import json

	cost = 0

	work_order = frappe.get_doc("Batch Work Order", work_order_id)

	frappe.msgprint(f"✅ Loaded Work Order: {work_order.name}")

	# ---------------------------------------------------------
	# Get manufacture quantities supplied from the dialog
	# ---------------------------------------------------------
	manufacture_qty_map = {}

	if manufacture_items:
		if isinstance(manufacture_items, str):
			manufacture_items = json.loads(manufacture_items)

		for row in manufacture_items:
			if row.get("name"):
				manufacture_qty_map[row.get("name")] = flt(
					row.get("qty") or 0
				)

	wip_warehouse = work_order.wip_warehouse

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.purpose = "Repack"
	stock_entry.stock_entry_type = "Repack"
	stock_entry.company = work_order.company
	stock_entry.use_multi_level_bom = work_order.use_multi_level_bom
	stock_entry.to_warehouse = work_order.fg_warehouse
	stock_entry.from_warehouse = wip_warehouse
	stock_entry.project = work_order.project
	stock_entry.posting_date = work_order.production_date or nowdate()
	stock_entry.set_posting_time = 1
	stock_entry.docstatus = 1

	# ---------------------------------------------------------
	# REQUIRED ITEMS
	# ---------------------------------------------------------
	for item_row in work_order.required_items:

		if item_row.is_stock_item:

			stock_uom = item_row.get("stock_uom") or frappe.db.get_value(
				"Item",
				item_row.item_code,
				"stock_uom"
			)

			if not item_row.consumed_qty:
				continue

			cost += item_row.consumed_qty * item_row.rate

			se_child = stock_entry.append("items")

			se_child.s_warehouse = wip_warehouse
			se_child.item_code = item_row.get("item_code")
			se_child.uom = (
				item_row.get("uom")
				if item_row.get("uom")
				else stock_uom
			)
			se_child.stock_uom = stock_uom

			se_child.qty = flt(
				item_row.get("consumed_qty"),
				se_child.precision("qty")
			)

			se_child.allow_alternative_item = item_row.get(
				"allow_alternative_item",
				0
			)

			se_child.subcontracted_item = item_row.get(
				"main_item_code"
			)

			se_child.basic_rate = item_row.get("rate")

			se_child.cost_center = (
				item_row.get("cost_center")
				or work_order.get("cost_center")
				or get_default_cost_center(
					item_row,
					company=work_order.company
				)
			)

			se_child.set_basic_rate_manually = 1

			se_child.conversion_factor = (
				flt(item_row.get("conversion_factor")) or 1
			)

			se_child.transfer_qty = flt(
				item_row.get("transferred_qty")
				* se_child.conversion_factor,
				se_child.precision("transferred_qty"),
			)

		else:

			ad_child = stock_entry.append("additional_costs")

			ad_child.expense_account = item_row.get(
				"expense_account"
			)

			ad_child.account_currency = work_order.currency

			qty = flt(
				item_row.get("required_qty") or 0
			)

			rate = flt(
				item_row.get("rate") or 0
			)

			ad_child.amount = qty * rate

			ad_child.description = item_row.item_name

			ad_child.cost_center = (
				item_row.get("cost_center")
				or work_order.get("cost_center")
				or get_default_cost_center(
					item_row,
					company=work_order.company
				)
			)

	# ---------------------------------------------------------
	# ADDITIONAL COSTS
	# ---------------------------------------------------------
	add_child = work_order.custom_additional_costs

	if add_child:

		for item_row in add_child:

			ad_child = stock_entry.append(
				"additional_costs"
			)

			ad_child.expense_account = item_row.get(
				"expense_account"
			)

			ad_child.account_currency = work_order.currency

			ad_child.amount = flt(
				item_row.get("amount") or 0
			)

			ad_child.description = item_row.get(
				"description"
			)

	# ---------------------------------------------------------
	# TOTAL INCOMING VALUE
	# ---------------------------------------------------------
	work_order.total_incoming_value = cost

	# ---------------------------------------------------------
	# SCRAP ITEMS
	# ---------------------------------------------------------
	for scrap_item in work_order.scrap_items:

		stock_uom = scrap_item.get(
			"stock_uom"
		) or frappe.db.get_value(
			"Item",
			scrap_item.item_code,
			"stock_uom"
		)

		if not scrap_item.qty:
			continue

		cost -= scrap_item.qty * (
			scrap_item.rate
			if not scrap_item.is_process_loss
			else 0
		)

		se_child = stock_entry.append("items")

		se_child.t_warehouse = (
			work_order.scrap_warehouse
			or work_order.fg_warehouse
		)

		se_child.item_code = scrap_item.get(
			"item_code"
		)

		se_child.uom = (
			scrap_item.get("stock_uom")
			if scrap_item.get("stock_uom")
			else stock_uom
		)

		se_child.stock_uom = stock_uom

		se_child.qty = flt(
			scrap_item.get("qty"),
			se_child.precision("qty")
		)

		se_child.subcontracted_item = scrap_item.get(
			"main_item_code"
		)

		se_child.basic_rate = (
			scrap_item.rate
			if not scrap_item.is_process_loss
			else 0
		)

		se_child.basic_amount = (
			se_child.basic_rate * se_child.qty
		)

		se_child.cost_center = (
			scrap_item.get("cost_center")
			or work_order.get("cost_center")
			or get_default_cost_center(
				scrap_item,
				company=work_order.company
			)
		)

		se_child.set_basic_rate_manually = 1
		se_child.is_scrap_item = 1
		se_child.is_finished_item = 0
		se_child.is_process_loss = scrap_item.is_process_loss

		se_child.conversion_factor = (
			flt(scrap_item.get("conversion_factor"))
			or 1
		)

		se_child.transfer_qty = flt(
			scrap_item.get("qty")
			* se_child.conversion_factor,
			se_child.precision("transferred_qty"),
		)

	# ---------------------------------------------------------
	# MANUFACTURE ITEMS
	# ---------------------------------------------------------

	base_manufacture_total_value = 0
	remain_cost_value = cost

	# First calculate total using the dialog quantities
	for i in work_order.manufacture_items:

		manufacture_qty = manufacture_qty_map.get(
			i.name,
			flt(i.qty)
		)

		if manufacture_qty <= 0:
			continue

		base_manufacture_total_value += (
			manufacture_qty * i.bom_cost
		)

	# ---------------------------------------------------------
	# Create Finished Goods items
	# ---------------------------------------------------------
	for manufacture_item in work_order.manufacture_items:

		# Use dialog quantity if supplied.
		# Otherwise use original Work Order quantity.
		manufacture_qty = manufacture_qty_map.get(
			manufacture_item.name,
			flt(manufacture_item.qty)
		)

		if manufacture_qty <= 0:
			continue

		stock_uom = manufacture_item.get(
			"stock_uom"
		) or frappe.db.get_value(
			"Item",
			manufacture_item.item_code,
			"stock_uom"
		)

		se_child = stock_entry.append("items")

		# -----------------------------------------------------
		# Calculate cost based on NEW manufacture quantity
		# -----------------------------------------------------
		if base_manufacture_total_value:

			amount = flt(
				flt(cost)
				/ flt(base_manufacture_total_value)
				* flt(manufacture_item.bom_cost)
				* flt(manufacture_qty),
				se_child.precision("basic_rate"),
			)

		else:
			amount = 0

		rate = (
			amount / manufacture_qty
			if manufacture_qty
			else 0
		)

		if rate >= remain_cost_value:
			rate = remain_cost_value

		remain_cost_value -= amount

		# -----------------------------------------------------
		# Finished Goods
		# -----------------------------------------------------
		se_child.t_warehouse = work_order.fg_warehouse

		se_child.item_code = manufacture_item.get( 
			"item_code"
		)

		se_child.uom = (
			manufacture_item.get("stock_uom")
			if manufacture_item.get("stock_uom")
			else stock_uom
		)

		se_child.stock_uom = stock_uom

		# IMPORTANT:
		# Use dialog quantity here
		se_child.qty = flt(
			manufacture_qty,
			se_child.precision("qty")
		)

		se_child.subcontracted_item = manufacture_item.get(
			"main_item_code"
		)

		se_child.basic_rate = rate

		se_child.basic_amount = (
			rate * se_child.qty
		)

		se_child.cost_center = (
			manufacture_item.get("cost_center")
			or work_order.get("cost_center")
			or get_default_cost_center(
				manufacture_item,
				company=work_order.company
			)
		)

		se_child.set_basic_rate_manually = 1

		# -----------------------------------------------------
		# Stock UOM
		# -----------------------------------------------------
		se_child.conversion_factor = (
			flt(
				manufacture_item.get(
					"conversion_factor"
				)
			) or 1
		)

		se_child.transfer_qty = flt(
			manufacture_qty
			* se_child.conversion_factor,
			se_child.precision("transfer_qty"),
		)

	# ---------------------------------------------------------
	# INSERT STOCK ENTRY
	# ---------------------------------------------------------
	stock_entry.insert(ignore_permissions=True)

	url = frappe.utils.get_url_to_form(
		stock_entry.doctype,
		stock_entry.name
	)

	frappe.msgprint(
		"Manufacture Entry Created "
		"<a href='{0}'>{1}</a>".format(
			url,
			stock_entry.name
		)
	)

	# ---------------------------------------------------------
	# COMPLETE WORK ORDER
	# ---------------------------------------------------------
	work_order.manufacture_entry = stock_entry.name
	work_order.status = "Completed"
	work_order.actual_end_date = now_datetime()

	work_order.save()

	work_order.notify_update()

	return stock_entry.name


def get_item_bom(item_code):
	boms = frappe.get_all(
		"BOM",
		filters={"item": item_code, "is_active": 1},
		order_by="is_default asc",
		page_length=1,
	)
	return boms[0].name if boms else None


@frappe.whitelist()
def update_status(work_order_id, status):
	work_order = frappe.get_doc("Batch Work Order", work_order_id)
	work_order.status = status
	work_order.save()
	work_order.notify_update()
	return "success"
