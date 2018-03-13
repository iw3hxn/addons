# -*- coding: utf-8 -*-
# © 2017 Antonio Mignolli - Didotech srl (www.didotech.com)

from osv import fields, osv
from osv.orm import except_orm
from tools.translate import _
import netsvc
import tools
from tools import float_compare
import decimal_precision as dp
import logging

class StockMove(osv.osv):
    _inherit = 'stock.move'


    def action_consume(self, cr, uid, ids, quantity, location_id=False, context=None):
        """ Consumed product with specific quatity from specific source location
        @param cr: the database cursor
        @param uid: the user id
        @param ids: ids of stock move object to be consumed
        @param quantity : specify consume quantity
        @param location_id : specify source location
        @param context: context arguments
        @return: Consumed lines
        """
        #quantity should in MOVE UOM
        if context is None:
            context = {}
        if quantity <= 0:
            raise osv.except_osv(_('Warning!'), _('Please provide Proper Quantity !'))
        res = []
        for move in self.browse(cr, uid, ids, context=context):
            move_qty = move.product_qty
            if move_qty <= 0:
                raise osv.except_osv(_('Error!'), _('Can not consume a move with negative or zero quantity !'))
            quantity_rest = move.product_qty
            quantity_rest -= quantity
            if quantity_rest < 0:
                quantity_rest = -quantity_rest
            uos_qty_rest = quantity_rest / move_qty * move.product_uos_qty
            if quantity_rest == 0:
                quantity_rest = 0
                uos_qty_rest = 0
                quantity = move.product_qty

            uos_qty = quantity / move_qty * move.product_uos_qty
            if quantity_rest > 0:
                default_val = {
                    'product_qty': quantity,
                    'product_uos_qty': uos_qty,
                    'state': move.state,
                    'location_id': location_id or move.location_id.id,
                }
                current_move = self.copy(cr, uid, move.id, default_val)
                res += [current_move]
                update_val = {}
                update_val['product_qty'] = quantity_rest
                update_val['product_uos_qty'] = uos_qty_rest
                self.write(cr, uid, [move.id], update_val)

            else:
                quantity_rest = quantity
                uos_qty_rest = uos_qty
                res += [move.id]
                update_val = {
                        'product_qty': quantity_rest,
                        'product_uos_qty': uos_qty_rest,
                        'location_id': location_id or move.location_id.id,
                }
                self.write(cr, uid, [move.id], update_val)

            product_obj = self.pool.get('product.product')
            for new_move in self.browse(cr, uid, res, context=context):
                for (id, name) in product_obj.name_get(cr, uid, [new_move.product_id.id]):
                    message = _("Product  '%s' is consumed with '%s' quantity.") %(name, new_move.product_qty)
                    self.log(cr, uid, new_move.id, message)
        self.action_done(cr, uid, res, context=context)

        return res
