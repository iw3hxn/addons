##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
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
import decimal_precision as dp


class sale_order_line(osv.osv):
    _inherit = "sale.order.line"

    def product_id_change(self, cr, uid, ids, pricelist, product, qty=0,
            uom=False, qty_uos=0, uos=False, name='', partner_id=False,
            lang=False, update_tax=True, date_order=False, packaging=False, fiscal_position=False, flag=False, context=None):
        res = super(sale_order_line, self).product_id_change(cr, uid, ids, pricelist, product, qty=qty,
            uom=uom, qty_uos=qty_uos, uos=uos, name=name, partner_id=partner_id,
            lang=lang, update_tax=update_tax, date_order=date_order, packaging=packaging, fiscal_position=fiscal_position, flag=flag, context=context)
        if not pricelist:
            return res
        frm_cur = self.pool.get('res.users').browse(cr, uid, uid, context).company_id.currency_id.id
        to_cur = self.pool.get('product.pricelist').browse(cr, uid, [pricelist], context)[0].currency_id.id
        if product:
            purchase_price = self.pool.get('product.product').browse(cr, uid, product, context).standard_price
            price = self.pool.get('res.currency').compute(cr, uid, frm_cur, to_cur, purchase_price, round=False)
            res['value'].update({'purchase_price': price})
        return res

    def _product_margin(self, cr, uid, ids, field_name, arg, context=None):
        res = {}
        price_subtotal = self._amount_line(cr, uid, ids, field_name, arg, context)
        for line in self.browse(cr, uid, ids, context=context):
            res[line.id] = {
                'margin': 0,
                'total_purchase_price': 0,
            }
            price_subtotal_line = price_subtotal[line.id]
            if line.purchase_price:
                total_purchase_price = line.product_uos_qty * line.purchase_price
            elif line.product_id:
                total_purchase_price = line.product_uos_qty * line.product_id.standard_price
            else:
                total_purchase_price = 0

            res[line.id] = {
                'margin': round(price_subtotal_line - total_purchase_price, 2),
                'total_purchase_price': total_purchase_price,
            }
        return res

    _columns = {
        'margin': fields.function(_product_margin, string='Margin', multi='sums', type='float', digits_compute=dp.get_precision('Account'), store={
            'sale.order.line': (lambda self, cr, uid, ids, c={}: ids, ['price_unit', 'product_uos_qty', 'discount', 'purchase_price', 'product_id'], 100),
        }),
        'total_purchase_price': fields.function(_product_margin, multi='sums', type='float', string='Total Cost Price', digits_compute=dp.get_precision('Account'), store={
            'sale.order.line': (lambda self, cr, uid, ids, c={}: ids, ['price_unit', 'product_uos_qty', 'discount', 'purchase_price', 'product_id'], 100),
        }),
        'purchase_price': fields.float('Cost Price', digits=(16, 2))
    }


class sale_order(osv.osv):
    _inherit = "sale.order"

    def _product_margin(self, cr, uid, ids, field_name, arg, context=None):
        result = {}
        for sale in self.browse(cr, uid, ids, context=context):
            result[sale.id] = {
                'margin': 0.0,
                'margin_rel': 0.0,
            }
            sale_order_line_ids = []
            for line in sale.order_line:
                sale_order_line_ids.append(line.id)
            margins = self.pool['sale.order.line']._product_margin(cr, uid, sale_order_line_ids, field_name, arg, context=context)
            margin = 0
            for key in margins.keys():
                margin += margins[key]['margin']
            result[sale.id]['margin'] = margin

            if sale.amount_untaxed:
                result[sale.id]['margin_rel'] = (result[sale.id]['margin'] / sale.amount_untaxed) * 100
        return result

    def _get_order(self, cr, uid, ids, context=None):
        parent_get_order = super(sale_order, self.pool.get('sale.order'))._get_order.im_func
        return parent_get_order(self, cr, uid, ids, context=context)

    _columns = {
        'margin': fields.function(_product_margin, string='Margin', type='float', multi='sums', digits_compute=dp.get_precision('Account'), help="It gives profitability by calculating the difference between the Unit Price and Cost Price.", store={
                'sale.order.line': (_get_order, ['product_id', 'price_unit', 'tax_id', 'discount', 'product_uom_qty', 'purchase_price'], 190),
                'sale.order': (lambda self, cr, uid, ids, c={}: ids, ['order_line', 'state'], 200),
        }),
        'margin_rel': fields.function(_product_margin, string='Margin %', type='float', multi='sums', digits_compute=dp.get_precision('Account'), store={
                'sale.order.line': (_get_order, ['product_id', 'price_unit', 'tax_id', 'discount', 'product_uom_qty', 'purchase_price'], 190),
                'sale.order': (lambda self, cr, uid, ids, c={}: ids, ['order_line', 'state'], 200),
        }),
    }

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
