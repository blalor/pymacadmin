#!/usr/bin/env python
# encoding: utf-8

"""
Usage: %prog

Monitor system event notifications

Configuration:

The configuration file is divided into sections for each class of
events. Each section is a dictionary using the event condition as the
key ("NSWorkspaceDidWakeNotification", "State:/Network/Global/IPv4",
etc). Each event must have one of the following properties:

command:      a shell command
function:     the name of a python function
class:        the name of a python class which will be instantiated once
              and have methods called as events occur.
method:       (class, method) tuple
process:      ??
"""

from Cocoa import \
    CFAbsoluteTimeGetCurrent, \
    CFRunLoopAddSource, \
    CFRunLoopAddTimer, \
    CFRunLoopTimerCreate, \
    NSNetServiceBrowser, \
    NSObject, \
    NSRunLoop, \
    NSWorkspace, \
    kCFRunLoopCommonModes, \
    NSDistributedNotificationCenter

from SystemConfiguration import \
    SCDynamicStoreCopyKeyList, \
    SCDynamicStoreCreate, \
    SCDynamicStoreCreateRunLoopSource, \
    SCDynamicStoreSetNotificationKeys

from FSEvents import \
    FSEventStreamCreate, \
    FSEventStreamStart, \
    FSEventStreamScheduleWithRunLoop, \
    kFSEventStreamEventIdSinceNow, \
    kCFRunLoopDefaultMode, \
    kFSEventStreamEventFlagMustScanSubDirs, \
    kFSEventStreamEventFlagUserDropped, \
    kFSEventStreamEventFlagKernelDropped

try:
    from CoreLocation import CLLocationManager, kCLLocationAccuracyBest
except ImportError, e:
    pass

import os
import os.path
import logging
import logging.handlers
import sys
import re
from subprocess import call
from optparse import OptionParser
from plistlib import readPlist, writePlist
from PyObjCTools import AppHelper
from functools import partial
import signal
from datetime import datetime


VERSION          = '$Revision: #4 $'

HANDLER_OBJECTS      = dict()     # Events which have a "class" handler use an instantiated object; we want to load only one copy
EXPLICIT_SC_HANDLERS = dict()     # Callbacks indexed by explicit SystemConfiguration keys
REGEXP_SC_HANDLERS   = dict()     # Callbacks indexed by regexp SystemConfiguration keys
FS_WATCHED_FILES     = dict()     # Callbacks indexed by filesystem path
MDNS_BROWSERS        = dict()
CL_HANDLERS          = []
DISTRIBUTED_IDS      = dict()
RELAUNCH_IDS         = dict()

class BaseHandler(object):
    # pylint: disable-msg=C0111,R0903
    pass


class NSNotificationHandler(NSObject):
    """Simple base class for handling NSNotification events"""
    # Method names and class structure are dictated by Cocoa & PyObjC, which
    # is substantially different from PEP-8:
    # pylint: disable-msg=C0103,W0232,R0903
    
    def init(self):
        """NSObject-compatible initializer"""
        self = super(NSNotificationHandler, self).init()
        if self is None: return None
        self.callable = self.not_implemented
        return self # NOTE: Unlike Python, NSObject's init() must return self!
    
    def not_implemented(self, *args, **kwargs):
        """A dummy function which exists only to catch configuration errors"""
        # TODO: Is there a better way to report the caller's location?
        import inspect
        stack = inspect.stack()
        my_name = stack[0][3]
        caller  = stack[1][3]
        
        raise NotImplementedError(
            "%s should have been overridden. Called by %s as: %s(%s)" % (
                my_name,
                caller,
                my_name,
                ", ".join(map(repr, args) + [ "%s=%s" % (k, repr(v)) for k,v in kwargs.items() ])
            )
        )
    
    
    def onNotification_(self, event):
        """Pass an NSNotifications to our handler"""
        user_info = None
        
        if event.userInfo:
            user_info = event.userInfo()
        
        self.callable(event = event, user_info = user_info) # pylint: disable-msg=E1101
    


