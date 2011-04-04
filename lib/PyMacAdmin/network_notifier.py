# encoding: utf-8

from subprocess import call

import logging
logger = logging.getLogger(__name__)

import re

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue

## need custom version of Growl Python bindings for identifier support
## webfaction:webapps/git_private/repos/growl_python.git
import Growl

IDENT_PREFIX = 'crankd.notifier.network'

def get_sc_value(key):
    return SCDynamicStoreCopyValue(STORE, key)


def growl(title, msg, sticky=False, priority=None, ident=IDENT_PREFIX):
    try:
        GROWLER.notify(
            'state_change',
            title,
            msg,
            sticky=sticky,
            # priority=Growl.growlPriority[priority],
            identifier=ident
        )
    except:
        logger.error("Unexpected error calling growl", exc_info = True)
    


def interface_state_change(key=None, re_obj=None, **kwargs):
    """State:/Network/Interface/([^/]+)/Link"""
    
    value = get_sc_value(key)
    iface = re_obj.match(key).groups()[0]
    ident = '%s.%s.%s' % (IDENT_PREFIX, iface, 'unknown')
    
    if not value:
        # interface disappeared
        logger.info("Interface %s disappeared", iface)
        ident = '%s.%s.%s' % (IDENT_PREFIX, iface, 'disappeared')
        message = '%s disappeared' % (iface,)
    else:
        if 'Active' in value and value['Active']:
            logger.info("Interface %s is now active", iface)
            ident = '%s.%s.%s' % (IDENT_PREFIX, iface, 'active')
            message = '%s is active' % (iface,)
            
        elif 'Detaching' in value:
            logger.info("Interface %s is detaching", iface)
            ident = '%s.%s.%s' % (IDENT_PREFIX, iface, 'detaching')
            message = '%s is detaching' % (iface,)
        
        else:
            logger.warn("what just happened? %s [%s]", iface, value)
            message = '%s: unknown' % (iface,)
    
    growl('Interface change', message, ident=ident)


def ipv4_state_change(key=None, **kwargs):
    """State:/Network/Global/IPv4"""
    
    value = get_sc_value(key)
    
    new_primary_iface = None
    
    sticky = False
    priority = None
    
    if value == None:
        logger.warn("Network is down!")
        
        title = 'Network change'
        message = 'Network is down!'
        sticky = True
        priority = 'High'
    else:
        svc_id = value['PrimaryService']
        new_primary_iface = value['PrimaryInterface']
        
        if STATE['primary'] != new_primary_iface:
            msg = "new primary interface: %s" % (new_primary_iface,)
            logger.info(msg)
            growl('Network change', msg, ident='crankd.notifier.network.primary_iface')
        
        service_value = get_sc_value('State:/Network/Service/%s/IPv4' % (svc_id,))
        ip_addr = service_value['Addresses'][0]
        
        logger.info('new IP for %s: %s' % (new_primary_iface, ip_addr))
        
        title = 'Network change'
        message = 'new IP for %s: %s' % (new_primary_iface, ip_addr)
        
        STATE['primary'] = new_primary_iface
    
    growl(title, message, sticky = sticky, priority = priority)


## globals and module startup stuff below
STORE = SCDynamicStoreCreate(None, "network_notifier", None , None)

GROWLER = Growl.GrowlNotifier(
    applicationName='crankd',
    notifications=["state_change"],
    applicationIcon=Growl.Image.imageFromPath("/System/Library/PreferencePanes/Network.prefPane/Contents/Resources/Network.icns"),

)
GROWLER.register()

STATE = {'primary': None}

tmp_val = get_sc_value("State:/Network/Global/IPv4")
if tmp_val:
    if 'PrimaryInterface' in tmp_val:
        STATE['primary'] = tmp_val['PrimaryInterface']

del tmp_val

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    ipv4_state_change("State:/Network/Global/IPv4")
    interface_state_change(key = "State:/Network/Interface/en1/Link",
                           re_obj = re.compile(r'''State:/Network/Interface/([^/]+)/Link'''))
