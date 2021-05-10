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
import datetime

import netsvc
from openerp import SUPERUSER_ID
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from openerp.tools.float_utils import float_round
from osv import osv, fields
from tools.translate import _

import pos_box_entries


class pos_make_payment(osv.osv_memory):
    _name = 'pos.make.payment'
    _description = 'Point of Sale Payment'

    def check(self, cr, uid, ids, context=None):
        """Check the order:
        if the order is not paid: continue payment,
        if the order is paid print ticket.
        """
        context = context or {}
        order_obj = self.pool['pos.order']
        order_line_obj = self.pool['pos.order.line']
        hr_employee_obj = self.pool.get('hr.employee') and self.pool['hr.employee'] or False

        active_id = context and context.get('active_id', False)
        
        order = order_obj.browse(cr, uid, active_id, context=context)
        context['pricelist'] = order.pricelist_id and order.pricelist_id.id or 1
        discount = False
        hr_employee = []
        vals = {}
        line_deactive = []
        sign_action = []
        product_customer_id = order.shop_id.product_customer_id and order.shop_id.product_customer_id.id or False

        for line in order.lines:
            if hr_employee_obj:
                hr_employee_ids = hr_employee_obj.search(cr, SUPERUSER_ID, [('product_id', '=', line.product_id.id)], context=context)
                if hr_employee_ids:
                    line_data_deactivate = {
                        'employee_id': hr_employee_ids[0],
                        'active': False
                    }
                    hr_employee.append(hr_employee_obj.browse(cr, SUPERUSER_ID, hr_employee_ids[0], context=context))
                    if line.qty > 0:
                        sign_action.append({'type': 'sign_in'})
                    else:
                        sign_action.append({'type': 'sign_out'})

                    line_deactive.append(line.id)
                    order_line_obj.write(cr, uid, line.id, line_data_deactivate, context=context)
                    continue

            list_price = self.pool['product.product']._product_price(cr, uid, [line.product_id.id], False, False, context=context)[line.product_id.id]
            pos_price = line.price_unit

            if line.product_id.id == product_customer_id:
                if line.notice:
                    partner_id = self.pool['res.partner'].search(cr, uid, [('property_customer_ref', '=', line.notice[len(order.shop_id.product_customer_id.default_code):-1])])
                    if partner_id:
                        order.write({'partner_id': partner_id[0]})
                line.write({'active': False})
            if not context.get('skip_force_discount', False):
                if float_round(list_price, precision_digits=2) != 0.0 and float_round(list_price, precision_digits=2) != float_round(pos_price, precision_digits=2):
                    discount = (list_price - pos_price) / list_price * 100
                    line_data = {
                        'price_unit': list_price,
                        'discount': discount,
                        'pos_discount': True,
                    }
                    order_line_obj.write(cr, uid, line.id, line_data, context=context)

        if hr_employee and len(order.lines) == len(line_deactive):  # i have set only one employee
            result = []
            while hr_employee:
                employee = hr_employee.pop()
                try:
                    ctx = context.copy()
                    ctx['shop_id'] = order.shop_id.id
                    attendance_action = sign_action.pop()['type']
                    employee_state = employee.state
                    force_attendance_action = False
                    if attendance_action == 'sign_in' and employee_state == 'present':
                        force_attendance_action = 'sign_out'
                    elif attendance_action == 'sign_out' and employee_state == 'absent':
                        force_attendance_action = 'sign_in'
                    if force_attendance_action:
                        result.append("Force {} at {}".format(force_attendance_action, order.date_order))

                        dt = datetime.datetime.strptime(order.date_order, DEFAULT_SERVER_DATETIME_FORMAT)
                        dt_10 = dt - datetime.timedelta(seconds=10)
                        hr_employee_obj.attendance_action_change(cr, employee.user_id.id, [employee.id],
                                                                 type=force_attendance_action, context=ctx,
                                                                 dt=dt_10.strftime(DEFAULT_SERVER_DATETIME_FORMAT))

                    res = hr_employee_obj.attendance_action_change(cr, employee.user_id.id, [employee.id], type=attendance_action, context=ctx, dt=order.date_order)
                except Exception as e:
                    res = str(e)
                result.append(res)

            order_obj.write(cr, uid, active_id, {'active': False, 'note': result}, context=context)
            pos_order_line_ids = self.pool['pos.order.line'].search(cr, uid, [('order_id', '=', active_id)], context=context)
            if pos_order_line_ids:
                self.pool['pos.order.line'].write(cr, uid, pos_order_line_ids, {'active': False}, context=context)
            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate(uid, 'pos.order', active_id, 'cancel', cr)
            return {'type': 'ir.actions.act_window_close'}

        if discount:
            vals['pos_discount'] = True
        if hr_employee and len(order.lines) >= 1:
            vals['user_id'] = hr_employee[0].user_id and hr_employee[0].user_id.id or order.user_id.id
        if vals:
            order_obj.write(cr, uid, active_id, vals, context=context)

        # amount = float_round(order.amount_total, precision_digits=2) - float_round(order.amount_paid, precision_digits=2)
        amount2 = abs(int(order.amount_total * 100) - int(order.amount_paid * 100))
        # this is probably a problem of osv_memory as it's not compatible with normal OSV's
        # data['journal'] = data['journal'][0]
#        if amount != 0.0:
        if amount2 > 0.1:
            data = self.read(cr, uid, ids, context=context)[0]
            order_obj.add_payment(cr, uid, active_id, data, context=context)
        if order_obj.test_paid(cr, uid, [active_id], context):
            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate(uid, 'pos.order', active_id, 'paid', cr)
            return True #self.print_report(cr, uid, ids, context=context)

        return self.launch_payment(cr, uid, ids, context=context)

    def launch_payment(self, cr, uid, ids, context=None):
        return {
            'name': _('Paiement'),
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'pos.make.payment',
            'view_id': False,
            'target': 'new',
            'views': False,
            'type': 'ir.actions.act_window',
        }

    def print_report(self, cr, uid, ids, context=None):
        active_id = context.get('active_id', [])
        datas = {'ids': [active_id]}
        ## TODO  based on POS config return {'type': 'ir.actions.act_window_close'}
        return {
            'type': 'ir.actions.report.xml',
            'report_name': 'pos.receipt',
            'datas': datas,
        }

    def _default_journal(self, cr, uid, context=None):
        res = pos_box_entries.get_journal(self, cr, uid, context=context)
        return len(res)>1 and res[1][0] or False

    def _default_amount(self, cr, uid, context=None):
        order_obj = self.pool.get('pos.order')
        active_id = context and context.get('active_id', False)
        if active_id:
            order = order_obj.browse(cr, uid, active_id, context=context)
            return order.amount_total - order.amount_paid
        return False

    def _get_journal(self, cr, uid, context=None):
        context = context or self.pool['res.users'].context_get(cr, uid)
        if context.get('user_id'):
            uid = context['user_id']
        return self.pool['pos.box.entries']._get_journal(cr, uid, context)

    _columns = {
        'journal': fields.selection(_get_journal, "Payment Mode", required=True),
        'amount': fields.float('Amount', digits=(16, 2), required= True),
        'payment_name': fields.char('Payment Reference', size=32),
        'payment_date': fields.date('Payment Date', required=True),
    }

    _defaults = {
        'payment_date': lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'),
        'amount': _default_amount,
        'journal': _default_journal
    }



# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