class MDNSBrowser(NSObject):
    def init(self):
        self = super(MDNSBrowser, self).init()
        if self is None: return None
        self.services = set()
        self.callable = None
        return self
    
    
    def search(self, type_):
        self.type = type_
        b = self.browser = NSNetServiceBrowser.new()
        b.setDelegate_(self)
        b.searchForServicesOfType_inDomain_(type_, '')
    
    
    def netServiceBrowser_didFindService_moreComing_(self, browser, service, morecoming):
        self.services.add(service)
        service.setDelegate_(self)
        service.resolveWithTimeout_(5)
    
    
    def netServiceBrowser_didRemoveService_moreComing_(self, browser, service, morecoming):
        pass    
    
    
    def netServiceDidResolveAddress_(self, service):
        self.notify(service, True)
    
    
    def netService_didNotResolve_(self, service, errinfo):
        self.notify(service, False)
    
    
    def notify(self, service, resolved):
        if self.callable:
            self.callable(service_info={
                'name': service.name(),
                'type': service.type(),
                'port': service.port(),
                'hostName': service.hostName(),
                'domain': service.domain(),
                'addresses': service.addresses(),
                'resolved': resolved,
                'TXTRecordData': service.TXTRecordData(),
            })
        
    


class LocationDelegate(NSObject):
    def init(self):
        self = super(LocationDelegate, self).init()
        if self is None:
                return None
        
        self.callable = None
        return self
    
    
    def start_manager(self):
        lm = self.manager = CLLocationManager.new()
        lm.setDelegate_(self)
        lm.setDesiredAccuracy_(kCLLocationAccuracyBest)
        lm.startUpdatingLocation()
    
    
    def locationManager_didUpdateToLocation_fromLocation_(self, manager, location, oldloc):
        c = location.coordinate()
        lat, lon = c.latitude, c.longitude
        haccuracy = location.horizontalAccuracy()
        
        if oldloc and lat == oldloc.coordinate().latitude and \
           lon == oldloc.coordinate().longitude and \
           haccuracy == oldloc.horizontalAccuracy():
            return
        
        if self.callable:
            self.callable(location_info={
                'latitude': lat,
                'longitude': lon,
                'horizontalAccuracy': haccuracy,
            })
        
    
    
    def locationManager_didFailWithError_(self, manager, error):
        pass
    


def log_list(msg, items, level=logging.INFO):
    """
    Record a a list of values with a message
    
    This would ordinarily be a simple logging call but we want to keep the
    length below the 1024-byte syslog() limitation and we'll format things
    nicely by repeating our message with as many of the values as will fit.
    
    Individual items longer than the maximum length will be truncated.
    """
    
    max_len    = 1024 - len(msg % "")
    cur_len    = 0
    cur_items  = list()
    
    while [ i[:max_len] for i in items]:
        i = items.pop()
        if cur_len + len(i) + 2 > max_len:
            logging.info(msg % ", ".join(cur_items))
            cur_len = 0
            cur_items = list()
        
        cur_items.append(i)
        cur_len += len(i) + 2
    
    logging.log(level, msg % ", ".join(cur_items))


def get_callable_for_event(name, event_config, context=None):
    """
        Returns a callable object which can be used as a callback for any
        event. The returned function has context information, logging, etc.
        included so they do not need to be passed when the actual event
        occurs.
        
        NOTE: This function does not process "class" handlers - by design they
        are passed to the system libraries which expect a delegate object with
        various event handling methods
    """
    
    kwargs = {
        'context':  context,
        'key':      name,
        'config':   event_config,
    }
    
    if "command" in event_config:
        f = partial(do_shell, event_config["command"], **kwargs)
    elif "function" in event_config:
        f = partial(get_callable_from_string(event_config["function"]), **kwargs)
    elif "method" in event_config:
        f = partial(getattr(get_handler_object(event_config['method'][0]), event_config['method'][1]), **kwargs)
    elif "process" in event_config:
        f = partial(do_relaunch, event_config, **kwargs)
    else:
        raise AttributeError("%s have a class, method, function or command" % name)
    
    return f


def get_mod_func(callback):
    """Convert a fully-qualified module.function name to (module, function) - stolen from Django"""
    try:
        dot = callback.rindex('.')
    except ValueError:
        return (callback, '')
    return (callback[:dot], callback[dot+1:])


