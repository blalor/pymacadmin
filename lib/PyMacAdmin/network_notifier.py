# encoding: utf-8

import logging
logger = logging.getLogger(__name__)

import socket

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue

## need custom version of Growl Python bindings for identifier support
## webfaction:webapps/git_private/repos/growl_python.git
import Growl

def get_sc_value(key):
    return SCDynamicStoreCopyValue(STORE, key)


def growl(title, msg, sticky=False, priority=None, ident='crankd.notifier.network'):
    try:
        GROWLER.notify('state_change', title, msg, sticky=sticky, identifier=ident)
    except:
        logger.error("Unexpected error calling growl", exc_info = True)
    


def state_change(key=None, **kwargs):
    """State:/Network/Global/IPv4"""
    
    value = get_sc_value(key)
    
    new_primary_iface = None
    
    if value == None:
        logger.warn("Network is down!")
        growl('Network change', 'Network is down!', sticky=True, priority='High')
    else:
        svc_id = value['PrimaryService']
        new_primary_iface = value['PrimaryInterface']
        
        if STATE['primary'] != new_primary_iface:
            msg = "new primary interface: %s" % (new_primary_iface,)
            logger.info(msg)
            growl('Network change', msg, ident='crankd.notifier.network.primary_iface')
        
        service_value = get_sc_value('State:/Network/Service/%s/IPv4' % (svc_id,))
        ip_addr = service_value['Addresses'][0]
        hostname = "unknown host"
        try:
            hostname = socket.gethostbyaddr(ip_addr)[0]
        except socket.herror:
            pass
        
        logger.info('new IP for %s: %s' % (new_primary_iface, ip_addr))
        growl(
            'Network change',
            'new IP for %s: %s\n%s' % (new_primary_iface, ip_addr, hostname)
        )
        
        STATE['primary'] = new_primary_iface


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
    state_change("State:/Network/Global/IPv4")
