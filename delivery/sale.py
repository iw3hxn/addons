# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
from osv import fields,osv

import time
from openerp.osv import fields, osv
from openerp.tools.translate import _


class sale_order_line(osv.Model):
    _inherit = 'sale.order.line'

    _columns = {
        'is_delivery': fields.boolean("Is a Delivery"),
    }

    _defaults = {
        'is_delivery': False
    }


class sale_order(osv.Model):
    _inherit = 'sale.order'
    _columns = {
        'carrier_id':fields.many2one("delivery.carrier", "Delivery Method", help="Complete this field if you plan to invoice the shipping based on picking."),
        'id': fields.integer('ID', readonly=True,invisible=True),
    }

    def onchange_partner_id(self, cr, uid, ids, part, context=None):
        result = super(sale_order, self).onchange_partner_id(cr, uid, ids, part)
        if part:
            dtype = self.pool['res.partner'].browse(cr, uid, part, context=context).property_delivery_carrier.id
            # TDE NOTE: not sure the aded 'if dtype' is valid
            if dtype:
                result['value']['carrier_id'] = dtype
        return result

    def _prepare_order_picking(self, cr, uid, order, context=None):
        result = super(sale_order, self)._prepare_order_picking(cr, uid, order, context=context)
        result.update(carrier_id=order.carrier_id.id)
        return result

    def _delivery_unset(self, cr, uid, ids, context=None):
        sale_obj = self.pool['sale.order.line']
        line_ids = sale_obj.search(cr, uid, [('order_id', 'in', ids), ('is_delivery', '=', True)], context=context)
        sale_obj.unlink(cr, uid, line_ids, context=context)