def get_callable_from_string(f_name):
    """Takes a string containing a function name (optionally module qualified) and returns a callable object"""
    try:
        mod_name, func_name = get_mod_func(f_name)
        if mod_name == "" and func_name == "":
            raise AttributeError("%s couldn't be converted to a module or function name" % f_name)
        
        module = __import__(mod_name)
        
        if func_name == "":
            func_name = mod_name # The common case is an eponymous class
        
        return getattr(module, func_name)
    
    except (ImportError, AttributeError), exc:
        raise RuntimeError("Unable to create a callable object for '%s': %s" % (f_name, exc))


def get_handler_object(class_name):
    """Return a single instance of the given class name, instantiating it if necessary"""
    
    if class_name not in HANDLER_OBJECTS:
        h_obj = get_callable_from_string(class_name)()
        if isinstance(h_obj, BaseHandler):
            pass # TODO: Do we even need BaseHandler any more?
        HANDLER_OBJECTS[class_name] = h_obj
    
    return HANDLER_OBJECTS[class_name]


def handle_sc_event(store, changed_keys, info):
    """Fire every event handler for one or more events"""
    
    for key in changed_keys:
        found_handler = False
        
        if key in EXPLICIT_SC_HANDLERS:
            EXPLICIT_SC_HANDLERS[key](key=key, info=info)
            found_handler = True
        else:
            for re_key in REGEXP_SC_HANDLERS:
                if re_key.match(key):
                    REGEXP_SC_HANDLERS[re_key](key=key, info=info, re_obj=re_key)
                    found_handler = True
        
        if not found_handler:
            logging.error("dropped SC event; no handler for %s" % (key,))


def list_events(option, opt_str, value, parser):
    """Displays the list of events which can be monitored on the current system"""
    
    print 'On this system SystemConfiguration supports these events:'
    for event in sorted(SCDynamicStoreCopyKeyList(get_sc_store(), '.*')):
        print "\t", event
    
    print
    print "Standard NSWorkspace Notification messages:\n\t",
    print "\n\t".join('''
        NSWorkspaceDidLaunchApplicationNotification
        NSWorkspaceDidMountNotification
        NSWorkspaceDidPerformFileOperationNotification
        NSWorkspaceDidTerminateApplicationNotification
        NSWorkspaceDidUnmountNotification
        NSWorkspaceDidWakeNotification
        NSWorkspaceSessionDidBecomeActiveNotification
        NSWorkspaceSessionDidResignActiveNotification
        NSWorkspaceWillLaunchApplicationNotification
        NSWorkspaceWillPowerOffNotification
        NSWorkspaceWillSleepNotification
        NSWorkspaceWillUnmountNotification
    '''.split())
    
    print "Any NSDistributedNotification message.\n"
    
    sys.exit(0)


def process_commandline():
    """
        Process command-line options
        Load our preference file
        Configure the module path to add Application Support directories
    """
    parser          = OptionParser(__doc__.strip())
    support_path    = '/Library/' if os.getuid() == 0 else os.path.expanduser('~/Library/')
    preference_file = os.path.join(support_path, 'Preferences', 'com.googlecode.pymacadmin.crankd.plist')
    module_path     = os.path.join(support_path, 'Application Support/crankd')
    
    if os.path.exists(module_path):
        sys.path.append(module_path)
    else:
        print >> sys.stderr, "Module directory %s does not exist: Python handlers will need to use absolute pathnames" % module_path
    
    parser.add_option("-f", "--config", dest="config_file", help='Use an alternate config file instead of %default', default=preference_file)
    parser.add_option("-l", "--list-events", action="callback", callback=list_events, help="List the events which can be monitored")
    parser.add_option("-d", "--debug", action="count", default=False, help="Log detailed progress information")
    (options, args) = parser.parse_args()
    
    if len(args):
        parser.error("Unknown command-line arguments: %s" % args)
    
    options.support_path = support_path
    options.config_file = os.path.realpath(options.config_file)
    
    # This is somewhat messy but we want to alter the command-line to use full
    # file paths in case someone's code changes the current directory or the
    sys.argv = [ os.path.realpath(sys.argv[0]), ]
    
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        sys.argv.append("--debug")
    
    if options.config_file:
        sys.argv.append("--config")
        sys.argv.append(options.config_file)
    
    return options


