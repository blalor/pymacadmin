# encoding: utf-8

from subprocess import call

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue

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
