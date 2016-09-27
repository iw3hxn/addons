from osv import fields, osv
from tools.translate import _
from mail.mail_message import truncate_text


class crm_add_note(osv.osv_memory):
    """Adds a new note to the case."""
    _name = 'purchase.add.note'
    _description = "Add Internal Note"

    _columns = {
        'body': fields.text('Note Body', required=True),
    }

    def action_add(self, cr, uid, ids, context=None):
        if context is None:
            context = {}

        if not context.get('active_model'):
            raise osv.except_osv(_('Error'), _('Can not add note!'))

        model = context.get('active_model')
        case_pool = self.pool.get(model)

        for obj in self.browse(cr, uid, ids, context=context):
            case_list = case_pool.browse(cr, uid, context['active_ids'],
                                         context=context)
            case = case_list[0]
            case_pool.message_append(cr, uid, [case], truncate_text(obj.body),
                                     body_text=obj.body)

        return {'type': 'ir.actions.act_window_close'}