def load_config(options):
    """Load our configuration from plist or create a default file if none exists"""
    if not os.path.exists(options.config_file):
        logging.info("%s does not exist - initializing with an example configuration" % CRANKD_OPTIONS.config_file)
        print >>sys.stderr, 'Creating %s with default options for you to customize' % options.config_file
        print >>sys.stderr, '%s --list-events will list the events you can monitor on this system' % sys.argv[0]
        example_config = {
            'SystemConfiguration': {
                'State:/Network/Global/IPv4': {
                    'command': '/bin/echo "Global IPv4 config changed"'
                },
                'regexp:State:/Network/Interface/([^/]+)/Link': {
                    'command': '/bin/echo "Network interface link state changed"'
                },
            },
            'NSWorkspace': {
                'NSWorkspaceDidMountNotification': {
                    'command': '/bin/echo "A new volume was mounted!"'
                },
                'NSWorkspaceDidWakeNotification': {
                    'command': '/bin/echo "The system woke from sleep!"'
                },
                'NSWorkspaceWillSleepNotification': {
                    'command': '/bin/echo "The system is about to go to sleep!"'
                }
            },
            'NSNetService': {
                '_ssh._tcp.': {
                    'command': '/bin/echo "new ssh server seen!"'
                }
            }
        }
        writePlist(example_config, options.config_file)
        sys.exit(1)
    
    logging.info("Loading configuration from %s" % CRANKD_OPTIONS.config_file)
    
    plist = readPlist(options.config_file)
    
    if "imports" in plist:
        for module in plist['imports']:
            try:
                __import__(module)
            except ImportError, exc:
                print >> sys.stderr, "Unable to import %s: %s" % (module, exc)
                sys.exit(1)
    return plist


def configure_logging():
    """Configures the logging module"""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    root_logger = logging.getLogger()
    root_logger.name = "crankd"
    root_logger.handlers[0].setLevel(logging.ERROR)
    
    # Enable logging to syslog as well:
    # Normally this would not be necessary but logging assumes syslog listens on
    # localhost syslog/udp, which is disabled on 10.5 (rdar://5871746)
    syslog = logging.handlers.SysLogHandler('/var/run/syslog')
    syslog.setFormatter(logging.Formatter('%(name)s: %(message)s'))
    syslog.setLevel(logging.INFO)
    logging.getLogger().addHandler(syslog)


def get_sc_store():
    """Returns an SCDynamicStore instance"""
    return SCDynamicStoreCreate(None, "crankd", handle_sc_event, None)


def add_workspace_notification(event, event_config, center):
    if "class" in event_config:
        obj         = get_handler_object(event_config['class'])
        objc_method = "on%s:" % event
        py_method   = objc_method.replace(":", "_")
        
        if not hasattr(obj, py_method) or not callable(getattr(obj, py_method)):
            print  >> sys.stderr, "NSWorkspace Notification %s: handler class %s must define a %s method" % (event, event_config['class'], py_method)
            sys.exit(1)
        
        notification_center.addObserver_selector_name_object_(obj, objc_method, event, None)
    else:
        handler          = NSNotificationHandler.new()
        handler.name     = "NSWorkspace Notification %s" % event
        handler.callable = get_callable_for_event(event, event_config, context=handler.name)
        
        assert(callable(handler.onNotification_))
        
        center.addObserver_selector_name_object_(handler, "onNotification:", event, None)


def add_workspace_notifications(nsw_config):
    # See http://developer.apple.com/documentation/Cocoa/Conceptual/Workspace/Workspace.html
    notification_center = NSWorkspace.sharedWorkspace().notificationCenter()
    
    for event in nsw_config:
        event_config = nsw_config[event]
        add_workspace_notification(event, event_config, notification_center)
    
    log_list("Listening for these NSWorkspace notifications: %s", nsw_config.keys())


