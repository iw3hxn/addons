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

import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
import logging
from PIL import Image

import netsvc
from osv import fields, osv
from tools.translate import _
from decimal import Decimal
import decimal_precision as dp
import io, StringIO

_logger = logging.getLogger(__name__)


class pos_config_journal(osv.osv):
    """ Point of Sale journal configuration"""
    _name = 'pos.config.journal'
    _description = "Journal Configuration"

    _columns = {
        'name': fields.char('Description', size=64),
        'code': fields.char('Code', size=64),
        'journal_id': fields.many2one('account.journal', "Journal")
    }


class sale_shop(osv.osv):
    _inherit = 'sale.shop'
    _columns = {
        'pos_user_id': fields.many2one('res.users', 'POS User'),
        'product_customer_id': fields.many2one('product.product', 'Product for Fidelity Card', domain="[('type', '=', 'service')]"),
        'fiscal_position_id': fields.many2one('account.fiscal.position', 'Fiscal Position', domain="[('customer', '=', True)]"),
        'property_account_receivable': fields.property(
            'account.account',
            type='many2one',
            relation='account.account',
            string="Account Receivable",
            view_load=True,
            domain="[('type', '=', 'receivable')]",
            help="This account will be used instead of the default one as the receivable account for the current shop" ),
    }

    _defaults = {
        'pos_user_id': lambda obj, cr, uid, context: uid,
    }

    _sql_constraints = [
        ('pos_user_id_uniq', 'unique(pos_user_id)', 'iPos User must be unique!'),
    ]

    def copy(self, cr, uid, id, default=None, context=None):
        default = default or {}
        default.update({
            'pos_user_id': False,
        })
        return super(sale_shop, self).copy(cr, uid, id, default, context)


