# encoding: utf-8

import sys
from subprocess import call

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue

STORE = SCDynamicStoreCreate(None, "network_notifier", None , None)

def get_sc_value(key):
    return SCDynamicStoreCopyValue(STORE, key)


def growl(title, msg, sticky=False, priority=None):
    growl_args = [
        '/Users/ulalobr/bin/.Darwin/growlnotify',
        '-n', 'crankd',
        '-t', title,
        '-m', msg,
        '-d', 'crankd.notifier.network',
        '--image', '/System/Library/PreferencePanes/Network.prefPane/Contents/Resources/Network.icns',
    ]
    
    if sticky:
        growl_args.append('-s')
    
    if priority != None:
        growl_args.extend(('-p', priority,))
    
    # print growl_args
    try:
        call(growl_args)
    except:
        print "Unexpected error calling growl:", sys.exc_info()[0]
    


def state_change(key=None, **kwargs):
    """State:/Network/Global/IPv4"""
    
    print ((key, kwargs))
    value = get_sc_value(key)
    
    if value == None:
        growl('Network change', 'Network is down!', sticky=True, priority='High')
    else:
        svc_id = value['PrimaryService']
        iface = value['PrimaryInterface']
        
        service_value = get_sc_value('State:/Network/Service/%s/IPv4' % (svc_id,))
        ip_addr = service_value['Addresses'][0]
        
        growl(
            'Network change',
            'new IP for %s: %s' % (iface, ip_addr)
        )
    


if __name__ == '__main__':
    state_change("State:/Network/Global/IPv4")