def add_distributed_notification(event, event_config, dist_center):
    if "class" in event_config:
        obj         = get_handler_object(event_config['class'])
        objc_method = "on%s:" % event
        py_method   = objc_method.replace(":", "_")
        
        if not hasattr(obj, py_method) or not callable(getattr(obj, py_method)):
            print >> sys.stderr, \
                "NSDistributedNotification %s: handler class %s must define a %s method" % (event, event_config['class'], py_method)
            sys.exit(1)
            
        dist_center.addObserver_selector_name_object_(obj, objc_method, event, None)
    else:
        shouldrm = False
        process_event = {}
        if not event in DISTRIBUTED_IDS:
            DISTRIBUTED_IDS[event] = event_config
            handler = NSNotificationHandler.new()
            handler.name = "NSDistributed %s" % event
            
            if "process" in event_config:
                notification_center = NSWorkspace.sharedWorkspace().notificationCenter()
                process_event["process"] = event_config["process"]
                process_event["event"] = event
                process_event["event_config"] = event_config
                
                logging.info("Adding Process Monitor: %s" % process_event["process"])
                
                if not event in RELAUNCH_IDS:
                    add_workspace_notification("NSWorkspaceDidLaunchApplicationNotification", process_event, notification_center)
                    RELAUNCH_IDS[event] = process_event
                else:
                    shouldrm = True
            
            handler.callable = get_callable_for_event(event, event_config, context=handler.name)
            
            assert(callable(handler.onNotification_))
            
            event_name = event
            if event == '*':
                event_name = None
            
            if not shouldrm:
                dist_center.addObserver_selector_name_object_(handler, "onNotification:", event_name, None)
            else:
                del DISTRIBUTED_IDS[event]
                dist_center.removeObserver_name_object_(handler, "onNotification", event_name, None)


def add_distributed_notifications(nsd_config):
    dist_center = NSDistributedNotificationCenter.defaultCenter()
    for event in nsd_config:
        event_config = nsd_config[event]
        add_distributed_notification(event, event_config, dist_center)
    
    log_list("Listening for these NSDistributedNotifications: %s", nsd_config.keys())


def add_sc_notifications(sc_config):
    """
    This uses the SystemConfiguration framework to get a SCDynamicStore session
    and register for certain events. See the Apple SystemConfiguration
    documentation for details:
    
    <http://developer.apple.com/documentation/Networking/Reference/SysConfig/SCDynamicStore/CompositePage.html>
    
    TN1145 may also be of interest:
        <http://developer.apple.com/technotes/tn/tn1145.html>
    
    Inspired by the PyObjC SystemConfiguration callback demos:
    <https://svn.red-bean.com/pyobjc/trunk/pyobjc/pyobjc-framework-SystemConfiguration/Examples/CallbackDemo/>
    """
    
    keys = sc_config.keys()
    
    regexp_sc_keys = []
    try:
        for key in keys:
            handler = get_callable_for_event(key, sc_config[key], context="SystemConfiguration: %s" % key)
            
            if key.startswith("regexp:"):
                # strip "regexp:"
                re_key = key[7:]
                REGEXP_SC_HANDLERS[re.compile(re_key)] = handler
                regexp_sc_keys.append(re_key)
            else:
                EXPLICIT_SC_HANDLERS[key] = handler
            
    except AttributeError, exc:
        print  >> sys.stderr, "Error configuring SystemConfiguration events: %s" % exc
        sys.exit(1)
    
    store = get_sc_store()
    SCDynamicStoreSetNotificationKeys(store, EXPLICIT_SC_HANDLERS.keys(), regexp_sc_keys)
    
    # Get a CFRunLoopSource for our store session and add it to the application's runloop:
    CFRunLoopAddSource(
        NSRunLoop.currentRunLoop().getCFRunLoop(),
        SCDynamicStoreCreateRunLoopSource(None, store, 0),
        kCFRunLoopCommonModes
    )
    
    log_list("Listening for these SystemConfiguration events: %s", keys)


def add_mdns_notifications(mdns_config):
    for type_ in mdns_config:
        browser = MDNSBrowser.new()
        browser.callable = get_callable_for_event(type_, mdns_config[type_], context="NSNetServiceBrowser type: %s" % type_)
        browser.search(type_)
        MDNS_BROWSERS[type_] = browser


def add_cl_notifications(cl_config):
    for conf in cl_config:
        manager = LocationDelegate.new()
        manager.callable = get_callable_for_event(conf, cl_config[conf], context="CLCoreLocation")
        manager.start_manager()
        CL_HANDLERS.append(manager)


def add_fs_notifications(fs_config):
    for path in fs_config:
        add_fs_notification(path, get_callable_for_event(path, fs_config[path], context="FSEvent: %s" % path))