class pos_order(osv.osv):
    _name = "pos.order"
    _description = "Point of Sale"
    _order = "id desc"
    
    def create_from_ui(self, cr, uid, orders, context=None):
        #_logger.info("orders: %r", orders)
        list = []
        for order in orders:
            # order :: {'name': 'Order 1329148448062', 'amount_paid': 9.42, 'lines': [[0, 0, {'discount': 0, 'price_unit': 1.46, 'product_id': 124, 'qty': 5}], [0, 0, {'discount': 0, 'price_unit': 0.53, 'product_id': 62, 'qty': 4}]], 'statement_ids': [[0, 0, {'journal_id': 7, 'amount': 9.42, 'name': '2012-02-13 15:54:12', 'account_id': 12, 'statement_id': 21}]], 'amount_tax': 0, 'amount_return': 0, 'amount_total': 9.42}
            order_obj = self.pool.get('pos.order')
            # get statements out of order because they will be generated with add_payment to ensure
            # the module behavior is the same when using the front-end or the back-end
            statement_ids = order.pop('statement_ids')
            order_id = self.create(cr, uid, order, context)
            list.append(order_id)
            for payments in statement_ids:
                # call add_payment; refer to wizard/pos_payment for data structure
                # add_payment launches the 'paid' signal to advance the workflow to the 'paid' state
                payment = payments[2]
                order_obj.add_payment(cr, uid, order_id, {
                    'amount': payment['amount'],
                    'payment_name': order['name'],
                    'payment_date': payment['name'],
                    'journal': payment['journal_id'],
                }, context=context)
            if order['amount_return']:
                # search for open cash register of 'cash' journal
                statement_obj = self.pool.get('account.bank.statement')
                cash_registers_domain = [('state','=','open'),('user_id','=',uid),('journal_id.type','=','cash')]
                cash_register_ids = statement_obj.search(cr, uid, cash_registers_domain, context=context)
                if not len(cash_register_ids):
                    raise osv.except_osv( _('Error!'),
                            _("No cash statement found for this session. Unable to record returned cash."))
                cash_register = statement_obj.browse(cr, uid, cash_register_ids[0], context=context)
                self.add_payment(cr, uid, order_id, {
                    'amount': -order['amount_return'],
                    'payment_date': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'payment_name': _('return'),
                    'journal': cash_register.journal_id.id,
                }, context=context)
        return list

    def unlink(self, cr, uid, ids, context=None):
        for rec in self.browse(cr, uid, ids, context=context):
            if rec.state not in ('draft','cancel'):
                raise osv.except_osv(_('Unable to Delete !'), _('In order to delete a sale, it must be new or cancelled.'))
        return super(pos_order, self).unlink(cr, uid, ids, context=context)

    def onchange_partner_id(self, cr, uid, ids, part=False, context=None):
        if not part:
            return {'value': {}}
        pricelist = self.pool.get('res.partner').browse(cr, uid, part, context=context).property_product_pricelist.id
        return {'value': {'pricelist_id': pricelist}}

    def _amount_all(self, cr, uid, ids, name, args, context=None):
        #tax_obj = self.pool.get('account.tax')
        cur_obj = self.pool.get('res.currency')
        res = {}
        for order in self.browse(cr, uid, ids, context=context):
            res[order.id] = {
                'amount_paid': 0.0,
                'amount_return':0.0,
                'amount_tax':0.0,
            }
            val1 = val2 = 0.0
            cur = order.pricelist_id.currency_id
            for payment in order.statement_ids:
                res[order.id]['amount_paid'] += payment.amount
                res[order.id]['amount_return'] += (payment.amount < 0 and payment.amount or 0)
            for line in order.lines:
                val1 += line.price_subtotal_incl
                val2 += line.price_subtotal
            res[order.id]['amount_tax'] = cur_obj.round(cr, uid, cur, val1-val2)
            res[order.id]['amount_total'] = cur_obj.round(cr, uid, cur, val1)
        return res

    def _default_sale_journal(self, cr, uid, context=None):
        res = self.pool.get('account.journal').search(cr, uid, [('type', '=', 'sale')], limit=1)
        return res and res[0] or False

    def _default_shop(self, cr, uid, context=None):
        res = self.pool.get('sale.shop').search(cr, uid, [('pos_user_id', '=', uid)] )
        return res and res[0] or False

    def copy(self, cr, uid, id, default=None, context=None):
        if not default:
            default = {}
        d = {
            'state': 'draft',
            'invoice_id': False,
            'account_move': False,
            'picking_id': False,
            'statement_ids': [],
            'nb_print': 0,
            'name': self.pool.get('ir.sequence').get(cr, uid, 'pos.order'),
        }
        d.update(default)
        return super(pos_order, self).copy(cr, uid, id, d, context=context)

    _columns = {
        'date_from': fields.function(lambda *a, **k: {}, method=True, type='date', string="Date from"),
        'date_to': fields.function(lambda *a, **k: {}, method=True, type='date', string="Date to"),
        'active': fields.boolean('Active'),
        'name': fields.char('Order Ref', size=64, required=True,
            states={'draft': [('readonly', False)]}, readonly=True),
        'company_id': fields.many2one('res.company', 'Company', required=True, readonly=True),
        'shop_id': fields.many2one('sale.shop', 'Shop', required=True,
            states={'draft': [('readonly', False)]}, readonly=True),
        'date_order': fields.datetime('Date Ordered', readonly=True, select=True),
        'user_id': fields.many2one('res.users', 'Connected Salesman', help="Person who uses the the cash register. It could be a reliever, a student or an interim employee."),
        'amount_tax': fields.function(_amount_all, string='Taxes', digits_compute=dp.get_precision('Point Of Sale'), multi='all'),
        'amount_total': fields.function(_amount_all, string='Total', multi='all'),
        'amount_paid': fields.function(_amount_all, string='Paid', states={'draft': [('readonly', False)]}, readonly=True, digits_compute=dp.get_precision('Point Of Sale'), multi='all'),
        'amount_return': fields.function(_amount_all, 'Returned', digits_compute=dp.get_precision('Point Of Sale'), multi='all'),
        'lines': fields.one2many('pos.order.line', 'order_id', 'Order Lines', states={'draft': [('readonly', False)]}, readonly=True),
        'statement_ids': fields.one2many('account.bank.statement.line', 'pos_statement_id', 'Payments', states={'draft': [('readonly', False)]}, readonly=True),
        'pricelist_id': fields.many2one('product.pricelist', 'Pricelist', required=True, states={'draft': [('readonly', False)]}, readonly=True),
        'partner_id': fields.many2one('res.partner', 'Customer', change_default=True, select=1, states={'draft': [('readonly', False)], 'paid': [('readonly', False)]}),

        'state': fields.selection([('draft', 'New'),
                                   ('cancel', 'Cancelled'),
                                   ('paid', 'Paid'),
                                   ('done', 'Posted'),
                                   ('invoiced', 'Invoiced')],
                                  'State', readonly=True),

        'invoice_id': fields.many2one('account.invoice', 'Invoice'),
        'account_move': fields.many2one('account.move', 'Journal Entry', readonly=True),
        'picking_id': fields.many2one('stock.picking', 'Picking', readonly=True),
        'note': fields.text('Internal Notes'),
        'nb_print': fields.integer('Number of Print', readonly=True),
        'sale_journal': fields.many2one('account.journal', 'Journal', required=True, states={'draft': [('readonly', False)]}, readonly=True),
        'pos_discount': fields.boolean('Discount?')
    }

    def _default_pricelist(self, cr, uid, context=None):
