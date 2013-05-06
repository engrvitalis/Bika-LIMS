# coding: utf-8
from AccessControl import ModuleSecurityInfo, allow_module
from DateTime import DateTime
from Products.Archetypes.public import DisplayList
from Products.CMFCore.utils import getToolByName
from Products.ATContentTypes.utils import DT2dt,dt2DT
from Products.CMFPlone.TranslationServiceTool import TranslationServiceTool
from bika.lims.browser import BrowserView
from bika.lims import bikaMessageFactory as _
from bika.lims import interfaces
from bika.lims import logger
from bika.lims.config import POINTS_OF_CAPTURE
from email.Utils import formataddr
from plone.i18n.normalizer.interfaces import IIDNormalizer
from reportlab.graphics.barcode import getCodes, getCodeNames, createBarcodeDrawing
from xml.sax.saxutils import escape, unescape
from zope.component import getUtility
from zope.interface import providedBy
from magnitude import mg, MagnitudeError
from Products.CMFPlone.utils import safe_unicode
import copy,re,urllib
import json
import plone.protect
import transaction

ModuleSecurityInfo('email.Utils').declarePublic('formataddr')
allow_module('csv')

def to_utf8(text):
    if type(text) == unicode:
        return text.encode('utf-8')
    else:
        return unicode(text).encode('utf-8')

# Wrapper for PortalTransport's sendmail - don't know why there sendmail
# method is marked private
ModuleSecurityInfo('Products.bika.utils').declarePublic('sendmail')
#Protected( Publish, 'sendmail')
def sendmail(portal, from_addr, to_addrs, msg):
    mailspool = portal.portal_mailspool
    mailspool.sendmail(from_addr, to_addrs, msg)

class js_log(BrowserView):
    def __call__(self, message):
        """Javascript sends a string for us to place into the log.
        """
        self.logger.info(message)

ModuleSecurityInfo('Products.bika.utils').declarePublic('printfile')
def printfile(portal, from_addr, to_addrs, msg):
    import os

    """ set the path, then the cmd 'lpr filepath'
    temp_path = 'C:/Zope2/Products/Bika/version.txt'

    os.system('lpr "%s"' %temp_path)
    """
    pass

def getUsers(context, roles, allow_empty=True):
    """ Present a DisplayList containing users in the specified
        list of roles
    """
    mtool = getToolByName(context, 'portal_membership')
    pairs = allow_empty and [['','']] or []
    users = mtool.searchForMembers(roles = roles)
    for user in users:
        uid = user.getId()
        fullname = user.getProperty('fullname')
        if fullname is None:
            fullname = uid
        pairs.append((uid, fullname))
    pairs.sort(lambda x, y: cmp(x[1].lower(), y[1].lower()))
    return DisplayList(pairs)

def isActive(obj):
    """ Check if obj is inactive or cancelled.
    """
    wf = getToolByName(obj, 'portal_workflow')
    if (hasattr(obj, 'inactive_state') and obj.inactive_state == 'inactive') or \
       wf.getInfoFor(obj, 'inactive_state', 'active') == 'inactive':
        return False
    if (hasattr(obj, 'cancellation_state') and obj.inactive_state == 'cancelled') or \
       wf.getInfoFor(obj, 'cancellation_state', 'active') == 'cancelled':
        return False
    return True

def formatDateQuery(context, date_id):
    """ Obtain and reformat the from and to dates
        into a date query construct
    """
    from_date = context.REQUEST.get('%s_fromdate' % date_id, None)
    if from_date:
        from_date = from_date + ' 00:00'
    to_date = context.REQUEST.get('%s_todate' % date_id, None)
    if to_date:
        to_date = to_date + ' 23:59'

    date_query = {}
    if from_date and to_date:
        date_query = {'query': [from_date, to_date],
                      'range': 'min:max'}
    elif from_date or to_date:
        date_query = {'query': from_date or to_date,
                      'range': from_date and 'min' or 'max'}

    return date_query

def formatDateParms(context, date_id):
    """ Obtain and reformat the from and to dates
        into a printable date parameter construct
    """
    from_date = context.REQUEST.get('%s_fromdate' % date_id, None)
    to_date = context.REQUEST.get('%s_todate' % date_id, None)

    date_parms = {}
    if from_date and to_date:
        date_parms = 'from %s to %s' %(from_date, to_date)
    elif from_date:
        date_parms = 'from %s' %(from_date)
    elif to_date:
        date_parms = 'to %s' %(to_date)

    return date_parms

