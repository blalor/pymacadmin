# encoding: utf-8

import subprocess

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue, \
    SCNetworkSetSetCurrent, \
    SCNetworkSetGetName, \
    SCNetworkSetSetCurrent, \
    SCNetworkSetGetName, \
    SCNetworkSetCopyAll, \
    SCNetworkSetCopyCurrent, \
    SCPreferencesCreate

import logging
logger = logging.getLogger(__name__)

import Growl

PREV_BATT_STATE = {
    'BatteryHealth' : None,
    'Current Capacity' : None,
    'Is Charging' : None,
    'Power Source State' : None,
    'Time to Empty' : None,
    'Time to Full Charge' : None,
}

IDENT_PREFIX = 'crankd.notifier.power'

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
    


def battery_change(key=None, **kwargs):
    sc_value = SCDynamicStoreCopyValue(STORE, key)
    
    if PREV_BATT_STATE['BatteryHealth'] != sc_value['BatteryHealth']:
        logger.info("Battery health changed; now: %s" % (sc_value['BatteryHealth'],))
    
    if (PREV_BATT_STATE['Current Capacity'] != sc_value['Current Capacity']) and ("Is Charged" not in sc_value):
        if sc_value['Is Charging'] == 0:
            msg = "Battery capacity now %(Current Capacity)d%%; time to empty: %(Time to Empty)s" % sc_value
            
            logger.info(msg)
            
            if sc_value['Current Capacity'] <= 5:
                growl('Battery low!', msg, sticky=True)
            elif sc_value['Current Capacity'] <= 15:
                growl('Battery low!', msg)
            
        else:
            logger.info("Battery capacity now %(Current Capacity)d%%; time to full: %(Time to Full Charge)s" % sc_value)
    
    if PREV_BATT_STATE['Power Source State'] != sc_value['Power Source State']:
        logger.warn("Power source is now %s" % (sc_value['Power Source State'],))
    
    for k in PREV_BATT_STATE:
        PREV_BATT_STATE[k] = sc_value[k]


class NetworkSetSwitcher(object):
    # {{{ __init__
    def __init__(self):
        super(NetworkSetSwitcher, self).__init__()
        
        self.__prefs = SCPreferencesCreate(None, "power_notifier", None)
        self.__offlineSet = None
        self.__lastNetworkSet = None
    
    # }}}
    
    # {{{ take_network_offline
    def take_network_offline(self, **kwargs):
        self.__lastNetworkSet = None
        self.__offlineSet = None
        
        currentNetworkSet = SCNetworkSetCopyCurrent(self.__prefs)
        
        for netSet in SCNetworkSetCopyAll(self.__prefs):
            if SCNetworkSetGetName(netSet) == u'Offline':
                self.__offlineSet = netSet
                break
            
        
        if not self.__offlineSet:
            logger.error("could not find offline set")
        else:
            if currentNetworkSet == self.__offlineSet:
                logger.warn("Offline network set is already active")
            else:
                try:
                    subprocess.check_call(("scselect", "Offline"))
                    
                    logger.info("Changed to Offline network set")
                    
                    self.__lastNetworkSet = currentNetworkSet
                except subprocess.CalledProcessError:
                    logger.error("could not switch to offline set", exc_info=True)
                
            
        
    # }}}
    
    # {{{ reactivate_network
    def reactivate_network(self, **kwargs):
        if self.__lastNetworkSet != None:
            logger.debug("reactivating network")
            
            networkSetName = SCNetworkSetGetName(self.__lastNetworkSet)
            
            try:
                subprocess.check_call(("scselect", networkSetName))
                
                logger.info("Changed to %s network set", networkSetName)
            except subprocess.CalledProcessError:
                logger.error("Could not switch to %s set", networkSetName, exc_info=True)
            
        else:
            logger.error("last network set is unknown")
        
        self.__lastNetworkSet = None
    
    # }}}
    


## globals and module startup stuff below

GROWLER = Growl.GrowlNotifier(
    applicationName='crankd.power_notifier',
    notifications=["state_change"],
    applicationIcon=Growl.Image.imageFromPath("/System/Library/PreferencePanes/EnergySaver.prefPane/Contents/Resources/EnergySaver.icns"),

)
GROWLER.register()

STORE = SCDynamicStoreCreate(None, "power_notifier", None , None)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    battery_change('State:/IOKit/PowerSources/InternalBattery-0')
