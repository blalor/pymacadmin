# encoding: utf-8

from subprocess import call

from SystemConfiguration import \
    SCDynamicStoreCreate, \
    SCDynamicStoreCopyValue

STORE = SCDynamicStoreCreate(None, "power_notifier", None , None)

PREV_BATT_STATE = {
    'BatteryHealth' : None,
    'Current Capacity' : None,
    'Is Charging' : None,
    'Power Source State' : None,
    'Time to Empty' : None,
    'Time to Full Charge' : None,
}

def logger(msg):
    logger_args = [
        'logger',
        '-t', 'crankd.power',
        msg,
    ]
    
    call(logger_args)


def battery_change(key=None, **kwargs):
    sc_value = SCDynamicStoreCopyValue(STORE, key)
    
    if PREV_BATT_STATE['BatteryHealth'] != sc_value['BatteryHealth']:
        logger("Battery health changed; now: %s" % (sc_value['BatteryHealth'],))
    
    if (PREV_BATT_STATE['Current Capacity'] != sc_value['Current Capacity']) and ("Is Charged" not in sc_value):
        if sc_value['Is Charging'] == 0:
            logger("Battery capacity now %(Current Capacity)d%%; time to empty: %(Time to Empty)s" % sc_value)
        else:
            logger("Battery capacity now %(Current Capacity)d%%; time to full: %(Time to Full Charge)s" % sc_value)
    
    if PREV_BATT_STATE['Power Source State'] != sc_value['Power Source State']:
        logger("Power source is now %s" % (sc_value['Power Source State'],))
    
    for k in PREV_BATT_STATE:
        PREV_BATT_STATE[k] = sc_value[k]



if __name__ == '__main__':
    battery_change('State:/IOKit/PowerSources/InternalBattery-0')