def formatDuration(context, totminutes):
    """ Format a time period in a usable manner: eg. 3h24m
    """
    mins = totminutes % 60
    hours = (totminutes - mins) / 60

    if mins:
        mins_str = '%sm' % mins
    else:
        mins_str = ''

    if hours:
        hours_str = '%sh' % hours
    else:
        hours_str = ''

    return '%s%s' % (hours_str, mins_str)

# encode_header function copied from roundup's rfc2822 package.
hqre = re.compile(r'^[A-z0-9!"#$%%&\'()*+,-./:;<=>?@\[\]^_`{|}~ ]+$')

ModuleSecurityInfo('Products.bika.utils').declarePublic('encode_header')
def encode_header(header, charset = 'utf-8'):
    """ Will encode in quoted-printable encoding only if header
    contains non latin characters
    """

    # Return empty headers unchanged
    if not header:
        return header

    # return plain header if it does not contain non-ascii characters
    if hqre.match(header):
        return header

    quoted = ''
    #max_encoded = 76 - len(charset) - 7
    for c in header:
        # Space may be represented as _ instead of =20 for readability
        if c == ' ':
            quoted += '_'
        # These characters can be included verbatim
        elif hqre.match(c):
            quoted += c
        # Otherwise, replace with hex value like =E2
        else:
            quoted += "=%02X" % ord(c)
            plain = 0

    return '=?%s?q?%s?=' % (charset, quoted)

def zero_fill(matchobj):
    return matchobj.group().zfill(8)

num_sort_regex = re.compile('\d+')

ModuleSecurityInfo('Products.bika.utils').declarePublic('sortable_title')
def sortable_title(portal, title):
    """Convert title to sortable title
    """
    if not title:
        return ''

    def_charset = portal.plone_utils.getSiteEncoding()
    sortabletitle = title.lower().strip()
    # Replace numbers with zero filled numbers
    sortabletitle = num_sort_regex.sub(zero_fill, sortabletitle)
    # Truncate to prevent bloat
    for charset in [def_charset, 'latin-1', 'utf-8']:
        try:
            sortabletitle = unicode(sortabletitle, charset)[:30]
            sortabletitle = sortabletitle.encode(def_charset or 'utf-8')
            break
        except UnicodeError:
            pass
        except TypeError:
            # If we get a TypeError if we already have a unicode string
            sortabletitle = sortabletitle[:30]
            break
    return sortabletitle

def logged_in_client(context, member=None):
    if not member:
        membership_tool=getToolByName(context, 'portal_membership')
        member = membership_tool.getAuthenticatedMember()

    client = None
    groups_tool=context.portal_groups
    member_groups = [groups_tool.getGroupById(group.id).getGroupName()
                 for group in groups_tool.getGroupsByUserId(member.id)]

    if 'Clients' in member_groups:
        for obj in context.clients.objectValues("Client"):
            if member.id in obj.users_with_local_role('Owner'):
                client = obj
    return client

def changeWorkflowState(content, wf_id, state_id, acquire_permissions=False,
                        portal_workflow=None, **kw):
    """Change the workflow state of an object
    @param content: Content obj which state will be changed
    @param state_id: name of the state to put on content
    @param acquire_permissions: True->All permissions unchecked and on riles and
                                acquired
                                False->Applies new state security map
    @param portal_workflow: Provide workflow tool (optimisation) if known
    @param kw: change the values of same name of the state mapping
    @return: None
    """

    if portal_workflow is None:
        portal_workflow = getToolByName(content, 'portal_workflow')

    # Might raise IndexError if no workflow is associated to this type
    found_wf = 0
    for wf_def in portal_workflow.getWorkflowsFor(content):
        if wf_id == wf_def.getId():
            found_wf = 1
            break
    if not found_wf:
        logger.error("%s: Cannot find workflow id %s" % (content, wf_id))

    wf_state = {
        'action': None,
        'actor': None,
        'comments': "Setting state to %s" % state_id,
        'review_state': state_id,
        'time': DateTime(),
        }

    # Updating wf_state from keyword args
    for k in kw.keys():
        # Remove unknown items
        if not wf_state.has_key(k):
            del kw[k]
    if kw.has_key('review_state'):
        del kw['review_state']
    wf_state.update(kw)

    portal_workflow.setStatusOf(wf_id, content, wf_state)

    if acquire_permissions:
        # Acquire all permissions
        for permission in content.possible_permissions():
            content.manage_permission(permission, acquire=1)
    else:
        # Setting new state permissions
        wf_def.updateRoleMappingsFor(content)

    # Map changes to the catalogs
    content.reindexObject(idxs=['allowedRolesAndUsers', 'review_state'])
    return

