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
import datetime
from operator import add
from dateutil.relativedelta import relativedelta
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT
import decimal_precision as dp
from openerp.osv import fields, osv
from openerp.tools.translate import _


def strToDate(dt):
        dt_date=datetime.date(int(dt[0:4]),int(dt[5:7]),int(dt[8:10]))
        return dt_date

# ---------------------------------------------------------
# Budgets
# ---------------------------------------------------------
class account_budget_post(osv.osv):
    _name = "account.budget.post"
    _description = "Budgetary Position"
    _columns = {
        'code': fields.char('Code', size=64, required=True),
        'name': fields.char('Name', size=256, required=True),
        'account_ids': fields.many2many('account.account', 'account_budget_rel', 'budget_id', 'account_id', 'Accounts'),
        'crossovered_budget_line': fields.one2many('crossovered.budget.lines', 'general_budget_id', 'Budget Lines'),
        'company_id': fields.many2one('res.company', 'Company', required=True),
    }
    _defaults = {
        'company_id': lambda self, cr, uid, c: self.pool.get('res.company')._company_default_get(cr, uid, 'account.budget.post', context=c)
    }
    _order = "name"

account_budget_post()


class crossovered_budget(osv.osv):
    _name = "crossovered.budget"
    _description = "Budget"

    _columns = {
        'name': fields.char('Name', size=64, required=True, states={'done':[('readonly',True)]}),
        'code': fields.char('Code', size=16, required=True, states={'done':[('readonly',True)]}),
        'creating_user_id': fields.many2one('res.users', 'Responsible User'),
        'validating_user_id': fields.many2one('res.users', 'Validate User', readonly=True),
        'date_from': fields.date('Start Date', required=True, states={'done':[('readonly',True)]}),
        'date_to': fields.date('End Date', required=True, states={'done':[('readonly',True)]}),
        'state' : fields.selection([('draft','Draft'),('cancel', 'Cancelled'),('confirm','Confirmed'),('validate','Validated'),('done','Done')], 'Status', select=True, required=True, readonly=True),
        'crossovered_budget_line': fields.one2many('crossovered.budget.lines', 'crossovered_budget_id', 'Budget Lines', states={'done':[('readonly',True)]}),
        'company_id': fields.many2one('res.company', 'Company', required=True),
    }

    _defaults = {
        'state': 'draft',
        'creating_user_id': lambda self, cr, uid, context: uid,
        'company_id': lambda self, cr, uid, c: self.pool.get('res.company')._company_default_get(cr, uid, 'account.budget.post', context=c)
    }

    def budget_confirm(self, cr, uid, ids, *args):
        self.write(cr, uid, ids, {
            'state': 'confirm'
        })
        return True

    def budget_draft(self, cr, uid, ids, *args):
        self.write(cr, uid, ids, {
            'state': 'draft'
        })
        return True

    def budget_validate(self, cr, uid, ids, *args):
        self.write(cr, uid, ids, {
            'state': 'validate',
            'validating_user_id': uid,
        })
        return True

    def budget_cancel(self, cr, uid, ids, *args):
        self.write(cr, uid, ids, {
            'state': 'cancel'
        })
        return True

    def budget_done(self, cr, uid, ids, *args):
        self.write(cr, uid, ids, {
            'state': 'done'
        })
        return True