#        res = self.pool.get('sale.shop').search(cr, uid, [], context=context)
        res = self._default_shop(cr, uid, context=None)
        if res:
            shop = self.pool.get('sale.shop').browse(cr, uid, res, context=context)
            return shop.pricelist_id and shop.pricelist_id.id or False
        return False

    _defaults = {
        'user_id': lambda self, cr, uid, context: uid,
        'state': 'draft',
        'name': '/', #  lambda obj, cr, uid, context: obj.pool.get('ir.sequence').get(cr, uid, 'pos.order'),
        'date_order': lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'),
        'nb_print': 0,
        'company_id': lambda self,cr,uid,c: self.pool.get('res.users').browse(cr, uid, uid, c).company_id.id,
        'sale_journal': _default_sale_journal,
        'shop_id': _default_shop,
        'pricelist_id': _default_pricelist,
        'active': 1,
    }

    def create(self, cr, uid, vals, context=None):
        if not context:
            context = {}

        if vals.get('name', '/') == '/':
            vals.update({'name': self.pool['ir.sequence'].get(cr, uid, 'pos.order', context=context)})
        return super(pos_order, self).create(cr, uid, vals, context=context)

    def test_paid(self, cr, uid, ids, context=None):
        """A Point of Sale is paid when the sum
        @return: True
        """
        if not context:
            context = {}
        for order in self.browse(cr, uid, ids, context=context):
            if order.lines and not order.amount_total:
                return True
            if context.get('force_paid', False):
                return True
            if (not order.lines) or (not order.statement_ids) or (abs(order.amount_total - order.amount_paid) > 1):
                return False
        return True

    def create_picking(self, cr, uid, ids, context=None):
        """Create a picking for each order and validate it."""
        picking_obj = self.pool['stock.picking']
        partner_obj = self.pool['res.partner']
        move_obj = self.pool['stock.move']
        # hr_employee_obj = self.pool.get('hr.employee') and self.pool['hr.employee'] or False

        for order in self.browse(cr, uid, ids, context=context):
            address = order.partner_id and partner_obj.address_get(cr, uid, [order.partner_id.id], ['delivery']) or {}
            picking_id = picking_obj.create(cr, uid, {
                'origin': order.name,
                'address_id': address.get('delivery', False),
                'type': 'out',
                'company_id': order.company_id.id,
                'move_type': 'direct',
                'note': order.note or "",
                'invoice_state': 'none',
                'auto_picking': True,
            }, context=context)
            self.write(cr, uid, [order.id], {'picking_id': picking_id}, context=context)
            location_id = order.shop_id.warehouse_id.lot_stock_id.id
            destination_id = order.shop_id.warehouse_id.lot_output_id.id

            for line in order.lines:
                if line.product_id and line.product_id.type == 'service' or line.employee_id:
                    continue

                price_unit = 0
                if line.qty != 0.0:
                    price_unit = line.price_subtotal / abs(line.qty)

                move_obj.create(cr, uid, {
                    'name': line.name[:250],
                    'product_uom': line.product_id.uom_id.id,
                    'product_uos': line.product_id.uom_id.id,
                    'picking_id': picking_id,
                    'product_id': line.product_id.id,
                    'product_uos_qty': abs(line.qty),
                    'product_qty': abs(line.qty),
                    'tracking_id': False,
                    'state': 'draft',
                    'location_id': location_id if line.qty >= 0 else destination_id,
                    'location_dest_id': destination_id if line.qty >= 0 else location_id,
                    'company_id': order.shop_id.company_id.id,
                    'price_unit': price_unit
                }, context=context)

            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate(uid, 'stock.picking', picking_id, 'button_confirm', cr)
            picking_obj.force_assign(cr, uid, [picking_id], context)
        return True

    def set_to_draft(self, cr, uid, ids, *args):
        context = {}
        if not len(ids):
            return False
        for order in self.browse(cr, uid, ids, context=context):
            if order.state != 'cancel':
                raise osv.except_osv(_('Error!'), _('In order to set to draft a sale, it must be cancelled.'))
        self.write(cr, uid, ids, {'state': 'draft'})
        wf_service = netsvc.LocalService("workflow")
        for i in ids:
            wf_service.trg_create(uid, 'pos.order', i, cr)
        return True

    def cancel_order(self, cr, uid, ids, context=None):
        """ Changes order state to cancel
        @return: True
        """
        stock_picking_obj = self.pool.get('stock.picking')
        for order in self.browse(cr, uid, ids, context=context):
            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate(uid, 'stock.picking', order.picking_id.id, 'button_cancel', cr)
            if stock_picking_obj.browse(cr, uid, order.picking_id.id, context=context).state != 'cancel':
                raise osv.except_osv(_('Error!'), _('Unable to cancel the picking.'))
        self.write(cr, uid, ids, {'state': 'cancel'}, context=context)
        return True

    def add_payment(self, cr, uid, order_id, data, context=None):
        """Create a new payment for the order"""
        statement_obj = self.pool.get('account.bank.statement')
        statement_line_obj = self.pool.get('account.bank.statement.line')
        # prod_obj = self.pool.get('product.product')
        property_obj = self.pool.get('ir.property')
        curr_c = self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id
        curr_company = curr_c.id
        order = self.browse(cr, uid, order_id, context=context)
        ids_new = []
        args = {
            'amount': data['amount'],
        }
        if 'payment_date' in data.keys():
            args['date'] = data['payment_date']
        args['name'] = order.name
        if data.get('payment_name', False):
            args['name'] = args['name'] + ': ' + data['payment_name']
        account_def = property_obj.get(cr, uid, 'property_account_receivable', 'res.partner', context=context)
        args['account_id'] = (order.partner_id and order.partner_id.property_account_receivable \
                             and order.partner_id.property_account_receivable.id) or order.shop_id and order.shop_id.property_account_receivable and order.shop_id.property_account_receivable.id or (account_def and account_def.id) or False
        args['partner_id'] = order.partner_id and order.partner_id.id or None

        if not args['account_id']:
            if not args['partner_id']:
                msg = _('There is no receivable account defined to make payment')
            else:
                msg = _('There is no receivable account defined to make payment for the partner: "%s" (id:%d)') % (order.partner_id.name, order.partner_id.id,)
            raise osv.except_osv(_('Configuration Error !'), msg)

        statement_id = statement_obj.search(cr, uid, [('journal_id', '=', int(data['journal'])),
                                                      ('company_id', '=', curr_company),
                                                      ('user_id', '=', uid),
                                                      ('state', '=', 'open')], context=context)

        if len(statement_id) == 0:
            raise osv.except_osv(_('Error !'), _('You have to open at least one cashbox'))
        if statement_id:
            statement_id = statement_id[0]
        args['statement_id'] = statement_id
        args['pos_statement_id'] = order_id
        args['journal_id'] = int(data['journal'])
        args['type'] = 'customer'
        args['ref'] = order.name
        statement_line_obj.create(cr, uid, args, context=context)
        ids_new.append(statement_id)

        wf_service = netsvc.LocalService("workflow")
        wf_service.trg_validate(uid, 'pos.order', order_id, 'paid', cr)
        wf_service.trg_write(uid, 'pos.order', order_id, cr)

        return statement_id

    def refund(self, cr, uid, ids, context=None):
        """Create a copy of order  for refund order"""
        clone_list = []
        line_obj = self.pool.get('pos.order.line')
        for order in self.browse(cr, uid, ids, context=context):
            clone_id = self.copy(cr, uid, order.id, {
                'name': order.name + ' REFUND',
            }, context=context)
            clone_list.append(clone_id)

        for clone in self.browse(cr, uid, clone_list, context=context):
            for order_line in clone.lines:
                line_obj.write(cr, uid, [order_line.id], {
                    'qty': -order_line.qty
                }, context=context)

        new_order = ','.join(map(str,clone_list))
        abs = {
            # 'domain': "[('id', 'in', ["+new_order+"])]",
            'name': _('Return Products'),
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'pos.order',
            'res_id':clone_list[0],
            'view_id': False,
            'context':context,
            'type': 'ir.actions.act_window',
            'nodestroy': True,
            'target': 'current',
        }
        return abs

    def action_invoice_state(self, cr, uid, ids, context=None):
        return self.write(cr, uid, ids, {'state':'invoiced'}, context=context)

    def action_invoice(self, cr, uid, ids, context=None):
        wf_service = netsvc.LocalService("workflow")
        inv_ref = self.pool.get('account.invoice')
        inv_line_ref = self.pool.get('account.invoice.line')
        product_obj = self.pool.get('product.product')
        inv_ids = []

        for order in self.pool.get('pos.order').browse(cr, uid, ids, context=context):
            if order.invoice_id:
                inv_ids.append(order.invoice_id.id)
                continue

            if not order.partner_id:
                raise osv.except_osv(_('Error'), _('Please provide a partner for the sale.'))

            acc = order.partner_id.property_account_receivable.id
            inv = {
                'name': order.name,
                'origin': order.name,
                'account_id': acc,
                'journal_id': order.sale_journal.id or None,
                'type': 'out_invoice',
                'reference': order.name,
                'partner_id': order.partner_id.id,
                'comment': order.note or '',
                'currency_id': order.pricelist_id.currency_id.id, # considering partner's sale pricelist's currency
            }
            inv.update(inv_ref.onchange_partner_id(cr, uid, [], 'out_invoice', order.partner_id.id)['value'])
            if not inv.get('account_id', None):
                inv['account_id'] = acc
            inv_id = inv_ref.create(cr, uid, inv, context=context)

            self.write(cr, uid, [order.id], {'invoice_id': inv_id, 'state': 'invoiced'}, context=context)
            inv_ids.append(inv_id)
            for line in order.lines:
                inv_line = {
                    'invoice_id': inv_id,
                    'product_id': line.product_id.id,
                    'quantity': line.qty,
                }
                inv_name = product_obj.name_get(cr, uid, [line.product_id.id], context=context)[0][1]
                inv_line.update(inv_line_ref.product_id_change(cr, uid, [],
                                                               line.product_id.id,
                                                               line.product_id.uom_id.id,
                                                               line.qty, partner_id = order.partner_id.id,
                                                               fposition_id=order.partner_id.property_account_position.id)['value'])
                if line.product_id.description_sale:
                    inv_line['note'] = line.product_id.description_sale
                inv_line['price_unit'] = line.price_unit
                inv_line['discount'] = line.discount
                inv_line['name'] = inv_name
                inv_line['invoice_line_tax_id'] = ('invoice_line_tax_id' in inv_line)\
                    and [(6, 0, inv_line['invoice_line_tax_id'])] or []
                inv_line_ref.create(cr, uid, inv_line, context=context)
            inv_ref.button_reset_taxes(cr, uid, [inv_id], context=context)
            wf_service.trg_validate(uid, 'pos.order', order.id, 'invoice', cr)

        if not inv_ids:
            return {}
        
        mod_obj = self.pool['ir.model.data']
        res = mod_obj.get_object_reference(cr, uid, 'account', 'invoice_form')
        res_id = res and res[1] or False
        return {
            'name': _('Customer Invoice'),
            'view_type': 'form',
            'view_mode': 'form',
            'view_id': [res_id],
            'res_model': 'account.invoice',
            'context': "{'type':'out_invoice'}",
            'type': 'ir.actions.act_window',
            'nodestroy': True,
            'target': 'current',
            'res_id': inv_ids and inv_ids[0] or False,
        }

    def create_account_move(self, cr, uid, ids, context=None):
        return self._create_account_move_line(cr, uid, ids, None, None, context=context)

    def _create_account_move_line(self, cr, uid, ids, session=None, move_id=None, context=None):
        # Tricky, via the workflow, we only have one id in the ids variable
        """Create a account move line of order grouped by products or not."""
        context = context or self.pool['res.users'].context_get(cr, uid)

        account_move_obj = self.pool['account.move']
        account_period_obj = self.pool['account.period']
        account_tax_obj = self.pool['account.tax']
        property_obj = self.pool['ir.property']
        cur_obj = self.pool['res.currency']

        fiscal_pos_obj = self.pool['account.fiscal.position']

        # session_ids = set(order.session_id for order in self.browse(cr, uid, ids, context=context))

        if session and not all(session.id == order.session_id.id for order in self.browse(cr, uid, ids, context=context)):
            raise osv.except_osv(_('Error!'), _('Selected orders do not have the same session!'))

        grouped_data = {}

        def compute_tax(amount, tax, line):
            if amount > 0:
                tax_code_id = tax['base_code_id']
                tax_amount = line.price_subtotal * tax['base_sign']
            else:
                tax_code_id = tax['ref_base_code_id']
                tax_amount = line.price_subtotal * tax['ref_base_sign']

            return (tax_code_id, tax_amount,)

        for order in self.browse(cr, uid, ids, context=context):
            if order.account_move:
                continue
            if order.state != 'paid':
                continue
            if order.sale_journal.group_invoice_lines:
                have_to_group_by = True
            else:
                have_to_group_by = False

            current_company = order.sale_journal.company_id

            group_tax = {}
            account_def = order.shop_id.property_account_receivable or property_obj.get(cr, uid, 'property_account_receivable', 'res.partner', context=context)

            order_account = order.partner_id and \
                            order.partner_id.property_account_receivable and \
                            order.partner_id.property_account_receivable.id or \
                            account_def and account_def.id or current_company.account_receivable.id

            # order_account = fiscal_pos_obj.map_account(cr, uid, order.shop_id.fiscal_position_id, order_account)
            if move_id is None:
                # Create an entry for the sale
                date = order.date_order[:10]
                period = account_period_obj.find(cr, uid, dt=date, context=dict(context or {}, company_id=current_company.id, account_period_prefer_normal=True))[0]
                move_id = account_move_obj.create(cr, uid, {
                    'ref': order.name,
                    'journal_id': order.sale_journal.id,
                    'date': date,
                    'period_id': period,
                }, context=context)

            def insert_data(data_type, values):
                # if have_to_group_by:

                sale_journal_id = order.sale_journal.id
                period = account_period_obj.find(cr, uid, dt=order.date_order[:10], context=dict(context or {}, company_id=current_company.id, account_period_prefer_normal=True))[0]

                # 'quantity': line.qty,
                # 'product_id': line.product_id.id,
                values.update({
                    'date': order.date_order[:10],
                    'ref': order.name,
                    'journal_id': sale_journal_id,
                    'period_id': period,
                    'move_id': move_id,
                    'company_id': current_company.id,
                })

                if data_type == 'product':
                    key = ('product', values['partner_id'], values['account_id'], values['debit'] > 0)
                elif data_type == 'tax':
                    key = ('tax', values['partner_id'], values['tax_code_id'], values['debit'] > 0)
                elif data_type == 'counter_part':
                    key = ('counter_part', values['partner_id'], values['account_id'], values['debit'] > 0)
                else:
                    return

                grouped_data.setdefault(key, [])

                # if not have_to_group_by or (not grouped_data[key]):
                #     grouped_data[key].append(values)
                # else:
                #     pass

                if have_to_group_by:
                    if not grouped_data[key]:
                        grouped_data[key].append(values)
                    else:
                        current_value = grouped_data[key][0]
                        current_value.update({
                            'name': data_type,
                            'quantity': current_value.get('quantity', 0.0) + values.get('quantity', 0.0),
                            'credit': current_value.get('credit', 0.0) + values.get('credit', 0.0),
                            'debit': current_value.get('debit', 0.0) + values.get('debit', 0.0),
                            'tax_amount': current_value.get('tax_amount', 0.0) + values.get('tax_amount', 0.0),
                        })
                else:
                    grouped_data[key].append(values)

            # because of the weird way the pos order is written, we need to make sure there is at least one line,
            # because just after the 'for' loop there are references to 'line' and 'income_account' variables (that
            # are set inside the for loop)
            # TOFIX: a deep refactoring of this method (and class!) is needed in order to get rid of this stupid hack
            assert order.lines, _('The POS order must have lines when calling this method')
            # Create an move for each order line

            cur = order.pricelist_id.currency_id
            counter_part = 0
            for line in order.lines:
                tax_amount = 0
                taxes = [tax for tax in line.product_id.taxes_id if tax.company_id.id == line.order_id.company_id.id]

                if line.shop_id and line.shop_id.fiscal_position_id:
                    taxes = fiscal_pos_obj.map_tax(cr, uid, line.shop_id.fiscal_position_id, taxes, context=context)
                    taxes = self.pool['account.tax'].browse(cr, uid, taxes, context)

                context.update({'tax_calculation_rounding_method': 'round_globally'})
                # computed_taxes = account_tax_obj.compute_all(cr, uid, taxes, (line.price_unit * (100.0-line.discount)) / 100.0, line.qty, context=context)['taxes']
                computed_taxes = account_tax_obj.compute_all(cr, uid, taxes, line.price_subtotal_incl / line.qty, line.qty, context=context)['taxes']
                for tax in computed_taxes:
                    # tax_amount += cur_obj.round(cr, uid, cur, tax['amount'])
                    group_key = (tax['tax_code_id'], tax['base_code_id'], tax['account_collected_id'], tax['id'])
                    group_tax.setdefault(group_key, 0)
                    group_tax[group_key] += cur_obj.round(cr, uid, cur, tax['amount'])

                amount = line.price_subtotal

                # Search for the income account
                if line.product_id.property_account_income.id:
                    income_account = line.product_id.property_account_income.id
                elif line.product_id.categ_id.property_account_income_categ.id:
                    income_account = line.product_id.categ_id.property_account_income_categ.id
                else:
                    raise osv.except_osv(_('Error!'), _('Please define income '\
                        'account for this product: "%s" (id:%d).') \
                        % (line.product_id.name, line.product_id.id, ))

                income_account = fiscal_pos_obj.map_account(cr, uid, line.shop_id.fiscal_position_id, income_account, context=context)

                # Empty the tax list as long as there is no tax code:
                tax_code_id = False
                tax_amount = 0
                while computed_taxes:
                    tax = computed_taxes.pop(0)
                    tax_code_id, tax_amount = compute_tax(amount, tax, line)

                    # If there is one we stop
                    if tax_code_id:
                        break

                # Create a move for the line
                insert_data('product', {
                    'name': line.product_id.name,
                    'quantity': line.qty,
                    'product_id': line.product_id.id,
                    'account_id': income_account,
                    'credit': ((amount > 0) and amount) or 0.0,
                    'debit': ((amount < 0) and -amount) or 0.0,
                    'tax_code_id': tax_code_id,
                    'tax_amount': tax_amount,
                    'partner_id': order.partner_id and self.pool["res.partner"]._find_accounting_partner(order.partner_id).id or False
                })
                counter_part += amount

                # For each remaining tax with a code, whe create a move line
                for tax in computed_taxes:
                    tax_code_id, tax_amount = compute_tax(amount, tax, line)
                    if not tax_code_id:
                        continue

                    insert_data('tax', {
                        'name': _('Tax'),
                        'product_id': line.product_id.id,
                        'quantity': line.qty,
                        'account_id': income_account,
                        'credit': 0.0,
                        'debit': 0.0,
                        'tax_code_id': tax_code_id,
                        'tax_amount': tax_amount,
                        'partner_id': order.partner_id and self.pool["res.partner"]._find_accounting_partner(order.partner_id).id or False
                    })

            # Create a move for each tax group
            (tax_code_pos, base_code_pos, account_pos, tax_id) = (0, 1, 2, 3)

            for key, tax_amount in group_tax.items():
                tax = self.pool['account.tax'].browse(cr, uid, key[tax_id], context=context)
                insert_data('tax', {
                    'name': _('Tax') + ' ' + tax.name,
                    'quantity': line.qty,
                    'product_id': line.product_id.id,
                    'account_id': key[account_pos] or income_account,
                    'credit': ((tax_amount > 0) and tax_amount) or 0.0,
                    'debit': ((tax_amount < 0) and -tax_amount) or 0.0,
                    'tax_code_id': key[tax_code_pos],
                    'tax_amount': tax_amount,
                    'partner_id': order.partner_id and self.pool['res.partner']._find_accounting_partner(
                        order.partner_id).id or False
                })
                counter_part += tax_amount

            # counterpart
            insert_data('counter_part', {
                'name': _("Trade Receivables"),  # order.name,
                'account_id': order_account,
                # 'credit': ((order.amount_total < 0) and -order.amount_total) or 0.0,
                # 'debit': ((order.amount_total > 0) and order.amount_total) or 0.0,
                'credit': ((counter_part < 0) and -counter_part) or 0.0,
                'debit': ((counter_part > 0) and counter_part) or 0.0,
                'partner_id': order.partner_id and self.pool["res.partner"]._find_accounting_partner(order.partner_id).id or False
            })

            order.write({'state': 'done', 'account_move': move_id})

        all_lines = []
        for group_key, group_data in grouped_data.iteritems():
            for value in group_data:
                all_lines.append((0, 0, value),)
        if move_id:  # In case no order was changed
            account_move_obj.write(cr, uid, [move_id], {'line_id': all_lines}, context=context)
            if order.sale_journal.entry_posted:
                account_move_obj.button_validate(cr, uid, [move_id], context)

        return True

    def action_payment(self, cr, uid, ids, context=None):
        return self.write(cr, uid, ids, {'state': 'payment'}, context=context)

    def action_paid(self, cr, uid, ids, context=None):
        self.write(cr, uid, ids, {'state': 'paid'}, context=context)
        self.create_picking(cr, uid, ids, context=context)
        return True

    def action_cancel(self, cr, uid, ids, context=None):
        self.write(cr, uid, ids, {'state': 'cancel'}, context=context)
        return True

    def action_done(self, cr, uid, ids, context=None):
        self.create_account_move(cr, uid, ids, context=context)
        return True


