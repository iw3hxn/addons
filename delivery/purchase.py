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
from osv import fields, osv


# Overloaded sale_order to manage carriers :
class purchase_order(osv.osv):
    _inherit = 'purchase.order'
    _columns = {
        'carrier_id': fields.many2one("delivery.carrier", "Delivery Method", help="Complete this field if you plan to invoice the shipping based on picking."),
    }

    def onchange_partner_id(self, cr, uid, ids, partner_id):
        result = super(purchase_order, self).onchange_partner_id(cr, uid, ids, partner_id)
        if partner_id:
            dtype = self.pool['res.partner'].browse(cr, uid, partner_id).property_delivery_carrier.id
            result['value']['carrier_id'] = dtype
        return result

    # def _prepare_order_picking(self, cr, uid, order, context=None):
    #     result = super(sale_order, self)._prepare_order_picking(cr, uid, order, context=context)
    #     result.update(carrier_id=order.carrier_id.id)
    #     return result