class crossovered_budget_lines(osv.osv):

    def _get_period_range_from_start_period(self, cr, uid, start_period, include_opening=False,
                                            fiscalyear=False, stop_at_previous_opening=False):
        """We retrieve all periods before start period"""
        opening_period_id = False
        past_limit = []
        period_obj = self.pool.get('account.period')
        mv_line_obj = self.pool.get('account.move.line')
        # We look for previous opening period
        date_start = date_stop = start_period

        if stop_at_previous_opening:
            opening_search = [('special', '=', True),
                              ('date_stop', '<', date_start)]
            if fiscalyear:
                opening_search.append(('fiscalyear_id', '=', fiscalyear.id))

            opening_periods = period_obj.search(cr, uid, opening_search,
                                                order='date_stop desc')
            for opening_period in opening_periods:
                validation_res = mv_line_obj.search(cr, uid,
                                                    [('period_id', '=', opening_period)],
                                                    limit=1)
                if validation_res:
                    opening_period_id = opening_period
                    break
            if opening_period_id:
                # we also look for overlapping periods
                opening_period_br = period_obj.browse(cr, uid, opening_period_id)
                past_limit = [('date_start', '>=', opening_period_br.date_stop)]

        periods_search = [('date_stop', '<=', date_stop)]
        periods_search += past_limit

        if not include_opening:
            periods_search += [('special', '=', False)]

        if fiscalyear:
            periods_search.append(('fiscalyear_id', '=', fiscalyear.id))
        periods = period_obj.search(cr, uid, periods_search)
        if include_opening and opening_period_id:
            periods.append(opening_period_id)
        periods = list(set(periods))

        return periods

    def _compute_init_balance(self, cr, uid, account_id=None, period_ids=None, mode='computed', default_values=False):
        if not isinstance(period_ids, list):
            period_ids = [period_ids]
        res = {}

        if not default_values:
            if not account_id or not period_ids:
                raise Exception('Missing account or period_ids')
            try:
                cr.execute("SELECT sum(debit) AS debit, "
                                    " sum(credit) AS credit, "
                                    " sum(debit)-sum(credit) AS balance, "
                                    " sum(amount_currency) AS curr_balance"
                                    " FROM account_move_line"
                                    " WHERE period_id in %s"
                                    " AND account_id = %s", (tuple(period_ids), account_id))
                res = cr.dictfetchone()

            except Exception, exc:
                cr.rollback()
                raise

        return {'debit': res.get('debit') or 0.0,
                'credit': res.get('credit') or 0.0,
                'init_balance': res.get('balance') or 0.0,
                'init_balance_currency': res.get('curr_balance') or 0.0,
                'state': mode}

    def get_included_opening_period(self, cr, uid, period):
        """Return the opening included in normal period we use the assumption
        that there is only one opening period per fiscal year"""
        period_obj = self.pool['account.period']
        date_start = date_stop = period
        return period_obj.search(cr, uid,
                                 [('special', '=', True),
                                  ('date_start', '>=', date_start),
                                  ('date_stop', '<=', date_stop)],
                                  limit=1)

    def _compute_initial_balances(self, cr, uid, account_ids, start_period, fiscalyear):
        """We compute initial balance.
        If form is filtered by date all initial balance are equal to 0
        This function will sum pear and apple in currency amount if account as no secondary currency"""
        # if opening period is included in start period we do not need to compute init balance
        # we just read it from opening entries
        res = {}
        # PNL and Balance accounts are not computed the same way look for attached doc
        # We include opening period in pnl account in order to see if opening entries
        # were created by error on this account
        pnl_periods_ids = self._get_period_range_from_start_period(cr, uid, start_period, fiscalyear=fiscalyear,
                                                                   include_opening=True)
        bs_period_ids = self._get_period_range_from_start_period(cr, uid, start_period, include_opening=True,
                                                                 stop_at_previous_opening=True)
        opening_period_selected = self.get_included_opening_period(cr, uid, start_period)

        for acc in self.pool.get('account.account').browse(cr, uid, account_ids):
            res[acc.id] = self._compute_init_balance(cr, uid, default_values=True)
            if acc.user_type.close_method == 'none':
                # we compute the initial balance for close_method == none only when we print a GL
                # during the year, when the opening period is not included in the period selection!
                if pnl_periods_ids and not opening_period_selected:
                    res[acc.id] = self._compute_init_balance(cr, uid, acc.id, pnl_periods_ids)
            else:
                res[acc.id] = self._compute_init_balance(cr, uid, acc.id, bs_period_ids)
        return res

    def _get_account_details(self, cr, uid, account_ids, target_move, fiscalyear, main_filter, start, stop, initial_balance_mode, context=None):
        """
        Get details of accounts to display on the report
        @param account_ids: ids of accounts to get details
        @param target_move: selection filter for moves (all or posted)
        @param fiscalyear: browse of the fiscalyear
        @param main_filter: selection filter period / date or none
        @param start: start date or start period browse instance
        @param stop: stop date or stop period browse instance
        @param initial_balance_mode: False: no calculation, 'opening_balance': from the opening period, 'initial_balance': computed from previous year / periods
        @return: dict of list containing accounts details, keys are the account ids
        """
        if context is None:
            context = {}

        account_obj = self.pool.get('account.account')
        period_obj = self.pool.get('account.period')
        use_period_ids = main_filter in ('filter_no', 'filter_period', 'filter_opening')

        if use_period_ids:
            if main_filter == 'filter_opening':
                period_ids = [start.id]
            else:
                period_ids = period_obj.build_ctx_periods(cr, uid, start.id, stop.id)
                # never include the opening in the debit / credit amounts
                period_ids = self.exclude_opening_periods(period_ids)

        init_balance = False
        if initial_balance_mode == 'opening_balance':
            init_balance = self._read_opening_balance(account_ids, start)
        elif initial_balance_mode:
            init_balance = self._compute_initial_balances(cr, uid, account_ids, start, fiscalyear)

        ctx = context.copy()
        ctx.update({'state': target_move,
                    'all_fiscalyear': True})

        if use_period_ids:
            ctx.update({'periods': period_ids,})
        elif main_filter == 'filter_date':
            ctx.update({'date_from': start,
                        'date_to': stop})

        accounts = account_obj.read(cr, uid, account_ids, ['type','code','name','debit','credit', 'balance', 'parent_id','level','child_id'], ctx)

        accounts_by_id = {}
        for account in accounts:
            if init_balance:
                # sum for top level views accounts
                child_ids = account_obj._get_children_and_consol(cr, uid, account['id'], ctx)
                if child_ids:
                    child_init_balances = [init_bal['init_balance'] for acnt_id, init_bal in init_balance.iteritems() if acnt_id in child_ids ]
                    top_init_balance = reduce(add, child_init_balances)
                    account['init_balance'] = top_init_balance
                else:
                    account.update(init_balance[account['id']])
                account['balance'] = account['init_balance'] + account['debit'] - account['credit']
            accounts_by_id[account['id']] = account
        return accounts_by_id

    def _prac_amt(self, cr, uid, ids, context=None):
        res = {}
        result = 0.0
        if context is None: 
            context = {}
        account_obj = self.pool.get('account.account')
        for line in self.browse(cr, uid, ids, context=context):
            acc_ids = [x.id for x in line.general_budget_id.account_ids]
            if acc_ids:
                acc_ids = account_obj._get_children_and_consol(cr, uid, acc_ids, context=context)
            if not acc_ids:
                res[line.id] = 0
                #  raise osv.except_osv(_('Error!'), _("The Budget '%s' has no accounts!") % str(line.general_budget_id.name))

            date_to = context.get('wizard_date_to', line.date_to)
            date_from = context.get('wizard_date_from', line.date_from)
            if line.analytic_account_id.id:
                cr.execute("SELECT SUM(amount) FROM account_analytic_line WHERE account_id in "
                       """(with recursive account_analytic_account_hierarchy(id)
                        as 
                            (
                                select id from account_analytic_account 
                                    where id=%s
                                union all
                                select account_analytic_account.id from 
                                    account_analytic_account 
                                    join account_analytic_account_hierarchy
                                    on account_analytic_account.parent_id=
                                        account_analytic_account_hierarchy.id
                            )"""
                       "select id from account_analytic_account_hierarchy) "
                       "AND (date "
                       "between to_date(%s,'yyyy-mm-dd') AND to_date(%s,'yyyy-mm-dd')) AND "
                       "general_account_id=ANY(%s)", (line.analytic_account_id.id, date_from, date_to,acc_ids,))
                result = cr.fetchone()[0]
            else:
                fiscalyear_obj = self.pool['account.fiscalyear']
                fiscalyear_id = fiscalyear_obj.find(cr, uid, dt=date_from, context=context)
                fiscalyear = fiscalyear_obj.browse(cr, uid, fiscalyear_id, context)
                accounts_by_ids = self._get_account_details(cr, uid, acc_ids, target_move='posted', fiscalyear=fiscalyear, main_filter='filter_date', start=date_from,
                                                            stop=date_to, initial_balance_mode='initial_balance', context=context)
                result = 0.0
                for key in accounts_by_ids.keys():
                    result += accounts_by_ids[key]['balance']

            if result is None:
                result = 0.00
            res[line.id] = result
        return res

    def _prac(self, cr, uid, ids, name, args, context=None):
        res = {}
        for line in self.browse(cr, uid, ids, context=context):
            res[line.id] = self._prac_amt(cr, uid, [line.id], context=context)[line.id]
        return res

    def _theo_amt(self, cr, uid, ids, context=None):
        res = {}
        if context is None: 
            context = {}
        for line in self.browse(cr, uid, ids, context=context):
            today = datetime.datetime.today()
            date_to = context.get('wizard_date_to', today.strftime("%Y-%m-%d"))
            date_from = context.get('wizard_date_from', line.date_from)

            if line.paid_date:
                if strToDate(line.date_to) <= strToDate(line.paid_date):
                    theo_amt = 0.00
                else:
                    theo_amt = line.planned_amount
            else:
                total = strToDate(line.date_to) - strToDate(line.date_from)
                elapsed = min(strToDate(line.date_to),strToDate(date_to)) - max(strToDate(line.date_from),strToDate(date_from))
                if strToDate(date_to) < strToDate(line.date_from):
                    elapsed = strToDate(date_to) - strToDate(date_to)

                if total.days:
                    theo_amt = float((elapsed.days + 1) / float(total.days + 1)) * line.planned_amount
                else:
                    theo_amt = line.planned_amount

            res[line.id] = theo_amt
        return res

    def _theo(self, cr, uid, ids, name, args, context=None):
        res = {}
        for line in self.browse(cr, uid, ids, context=context):

            start_date = datetime.datetime.strptime(line.date_from, DEFAULT_SERVER_DATE_FORMAT)
            end_date = datetime.datetime.strptime(line.date_to, DEFAULT_SERVER_DATE_FORMAT)
            today = datetime.datetime.today()
            if today > end_date:
                today = end_date

            total_delta_months = relativedelta(end_date, start_date).months + 1  #  (end_date - start_date).days
            today_delta_months = relativedelta(today, start_date).months + 1  #  (end_date - start (today - start_date).days

            res[line.id] = (line.planned_amount / total_delta_months) * today_delta_months
        return res

    def _perc(self, cr, uid, ids, name, args, context=None):
        res = {}
        for line in self.browse(cr, uid, ids, context=context):
            if line.theoritical_amount != 0.00:
                res[line.id] = float((line.practical_amount or 0.0) / line.theoritical_amount) * 100
            else:
                res[line.id] = 0.00
        return res

    def _delta_prac(self, cr, uid, ids, name, args, context=None):
        res = {}
        for line in self.browse(cr, uid, ids, context=context):
            if line.practical_amount != 0.00:
                res[line.id] = line.planned_amount - line.practical_amount
            else:
                res[line.id] = 0.00
        return res

    def _practical_amount_month(self, cr, uid, ids, name, args, context=None):
        res = {}
        months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        account_obj = self.pool.get('account.account')
        for line in self.browse(cr, uid, ids, context=context):
            res[line.id] = {}
            acc_ids = [x.id for x in line.general_budget_id.account_ids]
            if acc_ids:
                acc_ids = account_obj._get_children_and_consol(cr, uid, acc_ids, context=context)
            date_from = line.date_from
            fiscalyear_obj = self.pool['account.fiscalyear']
            fiscalyear_id = fiscalyear_obj.find(cr, uid, dt=date_from, context=context)
            fiscalyear = fiscalyear_obj.browse(cr, uid, fiscalyear_id, context)

            start_date = datetime.datetime.strptime(line.date_from, DEFAULT_SERVER_DATE_FORMAT)

            month_number = 1
            for month in months:
                new_start_date = start_date
                date_from = datetime.date(start_date.year, month_number, 1)

                date_to = date_from + relativedelta(day=1, months=+1, days=-1)

                month_sum = 0
                accounts_by_ids = self._get_account_details(cr, uid, acc_ids, target_move='posted',
                                                            fiscalyear=fiscalyear,
                                                            main_filter='filter_date', start=date_from.strftime(DEFAULT_SERVER_DATE_FORMAT),
                                                            stop=date_to.strftime(DEFAULT_SERVER_DATE_FORMAT), initial_balance_mode=False,
                                                            context=context)

                for key in accounts_by_ids.keys():
                    month_sum += accounts_by_ids[key]['balance']
                res[line.id][month] = month_sum
                res[line.id][month + '_budget'] = line.planned_amount / 12
                res[line.id][month + '_delta'] = res[line.id][month + '_budget'] - res[line.id][month]

                month_number += 1

        return res

    _name = "crossovered.budget.lines"
    _description = "Budget Line"
    _columns = {
        'crossovered_budget_id': fields.many2one('crossovered.budget', 'Budget', ondelete='cascade', select=True, required=True),
        'analytic_account_id': fields.many2one('account.analytic.account', 'Analytic Account'),
        'general_budget_id': fields.many2one('account.budget.post', 'Budgetary Position',required=True),
        'date_from': fields.date('Start Date', required=True),
        'date_to': fields.date('End Date', required=True),
        'paid_date': fields.date('Paid Date'),
        'planned_amount': fields.float('Planned Amount', required=True, digits_compute=dp.get_precision('Account')),
        'practical_amount': fields.function(_prac, string='Practical Amount', type='float', digits_compute=dp.get_precision('Account'), store=False),
        'theoritical_amount': fields.function(_theo, string='Theoretical Amount', type='float', digits_compute=dp.get_precision('Account'), store=False),
        'delta_amount': fields.function(_delta_prac, string='Delta Amount', type='float', digits_compute=dp.get_precision('Account'), store=False),
        'percentage': fields.function(_perc, string='Percentage', type='float'),
        'company_id': fields.related('crossovered_budget_id', 'company_id', type='many2one', relation='res.company', string='Company', store=True, readonly=True),
        'jan': fields.function(_practical_amount_month, string='Gen', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'feb': fields.function(_practical_amount_month, string='Feb', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'mar': fields.function(_practical_amount_month, string='Mar', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'apr': fields.function(_practical_amount_month, string='Apr', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'may': fields.function(_practical_amount_month, string='Mag', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'jun': fields.function(_practical_amount_month, string='Giu', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'jul': fields.function(_practical_amount_month, string='Lug', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'aug': fields.function(_practical_amount_month, string='Ago', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'sep': fields.function(_practical_amount_month, string='Set', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'oct': fields.function(_practical_amount_month, string='Ott', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'nov': fields.function(_practical_amount_month, string='Nov', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'dec': fields.function(_practical_amount_month, string='Dic', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),

        'jan_budget': fields.function(_practical_amount_month, string='Gen', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'feb_budget': fields.function(_practical_amount_month, string='Feb', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'mar_budget': fields.function(_practical_amount_month, string='Mar', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'apr_budget': fields.function(_practical_amount_month, string='Apr', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'may_budget': fields.function(_practical_amount_month, string='Mag', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'jun_budget': fields.function(_practical_amount_month, string='Giu', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'jul_budget': fields.function(_practical_amount_month, string='Lug', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'aug_budget': fields.function(_practical_amount_month, string='Ago', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'sep_budget': fields.function(_practical_amount_month, string='Set', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'oct_budget': fields.function(_practical_amount_month, string='Ott', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'nov_budget': fields.function(_practical_amount_month, string='Nov', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),
        'dec_budget': fields.function(_practical_amount_month, string='Dic', type='float', multi='practical_amount_month',
                               digits_compute=dp.get_precision('Account')),

        'jan_delta': fields.function(_practical_amount_month, string='Gen', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'feb_delta': fields.function(_practical_amount_month, string='Feb', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'mar_delta': fields.function(_practical_amount_month, string='Mar', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'apr_delta': fields.function(_practical_amount_month, string='Apr', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'may_delta': fields.function(_practical_amount_month, string='Mag', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'jun_delta': fields.function(_practical_amount_month, string='Giu', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'jul_delta': fields.function(_practical_amount_month, string='Lug', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'aug_delta': fields.function(_practical_amount_month, string='Ago', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'sep_delta': fields.function(_practical_amount_month, string='Set', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'oct_delta': fields.function(_practical_amount_month, string='Ott', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'nov_delta': fields.function(_practical_amount_month, string='Nov', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),
        'dec_delta': fields.function(_practical_amount_month, string='Dic', type='float',
                                      multi='practical_amount_month',
                                      digits_compute=dp.get_precision('Account')),

    }

crossovered_budget_lines()

class account_analytic_account(osv.osv):
    _inherit = "account.analytic.account"

    _columns = {
        'crossovered_budget_line': fields.one2many('crossovered.budget.lines', 'analytic_account_id', 'Budget Lines'),
    }

account_analytic_account()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