class account_bank_statement(osv.osv):
    _inherit = 'account.bank.statement'
    _columns = {
        'user_id': fields.many2one('res.users', 'User', readonly=True),
        'pos_close': fields.boolean('Close by POS'),
    }

    _defaults = {
        'user_id': lambda self, cr, uid, c={}: uid
    }


class account_bank_statement_line(osv.osv):
    _inherit = 'account.bank.statement.line'
    _columns = {
        'journal_id': fields.related('statement_id','journal_id','name', store=True, string='Journal', type='char', size=64),
        'pos_statement_id': fields.many2one('pos.order', ondelete='cascade'),
    }


class pos_order_line(osv.osv):
    _name = "pos.order.line"
    _description = "Lines of Point of Sale"
    _rec_name = "product_id"

    def _amount_line_all(self, cr, uid, ids, field_names, arg, context=None):
        res = dict([(i, {}) for i in ids])
        account_tax_obj = self.pool['account.tax']
        cur_obj = self.pool['res.currency']
        fiscal_pos_obj = self.pool['account.fiscal.position']
        for line in self.browse(cr, uid, ids, context=context):
            taxes_ids = [tax for tax in line.product_id.taxes_id if tax.company_id.id == line.order_id.company_id.id ]
            if line.shop_id and line.shop_id.fiscal_position_id:
                taxes_ids = fiscal_pos_obj.map_tax(cr, uid, line.shop_id.fiscal_position_id, taxes_ids, context=context)
                taxes_ids = self.pool['account.tax'].browse(cr, uid, taxes_ids, context)
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = account_tax_obj.compute_all(cr, uid, taxes_ids, price, line.qty, product=line.product_id, partner=line.order_id.partner_id or False)

            cur = line.order_id.pricelist_id.currency_id
            res[line.id]['price_subtotal'] = cur_obj.round(cr, uid, cur, taxes['total'])
            res[line.id]['price_subtotal_incl'] = cur_obj.round(cr, uid, cur, taxes['total_included'])
        return res

    def onchange_product_id(self, cr, uid, ids, pricelist, product_id, qty=0, partner_id=False, context=None):
        context = context or {}
        if not product_id:
            return {}
        if not pricelist:
           raise osv.except_osv(_('No Pricelist !'),
               _('You have to select a pricelist in the sale form !\n' \
               'Please set one before choosing a product.'))

        price = self.pool.get('product.pricelist').price_get(cr, uid, [pricelist],
               product_id, qty or 1.0, partner_id)[pricelist]

        result = self.onchange_qty(cr, uid, ids, product_id, 0.0, qty, price, context=context)
        result['value']['price_unit'] = price
        return result

    def onchange_qty(self, cr, uid, ids, product, discount, qty, price_unit, context=None):
        result = {}
        if not product:
            return result
        account_tax_obj = self.pool.get('account.tax')
        #cur_obj = self.pool.get('res.currency')
        prod = self.pool.get('product.product').browse(cr, uid, product, context=context)

        taxes = prod.taxes_id
        price = price_unit * (1 - (discount or 0.0) / 100.0)
        taxes = account_tax_obj.compute_all(cr, uid, prod.taxes_id, price, qty, product=prod, partner=False)

        result['price_subtotal'] = taxes['total']
        result['price_subtotal_incl'] = taxes['total_included']
        return {'value': result}

    _columns = {
        'date_from': fields.function(lambda *a, **k: {}, method=True, type='date', string="Date from"),
        'date_to': fields.function(lambda *a, **k: {}, method=True, type='date', string="Date to"),
        'date_order': fields.related('order_id', 'date_order', store=True, string='Date Order', type='date', size=64),
        'active': fields.boolean('Active'),
        'company_id': fields.many2one('res.company', 'Company', required=True),
        'name': fields.char('Line No', size=32, required=True),
        'notice': fields.char('Discount Notice', size=128),
        'product_id': fields.many2one('product.product', 'Product', domain=[('sale_ok', '=', True)], required=True, change_default=True),
        'price_unit': fields.float(string='Unit Price', digits=(16, 2)),
        'qty': fields.float('Quantity', digits=(16, 2)),
        'price_subtotal': fields.function(_amount_line_all, multi='pos_order_line_amount', string='Subtotal w/o Tax', store={'pos.order.line': (lambda self, cr, uid, ids, c={}: ids, ['price_unit', 'qty', 'discount'], 50)}),
        'price_subtotal_incl': fields.function(_amount_line_all, multi='pos_order_line_amount', string='Subtotal', store={'pos.order.line': (lambda self, cr, uid, ids, c={}: ids, ['price_unit', 'qty', 'discount'], 50)}),
        'discount': fields.float('Discount (%)', digits=(16, 2)),
        'order_id': fields.many2one('pos.order', 'Order Ref', ondelete='cascade'),
        'create_date': fields.datetime('Creation Date', readonly=True),
        'pos_discount': fields.boolean('Discount?'),
        'shop_id': fields.related('order_id', 'shop_id', type='many2one', relation='sale.shop', string='Shop', store=True),
        'user_id': fields.related('order_id', 'user_id', type='many2one', relation='res.users', string='Connected Salesman', store=True),
        'default_code': fields.related('product_id', 'default_code', type='char', relation='product.product', string='Reference', store=True),
        'employee_id': fields.integer('ID employee'),
        'categ_id': fields.related('product_id', 'product_tmpl_id', 'categ_id', type='many2one', relation='product.category', string='Category', store=True),
    }

    _defaults = {
        'active': 1,
        'name': lambda obj, cr, uid, context: obj.pool.get('ir.sequence').get(cr, uid, 'pos.order.line'),
        'qty': lambda *a: 1,
        'discount': lambda *a: 0.0,
        'company_id': lambda self,cr,uid,c: self.pool.get('res.users').browse(cr, uid, uid, c).company_id.id,
    }

    def copy_data(self, cr, uid, id, default=None, context=None):
        if not default:
            default = {}
        default.update({
            'name': self.pool.get('ir.sequence').get(cr, uid, 'pos.order.line')
        })
        return super(pos_order_line, self).copy_data(cr, uid, id, default, context=context)

    def search(self, cr, uid, args, offset=0, limit=0, order=None, context=None, count=False):
        new_args = []
        for arg in args:
            if arg[0] == 'shop_id':
                new_args.append(('order_id.shop_id', '=', arg[2] ))
            elif arg[0] == 'user_id':
                new_args.append(('order_id.user_id', '=', arg[2] ))
            else:
                new_args.append(arg)

        return super(pos_order_line, self).search(cr, uid, new_args, offset=offset, limit=limit, order=order, context=context, count=count)