def add_fs_notification(f_path, callback):
    """Adds an FSEvent notification for the specified path"""
    path = os.path.realpath(os.path.expanduser(f_path))
    if not os.path.exists(path):
        raise AttributeError("Cannot add an FSEvent notification: %s does not exist!" % path)
    
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    
    try:
        FS_WATCHED_FILES[path].append(callback)
    except KeyError:
        FS_WATCHED_FILES[path] = [callback]


def start_fs_events():
    stream_ref = FSEventStreamCreate(
        None,                               # Use the default CFAllocator
        fsevent_callback,
        None,                               # We don't need a FSEventStreamContext
        FS_WATCHED_FILES.keys(),
        kFSEventStreamEventIdSinceNow,      # We only want events which happen in the future
        1.0,                                # Process events within 1 second
        0                                   # We don't need any special flags for our stream
    )
    
    if not stream_ref:
        raise RuntimeError("FSEventStreamCreate() failed!")
    
    FSEventStreamScheduleWithRunLoop(stream_ref, NSRunLoop.currentRunLoop().getCFRunLoop(), kCFRunLoopDefaultMode)
    
    if not FSEventStreamStart(stream_ref):
        raise RuntimeError("Unable to start FSEvent stream!")
    
    logging.debug("FSEventStream started for %d paths: %s" % (len(FS_WATCHED_FILES), ", ".join(FS_WATCHED_FILES)))


def fsevent_callback(stream_ref, full_path, event_count, paths, masks, ids):
    """Process an FSEvent (consult the Cocoa docs) and call each of our handlers which monitors that path or a parent"""
    for i in range(event_count):
        path = os.path.dirname(paths[i])
        
        if masks[i] & kFSEventStreamEventFlagMustScanSubDirs:
            recursive = True
        
        if masks[i] & kFSEventStreamEventFlagUserDropped:
            logging.error("We were too slow processing FSEvents and some events were dropped")
            recursive = True
        
        if masks[i] & kFSEventStreamEventFlagKernelDropped:
            logging.error("The kernel was too slow processing FSEvents and some events were dropped!")
            recursive = True
        else:
            recursive = False
        
        for i in [k for k in FS_WATCHED_FILES if path.startswith(k)]:
            logging.debug("FSEvent: %s: processing %d callback(s) for path %s" % (i, len(FS_WATCHED_FILES[i]), path))
            for j in FS_WATCHED_FILES[i]:
                j(i, path=path, recursive=recursive)
            


def timer_callback(*args):
    """Handles the timer events which we use simply to have the runloop run regularly. Currently this logs a timestamp for debugging purposes"""
    logging.debug("timer callback at %s" % datetime.now())


def main():
    configure_logging()
    
    global CRANKD_OPTIONS, CRANKD_CONFIG
    
    CRANKD_OPTIONS = process_commandline()
    CRANKD_CONFIG  = load_config(CRANKD_OPTIONS)
    
    if "NSDistributed" in CRANKD_CONFIG:
        add_distributed_notifications(CRANKD_CONFIG["NSDistributed"])
    
    if "NSWorkspace" in CRANKD_CONFIG:
        add_workspace_notifications(CRANKD_CONFIG['NSWorkspace'])
    
    if "SystemConfiguration" in CRANKD_CONFIG:
        add_sc_notifications(CRANKD_CONFIG['SystemConfiguration'])
    
    if "FSEvents" in CRANKD_CONFIG:
        add_fs_notifications(CRANKD_CONFIG['FSEvents'])
    
    if "NSNetService" in CRANKD_CONFIG:
        add_mdns_notifications(CRANKD_CONFIG['NSNetService'])
    
    if "CLLocation" in CRANKD_CONFIG:
        add_cl_notifications(CRANKD_CONFIG['CLLocation'])
    
    # We reuse our FSEvents code to watch for changes to our files and
    # restart if any of our libraries have been updated:
    add_conditional_restart(CRANKD_OPTIONS.config_file, "Configuration file %s changed" % CRANKD_OPTIONS.config_file)
    for m in filter(lambda i: i and hasattr(i, '__file__'), sys.modules.values()):
        if m.__name__ == "__main__":
            msg = "%s was updated" % m.__file__
        else:
            msg = "Module %s was updated" % m.__name__
        
        add_conditional_restart(m.__file__, msg)
    
    signal.signal(signal.SIGHUP, partial(restart, "SIGHUP received"))
    
    start_fs_events()
    
    # NOTE: This timer is basically a kludge around the fact that we can't reliably get
    #       signals or Control-C inside a runloop. This wakes us up often enough to
    #       appear tolerably responsive:
    CFRunLoopAddTimer(
        NSRunLoop.currentRunLoop().getCFRunLoop(),
        CFRunLoopTimerCreate(None, CFAbsoluteTimeGetCurrent(), 2.0, 0, 0, timer_callback, None),
        kCFRunLoopCommonModes
    )
    
    try:
        AppHelper.runConsoleEventLoop(installInterrupt=True)
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received, exiting")
    
    sys.exit(0)


