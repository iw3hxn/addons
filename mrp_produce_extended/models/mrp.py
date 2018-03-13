# -*- coding: utf-8 -*-
# © 2017 Antonio Mignolli - Didotech srl (www.didotech.com)

import logging
from operator import attrgetter

import netsvc
import tools
from osv import osv
from tools import float_compare
from tools.translate import _

_logger = logging.getLogger(__name__)


#----------------------------------------------------------
# Work Centers
#----------------------------------------------------------
# capacity_hour : capacity per hour. default: 1.0.
#          Eg: If 5 concurrent operations at one time: capacity = 5 (because 5 employees)
# unit_per_cycle : how many units are produced for one cycle


def rounding(f, r):
    import math
    if not r:
        return f
    return math.ceil(f / r) * r

class mrp_production(osv.osv):
    """
    Production Orders / Manufacturing Orders
    """
    _inherit = 'mrp.production'
    _description = 'Manufacturing Order Extension'

    def add_prod_qty(self, cr, uid, production_id, product_qty, produced_qty, context=None):
        # From change_prod_qty
        prod_obj = self.pool.get('mrp.production')
        bom_obj = self.pool.get('mrp.bom')
        move_lines_obj = self.pool.get('stock.move')

        requested_qty = product_qty + produced_qty

        # Adapted from change_production.py
        prod = prod_obj.browse(cr, uid, production_id, context=context)
        prod_obj.write(cr, uid, prod.id, {'product_qty': requested_qty})
        prod_obj.action_compute(cr, uid, [prod.id])
        move_lines = prod.move_lines
        move_lines.extend(prod.picking_id.move_lines)

        move_lines_obj = self.pool.get('stock.move')
        for move in move_lines:
            bom_point = prod.bom_id
            bom_id = prod.bom_id.id
            if not bom_point:
                bom_id = bom_obj._bom_find(cr, uid, prod.product_id.id, prod.product_uom.id)
                if not bom_id:
                    raise osv.except_osv(_('Error'), _("Couldn't find bill of material for product"))
                prod_obj.write(cr, uid, [prod.id], {'bom_id': bom_id})
                bom_point = bom_obj.browse(cr, uid, [bom_id])[0]

            if not bom_id:
                raise osv.except_osv(_('Error'), _("Couldn't find bill of material for product"))

            factor = prod.product_qty * prod.product_uom.factor / bom_point.product_uom.factor
            res = bom_obj._bom_explode(cr, uid, bom_point, factor / bom_point.product_qty, [])
            for r in res[0]:
                if r['product_id'] == move.product_id.id:
                    move_lines_obj.write(cr, uid, [move.id], {'product_qty':  r['product_qty']})

        # It was self._update_product_to_produce(cr, uid, prod, wiz_qty.product_qty, context=context)
        for m in prod.move_created_ids:
            m.write({'product_qty': product_qty})
            # move_lines_obj.write(cr, uid, [m.id], {'product_qty': requested_qty})

        production = self.browse(cr, uid, production_id, context=context)
        return production

    def action_produce(self, cr, uid, production_id, production_qty, production_mode, context=None):
        """ To produce final product based on production mode (consume/consume&produce).
        If Production mode is consume, all stock move lines of raw materials will be done/consumed.
        If Production mode is consume & produce, all stock move lines of raw materials will be done/consumed
        and stock move lines of final product will be also done/produced.
        @param production_id: the ID of mrp.production object
        @param production_qty: specify qty to produce
        @param production_mode: specify production mode (consume/consume&produce).
        @return: True
        """
        stock_mov_obj = self.pool.get('stock.move')
        production = self.browse(cr, uid, production_id, context=context)

        produced_qty = 0
        for produced_product in production.move_created_ids2:
            if (produced_product.scrapped) or (produced_product.product_id.id <> production.product_id.id):
                continue
            produced_qty += produced_product.product_qty

        if production_mode in ['consume', 'consume_produce']:
            consumed_data = {}

            # Calculate already consumed qtys
            for consumed in production.move_lines2:
                if consumed.scrapped:
                    continue
                if not consumed_data.get(consumed.product_id.id, False):
                    consumed_data[consumed.product_id.id] = 0
                consumed_data[consumed.product_id.id] += consumed.product_qty

            # Find product qty to be consumed and consume it
            for scheduled in production.product_lines:

                # total qty of consumed product we need after this consumption
                total_consume = ((production_qty + produced_qty) * scheduled.product_qty / production.product_qty)

                # qty available for consume and produce
                qty_avail = scheduled.product_qty - consumed_data.get(scheduled.product_id.id, 0.0)

                if qty_avail <= 0.0:
                    # there will be nothing to consume for this raw material
                    continue

                raw_product = [move for move in production.move_lines if move.product_id.id == scheduled.product_id.id]
                if raw_product:
                    # qtys we have to consume
                    qty = total_consume - consumed_data.get(scheduled.product_id.id, 0.0)
                    if float_compare(qty, qty_avail, precision_rounding=scheduled.product_id.uom_id.rounding) == 1:
                        # if qtys we have to consume is more than qtys available to consume
                        # => must consume the difference, move from warehouse
                        _logger.info(
                            'action_produce: Manufacturing Order {prod.name}, Consuming {requested}, more than initial {prod.product_qty}'
                            .format(prod=production, requested=production_qty))

                    if qty <= 0.0:
                        # we already have more qtys consumed than we need
                        continue

                    consumed = 0
                    rounding = raw_product[0].product_uom.rounding

                    # sort the list by quantity, to consume smaller quantities first and avoid splitting if possible
                    raw_product.sort(key=attrgetter('product_qty'))

                    # search for exact quantity
                    for consume_line in raw_product:
                        if tools.float_compare(consume_line.product_qty, qty, precision_rounding=rounding) == 0:
                            # consume this line
                            consume_line.action_consume(qty, consume_line.location_id.id, context=context)
                            consumed = qty
                            break

                    index = 0                        
                    # consume the smallest quantity while we have not consumed enough
                    while tools.float_compare(consumed, qty, precision_rounding=rounding) == -1 and index < len(raw_product):
                        consume_line = raw_product[index]
                        to_consume = min(consume_line.product_qty, qty - consumed) 
                        consume_line.action_consume(to_consume, consume_line.location_id.id, context=context)
                        consumed += to_consume
                        index += 1

        if production_mode == 'consume_produce':
            # To produce remaining qty of final product
            #vals = {'state':'confirmed'}
            #final_product_todo = [x.id for x in production.move_created_ids]
            #stock_mov_obj.write(cr, uid, final_product_todo, vals)
            #stock_mov_obj.action_confirm(cr, uid, final_product_todo, context)
            produced_products = {}
            produced_products_qty = 0
            if production.move_created_ids2:
                produced_products_qty = production.move_created_ids2[0].product_qty

            for produced_product in production.move_created_ids2:
                if produced_product.scrapped:
                    continue
                if not produced_products.get(produced_product.product_id.id, False):
                    produced_products[produced_product.product_id.id] = 0
                produced_products[produced_product.product_id.id] += produced_product.product_qty

            for produce_product in production.move_created_ids:
                produced_qty = produced_products.get(produce_product.product_id.id, 0)
                subproduct_factor = self._get_subproduct_factor(cr, uid, production.id, produce_product.id, context=context)
                rest_qty = (subproduct_factor * production.product_qty) - produced_qty

                if rest_qty < production_qty:
                    _logger.info(
                        'action_produce: Manufacturing Order {prod.name}, Producing {requested}, more than initial {prod.product_qty}'
                        .format(prod=production, requested=production_qty))
                    # Add and Reload
                    production = self.add_prod_qty(cr, uid, production.id, production_qty, produced_products_qty, context)

                if rest_qty > 0:
                    stock_mov_obj.action_consume(cr, uid, [produce_product.id], (subproduct_factor * production_qty), context=context)

        for raw_product in production.move_lines2:
            new_parent_ids = []
            parent_move_ids = [x.id for x in raw_product.move_history_ids]
            for final_product in production.move_created_ids2:
                if final_product.id not in parent_move_ids:
                    new_parent_ids.append(final_product.id)
            for new_parent_id in new_parent_ids:
                stock_mov_obj.write(cr, uid, [raw_product.id], {'move_history_ids': [(4, new_parent_id)]})

        wf_service = netsvc.LocalService("workflow")
        wf_service.trg_validate(uid, 'mrp.production', production_id, 'button_produce_done', cr)
        return True

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