class pos_category(osv.osv):
    _name = 'pos.category'
    _description = "PoS Category"
    _order = "sequence, name"

    def _check_recursion(self, cr, uid, ids, context=None):
        level = 100
        while len(ids):
            cr.execute('select distinct parent_id from pos_category where id IN %s',(tuple(ids),))
            ids = filter(None, map(lambda x:x[0], cr.fetchall()))
            if not level:
                return False
            level -= 1
        return True

    _constraints = [
        (_check_recursion, 'Error ! You cannot create recursive categories.', ['parent_id'])
    ]

    def name_get(self, cr, uid, ids, context=None):
        if not len(ids):
            return []
        res = []
        for record in self.browse(cr, uid, ids, context=context):
            name = record.name
            if record.parent_id:
                name = record.parent_id.name +' / ' + name
            res.append((record.id, name))
        return res

    def _name_get_fnc(self, cr, uid, ids, prop, unknow_none, context=None):
        res = self.name_get(cr, uid, ids, context=context)
        return dict(res)

    _columns = {
        'name': fields.char('Name', size=64, required=True, translate=True),
        'complete_name': fields.function(_name_get_fnc, type="char", string='Name'),
        'parent_id': fields.many2one('pos.category','Parent Category', select=True),
        'child_id': fields.one2many('pos.category', 'parent_id', string='Children Categories'),
        'sequence': fields.integer('Sequence', help="Gives the sequence order when displaying a list of product categories."),
    }