# escape() and unescape() takes care of &, < and >.
html_unescape_table = {
    "&#8482;": safe_unicode("™").encode("utf-8"),
    "&euro;": safe_unicode("€").encode("utf-8"),
    "&#94;": safe_unicode("^").encode("utf-8"),
    "&#96;": safe_unicode("`").encode("utf-8"),
    "&#126;": safe_unicode("~").encode("utf-8"),
    "&cent;": safe_unicode("¢").encode("utf-8"),
    "&pound;": safe_unicode("£").encode("utf-8"),
    "&curren;": safe_unicode("¤").encode("utf-8"),
    "&yen;": safe_unicode("¥").encode("utf-8"),
    "&brvbar;": safe_unicode("¦").encode("utf-8"),
    "&sect;": safe_unicode("§").encode("utf-8"),
    "&uml;": safe_unicode("¨").encode("utf-8"),
    "&copy;": safe_unicode("©").encode("utf-8"),
    "&ordf;": safe_unicode("ª").encode("utf-8"),
    "&#171;": safe_unicode("«").encode("utf-8"),
    "&not;": safe_unicode("¬").encode("utf-8"),
    "&reg;": safe_unicode("®").encode("utf-8"),
    "&macr;": safe_unicode("¯").encode("utf-8"),
    "&deg;": safe_unicode("°").encode("utf-8"),
    "&plusmn;": safe_unicode("±").encode("utf-8"),
    "&sup2;": safe_unicode("²").encode("utf-8"),
    "&sup3;": safe_unicode("³").encode("utf-8"),
    "&acute;": safe_unicode("´").encode("utf-8"),
    "&micro;": safe_unicode("µ").encode("utf-8"),
    "&para;": safe_unicode("¶").encode("utf-8"),
    "&middot;": safe_unicode("·").encode("utf-8"),
    "&cedil;": safe_unicode("¸").encode("utf-8"),
    "&sup1;": safe_unicode("¹").encode("utf-8"),
    "&ordm;": safe_unicode("º").encode("utf-8"),
    "&raquo;": safe_unicode("»").encode("utf-8"),
    "&frac14;": safe_unicode("¼").encode("utf-8"),
    "&frac12;": safe_unicode("½").encode("utf-8"),
    "&frac34;": safe_unicode("¾").encode("utf-8"),
    "&iquest;": safe_unicode("¿").encode("utf-8"),
    "&Agrave;": safe_unicode("À").encode("utf-8"),
    "&Aacute;": safe_unicode("Á").encode("utf-8"),
    "&Acirc;": safe_unicode("Â").encode("utf-8"),
    "&Atilde;": safe_unicode("Ã").encode("utf-8"),
    "&Auml;": safe_unicode("Ä").encode("utf-8"),
    "&Aring;": safe_unicode("Å").encode("utf-8"),
    "&AElig;": safe_unicode("Æ").encode("utf-8"),
    "&Ccedil;": safe_unicode("Ç").encode("utf-8"),
    "&Egrave;": safe_unicode("È").encode("utf-8"),
    "&Eacute;": safe_unicode("É").encode("utf-8"),
    "&Ecirc;": safe_unicode("Ê").encode("utf-8"),
    "&Euml;": safe_unicode("Ë").encode("utf-8"),
    "&Igrave;": safe_unicode("Ì").encode("utf-8"),
    "&Iacute;": safe_unicode("Í").encode("utf-8"),
    "&Icirc;": safe_unicode("Î").encode("utf-8"),
    "&Iuml;": safe_unicode("Ï").encode("utf-8"),
    "&ETH;": safe_unicode("Ð").encode("utf-8"),
    "&Ntilde;": safe_unicode("Ñ").encode("utf-8"),
    "&Ograve;": safe_unicode("Ò").encode("utf-8"),
    "&Oacute;": safe_unicode("Ó").encode("utf-8"),
    "&Ocirc;": safe_unicode("Ô").encode("utf-8"),
    "&Otilde;": safe_unicode("Õ").encode("utf-8"),
    "&Ouml;": safe_unicode("Ö").encode("utf-8"),
    "&times;": safe_unicode("×").encode("utf-8"),
    "&Oslash;": safe_unicode("Ø").encode("utf-8"),
    "&Ugrave;": safe_unicode("Ù").encode("utf-8"),
    "&Uacute;": safe_unicode("Ú").encode("utf-8"),
    "&Ucirc;": safe_unicode("Û").encode("utf-8"),
    "&Uuml;": safe_unicode("Ü").encode("utf-8"),
    "&Yacute;": safe_unicode("Ý").encode("utf-8"),
    "&THORN;": safe_unicode("Þ").encode("utf-8"),
    "&szlig;": safe_unicode("ß").encode("utf-8"),
    "&agrave;": safe_unicode("à").encode("utf-8"),
    "&aacute;": safe_unicode("á").encode("utf-8"),
    "&acirc;": safe_unicode("â").encode("utf-8"),
    "&atilde;": safe_unicode("ã").encode("utf-8"),
    "&auml;": safe_unicode("ä").encode("utf-8"),
    "&aring;": safe_unicode("å").encode("utf-8"),
    "&aelig;": safe_unicode("æ").encode("utf-8"),
    "&ccedil;": safe_unicode("ç").encode("utf-8"),
    "&egrave;": safe_unicode("è").encode("utf-8"),
    "&eacute;": safe_unicode("é").encode("utf-8"),
    "&ecirc;": safe_unicode("ê").encode("utf-8"),
    "&euml;": safe_unicode("ë").encode("utf-8"),
    "&igrave;": safe_unicode("ì").encode("utf-8"),
    "&iacute;": safe_unicode("í").encode("utf-8"),
    "&icirc;": safe_unicode("î").encode("utf-8"),
    "&iuml;": safe_unicode("ï").encode("utf-8"),
    "&eth;": safe_unicode("ð").encode("utf-8"),
    "&ntilde;": safe_unicode("ñ").encode("utf-8"),
    "&ograve;": safe_unicode("ò").encode("utf-8"),
    "&oacute;": safe_unicode("ó").encode("utf-8"),
    "&ocirc;": safe_unicode("ô").encode("utf-8"),
    "&otilde;": safe_unicode("õ").encode("utf-8"),
    "&ouml;": safe_unicode("ö").encode("utf-8"),
    "&divide;": safe_unicode("÷").encode("utf-8"),
    "&oslash;": safe_unicode("ø").encode("utf-8"),
    "&ugrave;": safe_unicode("ù").encode("utf-8"),
    "&uacute;": safe_unicode("ú").encode("utf-8"),
    "&ucirc;": safe_unicode("û").encode("utf-8"),
    "&uuml;": safe_unicode("ü").encode("utf-8"),
    "&yacute;": safe_unicode("ý").encode("utf-8"),
    "&thorn;": safe_unicode("þ").encode("utf-8"),
    "&#255;": safe_unicode("ÿ").encode("utf-8"),
    "&#256;": safe_unicode("Ā").encode("utf-8"),
    "&#257;": safe_unicode("ā").encode("utf-8"),
    "&#258;": safe_unicode("Ă").encode("utf-8"),
    "&#259;": safe_unicode("ă").encode("utf-8"),
    "&#260;": safe_unicode("Ą").encode("utf-8"),
    "&#261;": safe_unicode("ą").encode("utf-8"),
    "&#262;": safe_unicode("Ć").encode("utf-8"),
    "&#263;": safe_unicode("ć").encode("utf-8"),
    "&#264;": safe_unicode("Ĉ").encode("utf-8"),
    "&#265;": safe_unicode("ĉ").encode("utf-8"),
    "&#266;": safe_unicode("Ċ").encode("utf-8"),
    "&#267;": safe_unicode("ċ").encode("utf-8"),
    "&#268;": safe_unicode("Č").encode("utf-8"),
    "&#269;": safe_unicode("č").encode("utf-8"),
    "&#270;": safe_unicode("Ď").encode("utf-8"),
    "&#271;": safe_unicode("ď").encode("utf-8"),
    "&#272;": safe_unicode("Đ").encode("utf-8"),
    "&#273;": safe_unicode("đ").encode("utf-8"),
    "&#274;": safe_unicode("Ē").encode("utf-8"),
    "&#275;": safe_unicode("ē").encode("utf-8"),
    "&#276;": safe_unicode("Ĕ").encode("utf-8"),
    "&#277": safe_unicode("ĕ").encode("utf-8"),
    "&#278;": safe_unicode("Ė").encode("utf-8"),
    "&#279;": safe_unicode("ė").encode("utf-8"),
    "&#280;": safe_unicode("Ę").encode("utf-8"),
    "&#281;": safe_unicode("ę").encode("utf-8"),
    "&#282;": safe_unicode("Ě").encode("utf-8"),
    "&#283;": safe_unicode("ě").encode("utf-8"),
    "&#284;": safe_unicode("Ĝ").encode("utf-8"),
    "&#285;": safe_unicode("ĝ").encode("utf-8"),
    "&#286;": safe_unicode("Ğ").encode("utf-8"),
    "&#287;": safe_unicode("ğ").encode("utf-8"),
    "&#288;": safe_unicode("Ġ").encode("utf-8"),
    "&#289;": safe_unicode("ġ").encode("utf-8"),
    "&#290;": safe_unicode("Ģ").encode("utf-8"),
    "&#291;": safe_unicode("ģ").encode("utf-8"),
    "&#292;": safe_unicode("Ĥ").encode("utf-8"),
    "&#293;": safe_unicode("ĥ").encode("utf-8"),
    "&#294;": safe_unicode("Ħ").encode("utf-8"),
    "&#295;": safe_unicode("ħ").encode("utf-8"),
    "&#296;": safe_unicode("Ĩ").encode("utf-8"),
    "&#297;": safe_unicode("ĩ").encode("utf-8"),
    "&#298;": safe_unicode("Ī").encode("utf-8"),
    "&#299;": safe_unicode("ī").encode("utf-8"),
    "&#300;": safe_unicode("Ĭ").encode("utf-8"),
    "&#301;": safe_unicode("ĭ").encode("utf-8"),
    "&#302;": safe_unicode("Į").encode("utf-8"),
    "&#303;": safe_unicode("į").encode("utf-8"),
    "&#304;": safe_unicode("İ").encode("utf-8"),
    "&#305;": safe_unicode("ı").encode("utf-8"),
    "&#306;": safe_unicode("Ĳ").encode("utf-8"),
    "&#307;": safe_unicode("ĳ").encode("utf-8"),
    "&#308;": safe_unicode("Ĵ").encode("utf-8"),
    "&#309;": safe_unicode("ĵ").encode("utf-8"),
    "&#310;": safe_unicode("Ķ").encode("utf-8"),
    "&#311;": safe_unicode("ķ").encode("utf-8"),
    "&#312;": safe_unicode("ĸ").encode("utf-8"),
    "&#313;": safe_unicode("Ĺ").encode("utf-8"),
    "&#314;": safe_unicode("ĺ").encode("utf-8"),
    "&#315;": safe_unicode("Ļ").encode("utf-8"),
    "&#316;": safe_unicode("ļ").encode("utf-8"),
    "&#317": safe_unicode("Ľ").encode("utf-8"),
    "&#318;": safe_unicode("ľ").encode("utf-8"),
    "&#319;": safe_unicode("Ŀ").encode("utf-8"),
    "&#320;": safe_unicode("ŀ").encode("utf-8"),
    "&#321;": safe_unicode("Ł").encode("utf-8"),
    "&#322;": safe_unicode("ł").encode("utf-8"),
    "&#323;": safe_unicode("Ń").encode("utf-8"),
    "&#324;": safe_unicode("ń").encode("utf-8"),
    "&#325;": safe_unicode("Ņ").encode("utf-8"),
    "&#326;": safe_unicode("ņ").encode("utf-8"),
    "&#327;": safe_unicode("Ň").encode("utf-8"),
    "&#328;": safe_unicode("ň").encode("utf-8"),
    "&#329;": safe_unicode("ŉ").encode("utf-8"),
    "&#330;": safe_unicode("Ŋ").encode("utf-8"),
    "&#331;": safe_unicode("ŋ").encode("utf-8"),
    "&#332;": safe_unicode("Ō").encode("utf-8"),
    "&#333;": safe_unicode("ō").encode("utf-8"),
    "&#334;": safe_unicode("Ŏ").encode("utf-8"),
    "&#335;": safe_unicode("ŏ").encode("utf-8"),
    "&#336;": safe_unicode("Ő").encode("utf-8"),
    "&#337;": safe_unicode("ő").encode("utf-8"),
    "&#338;": safe_unicode("Œ").encode("utf-8"),
    "&#339;": safe_unicode("œ").encode("utf-8"),
    "&#340;": safe_unicode("Ŕ").encode("utf-8"),
    "&#341;": safe_unicode("ŕ").encode("utf-8"),
    "&#342;": safe_unicode("Ŗ").encode("utf-8"),
    "&#343;": safe_unicode("ŗ").encode("utf-8"),
    "&#344;": safe_unicode("Ř").encode("utf-8"),
    "&#345;": safe_unicode("ř").encode("utf-8"),
    "&#346;": safe_unicode("Ś").encode("utf-8"),
    "&#347;": safe_unicode("ś").encode("utf-8"),
    "&#348;": safe_unicode("Ŝ").encode("utf-8"),
    "&#349;": safe_unicode("ŝ").encode("utf-8"),
    "&#350;": safe_unicode("Ş").encode("utf-8"),
    "&#351;": safe_unicode("ş").encode("utf-8"),
    "&#352;": safe_unicode("Š").encode("utf-8"),
    "&#353;": safe_unicode("š").encode("utf-8"),
    "&#354;": safe_unicode("Ţ").encode("utf-8"),
    "&#355;": safe_unicode("ţ").encode("utf-8"),
    "&#356;": safe_unicode("Ť").encode("utf-8"),
    "&#357": safe_unicode("ť").encode("utf-8"),
    "&#358;": safe_unicode("Ŧ").encode("utf-8"),
    "&#359;": safe_unicode("ŧ").encode("utf-8"),
    "&#360;": safe_unicode("Ũ").encode("utf-8"),
    "&#361;": safe_unicode("ũ").encode("utf-8"),
    "&#362;": safe_unicode("Ū").encode("utf-8"),
    "&#363;": safe_unicode("ū").encode("utf-8"),
    "&#364;": safe_unicode("Ŭ").encode("utf-8"),
    "&#365;": safe_unicode("ŭ").encode("utf-8"),
    "&#366;": safe_unicode("Ů").encode("utf-8"),
    "&#367;": safe_unicode("ů").encode("utf-8"),
    "&#368;": safe_unicode("Ű").encode("utf-8"),
    "&#369;": safe_unicode("ű").encode("utf-8"),
    "&#370;": safe_unicode("Ų").encode("utf-8"),
    "&#371;": safe_unicode("ų").encode("utf-8"),
    "&#372;": safe_unicode("Ŵ").encode("utf-8"),
    "&#373;": safe_unicode("ŵ").encode("utf-8"),
    "&#374;": safe_unicode("Ŷ").encode("utf-8"),
    "&#375;": safe_unicode("ŷ").encode("utf-8"),
    "&#376;": safe_unicode("Ÿ").encode("utf-8"),
    "&#377;": safe_unicode("Ź").encode("utf-8"),
    "&#378;": safe_unicode("ź").encode("utf-8"),
    "&#379;": safe_unicode("Ż").encode("utf-8"),
    "&#380;": safe_unicode("ż").encode("utf-8"),
    "&#381;": safe_unicode("Ž").encode("utf-8"),
    "&#382;": safe_unicode("ž").encode("utf-8"),
    "&#383;": safe_unicode("ſ").encode("utf-8"),
    "&#340;": safe_unicode("Ŕ").encode("utf-8"),
    "&#341;": safe_unicode("ŕ").encode("utf-8"),
    "&#342;": safe_unicode("Ŗ").encode("utf-8"),
    "&#343;": safe_unicode("ŗ").encode("utf-8"),
    "&#344;": safe_unicode("Ř").encode("utf-8"),
    "&#345;": safe_unicode("ř").encode("utf-8"),
    "&#346;": safe_unicode("Ś").encode("utf-8"),
    "&#347;": safe_unicode("ś").encode("utf-8"),
    "&#348;": safe_unicode("Ŝ").encode("utf-8"),
    "&#349;": safe_unicode("ŝ").encode("utf-8"),
    "&#350;": safe_unicode("Ş").encode("utf-8"),
    "&#351;": safe_unicode("ş").encode("utf-8"),
    "&#352;": safe_unicode("Š").encode("utf-8"),
    "&#353;": safe_unicode("š").encode("utf-8"),
    "&#354;": safe_unicode("Ţ").encode("utf-8"),
    "&#355;": safe_unicode("ţ").encode("utf-8"),
    "&#356;": safe_unicode("Ť").encode("utf-8"),
    "&#577;": safe_unicode("ť").encode("utf-8"),
    "&#358;": safe_unicode("Ŧ").encode("utf-8"),
    "&#359;": safe_unicode("ŧ").encode("utf-8"),
    "&#360;": safe_unicode("Ũ").encode("utf-8"),
    "&#361;": safe_unicode("ũ").encode("utf-8"),
    "&#362;": safe_unicode("Ū").encode("utf-8"),
    "&#363;": safe_unicode("ū").encode("utf-8"),
    "&#364;": safe_unicode("Ŭ").encode("utf-8"),
    "&#365;": safe_unicode("ŭ").encode("utf-8"),
    "&#366;": safe_unicode("Ů").encode("utf-8"),
    "&#367;": safe_unicode("ů").encode("utf-8"),
    "&#368;": safe_unicode("Ű").encode("utf-8"),
    "&#369;": safe_unicode("ű").encode("utf-8"),
    "&#370;": safe_unicode("Ų").encode("utf-8"),
    "&#371;": safe_unicode("ų").encode("utf-8"),
    "&#372;": safe_unicode("Ŵ").encode("utf-8"),
    "&#373;": safe_unicode("ŵ").encode("utf-8"),
    "&#374;": safe_unicode("Ŷ").encode("utf-8"),
    "&#375;": safe_unicode("ŷ").encode("utf-8"),
    "&#376;": safe_unicode("Ÿ").encode("utf-8"),
    "&#377": safe_unicode("Ź").encode("utf-8"),
    "&#378;": safe_unicode("ź").encode("utf-8"),
    "&#379;": safe_unicode("Ż").encode("utf-8"),
    "&#380;": safe_unicode("ż").encode("utf-8"),
    "&#381;": safe_unicode("Ž").encode("utf-8"),
    "&#382;": safe_unicode("ž").encode("utf-8"),
    "&#383;": safe_unicode("ſ").encode("utf-8"),
}


