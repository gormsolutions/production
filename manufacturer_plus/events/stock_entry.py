import frappe



def validate(doc, event=None):
    # check repack
    doc = recalculate_repack(doc)

def recalculate_repack(doc):
    if doc.stock_entry_type=='Repack':
        balance_stock_repack = frappe.db.get_single_value('Manufacturing Plus Setting', 'balance_stock_repack')
        if balance_stock_repack:
            doc.balance_stock_repack = balance_stock_repack
            # calculate outgoing and incoming value
            if doc.total_outgoing_value > doc.total_incoming_value:
                print('h_ware',)
                # spread the cost to incoming value
                diff = doc.total_outgoing_value - doc.total_incoming_value
                for i in doc.items:
                    if i.s_warehouse:
                        inper = (i.amount/doc.total_incoming_value)
                        i.amount = i.amount + (i.amount*inper)
                        i.valuation_rate = i.amount/i.qty
                        print(inper)
                doc.total_incoming_value = sum([i.amount for i in doc.items if i.s_warehouse])
                print(doc.total_incoming_value,  doc.total_outgoing_value, sum([i.amount for i in doc.items if i.s_warehouse]), sum([i.amount for i in doc.items if i.t_warehouse]),'recalcu;ated\n\n')
                
            elif doc.total_outgoing_value < doc.total_incoming_value:
                print('t_ware',)
                # spread the cost to incoming value
                diff = doc.total_incoming_value - doc.total_outgoing_value
                for i in doc.items:
                    if i.t_warehouse:
                        outper = (i.amount/doc.total_outgoing_value)
                        i.amount = i.amount + (i.amount*outper)
                        i.valuation_rate = i.amount/i.qty
                        print(outper)
                doc.total_outgoing_value = sum([i.amount for i in doc.items if i.t_warehouse])
            
                print(doc.total_incoming_value,  doc.total_outgoing_value, sum([i.amount for i in doc.items if i.s_warehouse]), sum([i.amount for i in doc.items if i.t_warehouse]), 'recalcu;ated\n\n')
    return doc