pos_category()


class product_product(osv.osv):
    _inherit = 'product.product'

    def _get_small_image(self, cr, uid, ids, prop, unknow_none, context=None):
        result = {}
        for obj in self.browse(cr, uid, ids, context=context):
            if not obj.product_image:
                result[obj.id] = False
                continue

            image_stream = io.BytesIO(obj.product_image.decode('base64'))
            img = Image.open(image_stream).convert('RGB')
            img.thumbnail((120, 100), Image.ANTIALIAS)
            img_stream = StringIO.StringIO()
            img.save(img_stream, "JPEG")
            result[obj.id] = img_stream.getvalue().encode('base64')
        return result

    def _get_medium_image(self, cr, uid, ids, prop, unknow_none, context=None):
        result = {}
        for obj in self.browse(cr, uid, ids, context=context):
            if not obj.product_image:
                result[obj.id] = False
                continue
            image_stream = io.BytesIO(obj.product_image.decode('base64'))
            img = Image.open(image_stream).convert('RGB')
            img.thumbnail((150, 150), Image.ANTIALIAS)
            img_stream = StringIO.StringIO()
            img.save(img_stream, "JPEG")
            result[obj.id] = img_stream.getvalue().encode('base64')
        return result

    def _get_tax_rate(self, cr, uid, ids, prop, unknow_none, context=None):
        context = context or self.pool['res.users'].context_get(cr, uid)
        result = {}
        fiscal_pos_obj = self.pool['account.fiscal.position']

        fiscal_position_id = False
        send_tax_code = False
        if context.get('warehouse', False) and context.get('pricelist', False):
            send_tax_code = True
            sale_shop_obj = self.pool['sale.shop']
            sale_shop = sale_shop_obj.search(cr, uid, [('pos_user_id', '=', uid), ('warehouse_id', '=', context.get('warehouse')), ('pricelist_id', '=', context.get('pricelist'))], context=context)
            if sale_shop:
                fiscal_position_id = sale_shop_obj.browse(cr, uid, sale_shop, context)[0].fiscal_position_id

        for product in self.browse(cr, uid, ids, context=context):
            tax_rate = 0.0
            if send_tax_code:
                taxes_ids = [tax for tax in product.taxes_id]
                if fiscal_position_id:
                    taxes_ids = fiscal_pos_obj.map_tax(cr, uid, fiscal_position_id, taxes_ids, context=context)
                    taxes_ids = self.pool['account.tax'].browse(cr, uid, taxes_ids, context)
                for tax in taxes_ids:
                    tax_rate =+ tax.amount
            result[product.id] = (tax_rate / (1 + tax_rate)) * 100
        return result

    _columns = {
        'taxrate': fields.function(_get_tax_rate, string="Get Tax Rate", type="float", store=False),
        'income_pdt': fields.boolean('PoS Cash Input', help="This is a product you can use to put cash into a statement for the point of sale backend."),
        'expense_pdt': fields.boolean('PoS Cash Output', help="This is a product you can use to take cash from a statement for the point of sale backend, exemple: money lost, transfer to bank, etc."),
        'pos_categ_id': fields.many2one('pos.category','PoS Category',
            help="If you want to sell this product through the point of sale, select the category it belongs to."),
        'product_image_small': fields.function(_get_small_image, string='Small Image', type="binary",
            store = {
                'product.product': (lambda self, cr, uid, ids, c={}: ids, ['product_image'], 10),
            }),
        'product_image_medium': fields.function(_get_medium_image, string='Medium Image', type="binary",
            store = {
                'product.product': (lambda self, cr, uid, ids, c={}: ids, ['product_image'], 10),
            }),
        'available_in_pos': fields.boolean('Available in the Point of Sale', help='Check if you want this product to appear in the Point of Sale'),
    }
    _defaults = {
        'available_in_pos': True,
    }


class res_partner(osv.osv):
    _inherit = 'res.partner'
    _columns = {
        'property_customer_ref': fields.char('Customer Ref.', size=16, help="The reference attributed by the partner to the current company as a customer of theirs."),
    }


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
