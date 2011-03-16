# encoding: utf-8

from subprocess import call

import logging
logger = logging.getLogger(__name__)

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue

## need custom version of Growl Python bindings for identifier support
## webfaction:webapps/git_private/repos/growl_python.git
import Growl
growler = Growl.GrowlNotifier(
    applicationName='crankd',
    notifications=["state_change"],
    applicationIcon=Growl.Image.imageFromPath("/System/Library/PreferencePanes/Network.prefPane/Contents/Resources/Network.icns"),
)
growler.register()

STORE = SCDynamicStoreCreate(None, "network_notifier", None , None)

def get_sc_value(key):
    return SCDynamicStoreCopyValue(STORE, key)


def growl(title, msg, sticky=False, priority=None):
    try:
        growler.notify('state_change', title, msg, sticky=sticky, priority=priority, identifier='crankd.notifier.network')
    except:
        logger.error("Unexpected error calling growl", exc_info = True)
    


def state_change(key=None, **kwargs):
    """State:/Network/Global/IPv4"""
    
    value = get_sc_value(key)
    
    if value == None:
        logger.warn("Network is down!")
        growl('Network change', 'Network is down!', sticky=True, priority='High')
    else:
        svc_id = value['PrimaryService']
        iface = value['PrimaryInterface']
        
        service_value = get_sc_value('State:/Network/Service/%s/IPv4' % (svc_id,))
        ip_addr = service_value['Addresses'][0]
        
        logger.info('new IP for %s: %s' % (iface, ip_addr))
        growl(
            'Network change',
            'new IP for %s: %s' % (iface, ip_addr)
        )
    


if __name__ == '__main__':
    state_change("State:/Network/Global/IPv4")