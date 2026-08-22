from erpnext.stock.doctype.stock_entry.stock_entry import *


class StockEntryOverride(StockEntry):
    def validate(self):
        super(StockEntryOverride, self).validate()
        self.recalculate_repack()

    def recalculate_repack(self):
        if self.purpose=='Repack':
            balance_stock_repack = frappe.db.get_single_value('Manufacturing Plus Setting', 'balance_stock_repack')
            if balance_stock_repack:
                self.balance_stock_repack = balance_stock_repack
                # calculate outgoing and incoming value
                if self.total_outgoing_value > self.total_incoming_value:
                    # spread the cost to incoming value
                    diff = self.total_outgoing_value - self.total_incoming_value
                    for i in self.items:
                        if i.t_warehouse:
                            per = (i.amount/self.total_incoming_value)
                            i.amount = i.amount + (diff*per)
                            i.valuation_rate = i.amount/i.qty
                    self.total_incoming_value = sum([i.amount for i in self.items if i.t_warehouse])
                    
                elif self.total_outgoing_value < self.total_incoming_value:
                    # spread the cost to incoming value
                    diff = self.value_difference
                    for i in self.items:
                        if i.s_warehouse:
                            per = (i.amount/self.total_outgoing_value)
                            i.amount = i.amount + (diff*per)
                            i.valuation_rate = i.amount/i.qty
                    self.total_outgoing_value = sum([i.amount for i in self.items if i.s_warehouse])
                
                self.value_difference = self.total_outgoing_value - self.total_incoming_value