def html_escape(text):
    import re
    html_escape_table = {v: k for k, v in html_unescape_table.items()}
    pattern = re.compile('|'.join(re.escape(key) for key in html_escape_table.keys()))
    return pattern.sub(lambda x: html_escape_table[x.group()], text)


class bika_bsc_counter(BrowserView):
    def __call__(self):
        bsc = getToolByName(self.context, 'bika_setup_catalog')
        return bsc.getCounter()

class bika_browserdata(BrowserView):
    """Returns information about services from bika_setup_catalog.
    This view is called from ./js/utils.js and it's output is cached
    in browser localStorage.
    """
    def __call__(self):
        translate = self.context.translate
        bsc = getToolByName(self.context, 'bika_setup_catalog')

        data = {
            'categories':{},  # services keyed by "POC_category"
            'services':{},    # service info, keyed by UID
        }

        ## Loop ALL SERVICES
        services = dict([(b.UID, b.getObject()) for b
                         in bsc(portal_type = "AnalysisService",
                                inactive_state = "active")])

        for uid, service in services.items():
            ## Store categories
            ## data['categories'][poc_catUID]: [uid, uid]
            key = "%s_%s" % (service.getPointOfCapture(),
                             service.getCategoryUID())
            if key in data['categories']:
                data['categories'][key].append(uid)
            else:
                data['categories'][key] = [uid, ]

            ## Get dependants
            ## (this service's Calculation backrefs' dependencies)
            backrefs = []
            # this function follows all backreferences so we need skip to
            # avoid recursion. It should maybe be modified to be more smart...
            skip = []
            def walk(items):
                for item in items:
                    if item.portal_type == 'AnalysisService'\
                       and item.UID() != service.UID():
                        backrefs.append(item)
                    if item not in skip:
                        skip.append(item)
                        brefs = item.getBackReferences()
                        walk(brefs)
            walk([service, ])

            ## Get dependencies
            ## (services we depend on)
            deps = {}
            calc = service.getCalculation()
            if calc:
                td = calc.getCalculationDependencies()
                def walk(td):
                    for depserv_uid, depserv_deps in td.items():
                        if depserv_uid == uid:
                            continue
                        depserv = services[depserv_uid]
                        category = depserv.getCategory()
                        cat = '%s_%s' % (category.UID(), category.Title())
                        poc = '%s_%s' % \
                            (depserv.getPointOfCapture(),
                             POINTS_OF_CAPTURE.getValue(depserv.getPointOfCapture()))
                        srv = '%s_%s' % (depserv.UID(), depserv.Title())
                        if not deps.has_key(poc): deps[poc] = {}
                        if not deps[poc].has_key(cat): deps[poc][cat] = []
                        if not srv in deps[poc][cat]:
                            deps[poc][cat].append(srv)
                        if depserv_deps:
                            walk(depserv_deps)
                walk(td)

            ## Get partition setup records for this service
            separate = service.getSeparate()
            containers = service.getContainer()
            try:
                containers.sort(lambda a,b:cmp(
                    int((hasattr(a, 'getJSCapacity') and a.getJSCapacity() and a.getJSCapacity().split(" ")[0]) or '0'),
                    int((hasattr(b, 'getJSCapacity') and b.getJSCapacity() and b.getJSCapacity().split(" ")[0]) or '0')
                ))
            except:
                pass
            preservations = service.getPreservation()
            partsetup = service.getPartitionSetup()

            # Single values become lists here
            for x in range(len(partsetup)):
                if partsetup[x].has_key('container') \
                   and type(partsetup[x]['container']) == str:
                    partsetup[x]['container'] = [partsetup[x]['container'],]
                if partsetup[x].has_key('preservation') \
                   and type(partsetup[x]['preservation']) == str:
                    partsetup[x]['preservation'] = [partsetup[x]['preservation'],]
                minvol = partsetup[x].get('vol', '0 g')
                try:
                    mgminvol = minvol.split(' ', 1)
                    mgminvol = mg(float(mgminvol[0]), mgminvol[1])
                except:
                    mgminvol = mg(0, 'ml')
                try:
                    mgminvol = str(mgminvol.ounit('ml'))
                except:
                    pass
                try:
                    mgminvol = str(mgminvol.ounit('g'))
                except:
                    pass
                partsetup[x]['vol'] = str(mgminvol)

            ## If no dependents, backrefs or partition setup exists
            ## nothing is stored for this service
            if not (backrefs or deps or separate or
                    containers or preservations or partsetup):
                continue

            # store info for this service
            data['services'][uid] = {
                'backrefs':[s.UID() for s in backrefs],
                'deps':deps,
            }

            data['services'][uid]['Separate'] = separate
            data['services'][uid]['Container'] = \
                [container.UID() for container in containers]
            data['services'][uid]['Preservation'] = \
                [pres.UID() for pres in preservations]
            data['services'][uid]['PartitionSetup'] = \
                partsetup

        uc = getToolByName(self.context, 'uid_catalog')

        ## SamplePoint and SampleType autocomplete lookups need a reference
        ## to resolve Title->UID
        data['st_uids'] = {}
        for st_proxy in bsc(portal_type = 'SampleType',
                        inactive_state = 'active'):
            st = st_proxy.getObject()
            data['st_uids'][st.Title()] = {
                'uid':st.UID(),
                'minvol': st.getJSMinimumVolume(),
                'containertype': st.getContainerType() and st.getContainerType().UID() or '',
                'samplepoints': [sp.Title() for sp in st.getSamplePoints()]
            }

        data['sp_uids'] = {}
        for sp_proxy in bsc(portal_type = 'SamplePoint',
                        inactive_state = 'active'):
            sp = sp_proxy.getObject()
            data['sp_uids'][sp.Title()] = {
                'uid':sp.UID(),
                'composite':sp.getComposite(),
                'sampletypes': [st.Title() for st in sp.getSampleTypes()]
            }

        data['containers'] = {}
        for c_proxy in bsc(portal_type = 'Container'):
            c = c_proxy.getObject()
            pres = c.getPreservation()
            data['containers'][c.UID()] = {
                'title':c.Title(),
                'uid':c.UID(),
                'containertype': c.getContainerType() and c.getContainerType().UID() or '',
                'prepreserved':c.getPrePreserved(),
                'preservation':pres and pres.UID() or '',
                'capacity':c.getJSCapacity(),
            }

        data['preservations'] = {}
        for p_proxy in bsc(portal_type = 'Preservation'):
            p = p_proxy.getObject()
            data['preservations'][p.UID()] = {
                'title':p.Title(),
                'uid':p.UID(),
            }

        data['prefixes'] = dict([(p['portal_type'], p['prefix']) for p in self.context.bika_setup.getPrefixes()])

        return json.dumps(data)