def create_env_name(name):
    """
    Converts input names into more traditional shell environment name style
    
    >>> create_env_name("NSApplicationBundleIdentifier")
    'NSAPPLICATION_BUNDLE_IDENTIFIER'
    >>> create_env_name("NSApplicationBundleIdentifier-1234$foobar!")
    'NSAPPLICATION_BUNDLE_IDENTIFIER_1234_FOOBAR'
    """
    new_name = re.sub(r'''(?<=[a-z])([A-Z])''', '_\\1', name)
    new_name = re.sub(r'\W+', '_', new_name)
    new_name = re.sub(r'_{2,}', '_', new_name)
    return new_name.upper().strip("_")


# distnot patch: handles the reloading of the event in question, this will be called on *every*
#        ApplicationLaunch event but only reloads events if it has a 'process' config option
def do_relaunch(command, context=None, **kwargs):
    # command=process name
    # kwargs["config"]["event_data"]=original event
    # kwargs["config"]["event_config"]=original event_config
    event=kwargs["config"]["event"]
    event_config=kwargs["config"]["event_config"]
    if 'user_info' in kwargs:
        if 'NSApplicationName' in kwargs['user_info']:
            if kwargs['user_info']['NSApplicationName'] == event_config["process"]:
                logging.info("%s: reloading handler %s" % (context, event))
                dist_center = NSDistributedNotificationCenter.defaultCenter()
                add_distributed_notification(event, event_config, dist_center)


def do_shell(command, context=None, **kwargs):
    """Executes a shell command with logging"""
    logging.info("%s: executing %s" % (context, command))
    
    child_env = {'CRANKD_CONTEXT': context}
    
    # We'll pull a subset of the available information in for shell scripts.
    # Anyone who needs more will probably want to write a Python handler
    # instead so they can reuse things like our logger & config info and avoid
    # ordeals like associative arrays in Bash
    for k in [ 'info', 'key' ]:
        if k in kwargs and kwargs[k]:
            child_env['CRANKD_%s' % k.upper()] = str(kwargs[k])
    
    if 'user_info' in kwargs:
        for k, v in kwargs['user_info'].items():
            child_env[create_env_name(k)] = str(v)
    
    try:
        rc = call(command, shell=True, env=child_env)
        if rc == 0:
            logging.debug("`%s` returned %d" % (command, rc))
        elif rc < 0:
            logging.error("`%s` was terminated by signal %d" % (command, -rc))
        else:
            logging.error("`%s` returned %d" % (command, rc))
    except OSError, exc:
        logging.error("Got an exception when executing %s:" % (command, exc))


def add_conditional_restart(file_name, reason):
    """
    FSEvents monitors directories, not files. This function uses stat to
    restart only if the file's mtime has changed
    """
    file_name = os.path.realpath(file_name)
    while not os.path.exists(file_name):
        file_name = os.path.dirname(file_name)
    orig_stat = os.stat(file_name).st_mtime
    
    def cond_restart(*args, **kwargs):
        try:
            if os.stat(file_name).st_mtime != orig_stat:
                restart(reason)
        except (OSError, IOError, RuntimeError), exc:
            restart("Exception while checking %s: %s" % (file_name, exc))
    
    add_fs_notification(file_name, cond_restart)


def restart(reason, *args, **kwargs):
    """Perform a complete restart of the current process using exec()"""
    logging.info("Restarting: %s" % reason)
    os.execv(sys.argv[0], sys.argv)

if __name__ == '__main__':
    main()
