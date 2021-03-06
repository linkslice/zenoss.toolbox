##############################################################################
#
# Copyright (C) Zenoss, Inc. 2016, all rights reserved.
#
# This content is made available according to terms specified in
# License.zenoss under the directory where your Zenoss product is installed.
#
##############################################################################
#!/opt/zenoss/bin/python

scriptVersion = "2.0.0"
scriptSummary = " - scans catalogs for broken references - WARNING: Before using with --fix " \
                "you MUST confirm zodbscan, findposkeyerror, and zenrelationscan return " \
                "no errors. "
documentationURL = "https://support.zenoss.com/hc/en-us/articles/203118075"
maxCycles = 12


import argparse
import datetime
import Globals
import logging
import os
import sys
import time
import traceback
import transaction
import ZenToolboxUtils

from Products.ZenUtils.ZenScriptBase import ZenScriptBase
from ZenToolboxUtils import inline_print
from ZODB.transact import transact


class CatalogScanInfo(object):
    def __init__(self, name, actualPath):
        self.prettyName = name
        self.dmdPath = actualPath
        self.initialSize = 0
        self.runResults = {}            # Dict to hold int(cycle): { ZenToolboxUtils.Counters }


def scan_progress_message(done, fix, cycle, catalog, issues, chunk, log):
    '''Handle output to screen and logfile, remove output from scan_catalog logic'''
    # Logic for log file output messages based on done, issues
    if not done:
        log.debug("Scan of %s catalog is %2d%% complete" % (catalog, 2*chunk))
    else:
        if issues > 0:
            log.warning("Scanned %s - found %d issue(s)" % (catalog, issues))
        else:
            log.info("No issues found scanning: %s" % (catalog))
        log.debug("Scan of %s catalog is complete" % (catalog))
    # Logic for screen output messages based on done, issues, and fix
    if issues > 0:
        if fix:
            if not done:
                inline_print("[%s]  Cleaning  [%-50s] %3d%% [%d Issues Detected]" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), '='*chunk, 2*chunk, issues))
            else:
                inline_print("[%s]  Clean #%2.0d [%-50s] %3.0d%% [%d Issues Detected]\n" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), cycle, '='*50, 100, issues))
        else:
            if not done:
                inline_print("[%s]  Scanning  [%-50s] %3d%% [%d Issues Detected]" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), '='*chunk, 2*chunk, issues))
            else:
                inline_print("[%s]  WARNING   [%-50s] %3.0d%% [%d Issues Detected]\n" %
                             (time.strftime("%Y-%m-%d %H:%M:%S"), '='*50, 100, issues))
    else:
        if not done:
            inline_print("[%s]  Scanning  [%-50s] %3d%% " %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), '='*chunk, 2*chunk))
        else:
            inline_print("[%s]  Verified  [%-50s] %3.0d%%\n" %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), '='*50, 100))


def global_catalog_paths_to_uids(catalogObject, fix, dmd, log, createEvents):
    """Scan through global_catalog verifying consistency of rids"""

    catalogReference = eval(catalogObject.dmdPath)._catalog
    catalogObject.initialSize = len(catalogReference.paths)

    if (catalogObject.initialSize > 50):
        progressBarChunkSize = (catalogObject.initialSize//50) + 1
    else:
        progressBarChunkSize = 1

    log.info("Examining global_catalog._catalog.paths for consistency against ._catalog.uids")
    print("[%s] Examining %-35s (%d Objects)" %
          (time.strftime("%Y-%m-%d %H:%M:%S"), "global_catalog 'paths to uids'", catalogObject.initialSize))

    currentCycle = 0

    while (currentCycle < maxCycles):
        currentCycle += 1
        catalogObject.runResults[currentCycle] = {'itemCount': ZenToolboxUtils.Counter(0),
                                                  'errorCount': ZenToolboxUtils.Counter(0),
                                                  'repairCount': ZenToolboxUtils.Counter(0)
                                                  }
        log.info("Beginning cycle %d for global_catalog 'paths to uids'" % (currentCycle))
        scan_progress_message(False, fix, currentCycle, "global_catalog 'paths to uids'", 0, 0, log)

        try:
            broken_rids = []
            for rid, path in catalogReference.paths.iteritems():
                catalogObject.runResults[currentCycle]['itemCount'].increment()
                if (catalogObject.runResults[currentCycle]['itemCount'].value() % progressBarChunkSize) == 0:
                    chunkNumber = catalogObject.runResults[currentCycle]['itemCount'].value() // progressBarChunkSize
                    scan_progress_message(False, fix, currentCycle, "global_catalog 'paths to uids'",
                                          catalogObject.runResults[currentCycle]['errorCount'].value(), chunkNumber, log)
                if path not in catalogReference.uids:
                    catalogObject.runResults[currentCycle]['errorCount'].increment()
                    broken_rids.append(rid)

        except Exception, e:
            log.exception(e)

        scan_progress_message(True, fix, currentCycle, "global_catalog 'paths to uids' consistency",
                              catalogObject.runResults[currentCycle]['errorCount'].value(), chunkNumber, log)

        if fix:
            if catalogObject.runResults[currentCycle]['errorCount'].value() > 0:
                log.info("Attempting to repair %d detected issues", len(broken_rids))
                for item in broken_rids:
                    try:
                        catalogObject.runResults[currentCycle]['repairCount'].increment()
                        catalogReference.paths.pop(item)
                        catalogReference.data.pop(item)
                        catalogReference._p_changed = True
                        transaction.commit()
                    except:
                        pass
            else:
                break
            if currentCycle > 1:
                if catalogObject.runResults[currentCycle]['errorCount'].value() == catalogObject.runResults[currentCycle-1]['errorCount'].value():
                    break
        # Final transaction.abort() to try and free up used memory
        log.debug("Calling transaction.abort() to minimize memory footprint")
        transaction.abort()

    if createEvents:
        scriptName = os.path.basename(__file__).split('.')[0]
        eventMsg = ""
        for cycleID in catalogObject.runResults.keys():
            eventMsg += "Cycle %d scanned %d items, found %d errors and attempted %d repairs\n" % \
                        (cycleID, catalogObject.runResults[cycleID]['itemCount'].value(),
                         catalogObject.runResults[cycleID]['errorCount'].value(),
                         catalogObject.runResults[cycleID]['repairCount'].value())
        if not catalogObject.runResults[currentCycle]['errorCount'].value():
            eventSeverity = 1
            if currentCycle == 1:
                eventSummaryMsg = "global_catalog 'paths to uids' - No Errors Detected (%d total items)" % \
                                   (catalogObject.initialSize)
            else:
                eventSummaryMsg = "global_catalog 'paths to uids' - No Errors Detected [--fix was successful] (%d total items)" % \
                                   (catalogObject.initialSize)
        else:
            eventSeverity = 4
            if fix:
                eventSummaryMsg = "global_catalog 'paths to uids' - %d Errors Remain after --fix [consult log file]  (%d total items)" % \
                                   (catalogObject.runResults[currentCycle]['errorCount'].value(), catalogObject.initialSize)
            else:
                eventSummaryMsg = "global_catalog 'paths to uids' - %d Errors Detected [run with --fix]  (%d total items)" % \
                                   (catalogObject.runResults[currentCycle]['errorCount'].value(), catalogObject.initialSize)

        log.debug("Creating event with %s, %s" % (eventSummaryMsg, eventSeverity))
        ZenToolboxUtils.send_summary_event(
            eventSummaryMsg, eventSeverity,
            scriptName, "global_catalog_paths_to_uids",
            documentationURL, dmd, eventMsg
        )

    return (catalogObject.runResults[currentCycle]['errorCount'].value() != 0)


def scan_catalog(catalogObject, fix, dmd, log, createEvents):
    """Scan through a catalog looking for broken references"""

    # Fix for ZEN-14717 (only for global_catalog)
    if (catalogObject.prettyName == 'global_catalog'):
        global_catalog_paths_to_uids(catalogObject, fix, dmd, log, createEvents)

    catalog = eval(catalogObject.dmdPath)
    catalogObject.initialSize = len(catalog)

    print("[%s] Examining %-35s (%d Objects)" %
          (time.strftime("%Y-%m-%d %H:%M:%S"), catalogObject.prettyName, catalogObject.initialSize))
    log.info("Examining %s catalog with %d objects" % (catalogObject.prettyName, catalogObject.initialSize))

    currentCycle = 0

    while (currentCycle < maxCycles):
        currentCycle += 1
        catalogObject.runResults[currentCycle] = {'itemCount': ZenToolboxUtils.Counter(0),
                                                  'errorCount': ZenToolboxUtils.Counter(0),
                                                  'repairCount': ZenToolboxUtils.Counter(0)
                                                  }
        log.info("Beginning cycle %d for catalog %s" % (currentCycle, catalogObject.prettyName))
        scan_progress_message(False, fix, currentCycle, catalogObject.prettyName, 0, 0, log)

        try:
            brains = eval(catalogObject.dmdPath)()
        except Exception:
            raise

        catalogSize = len(brains)
        if (catalogSize > 50):
            progressBarChunkSize = (catalogSize//50) + 1
        else:
            progressBarChunkSize = 1

        for brain in brains:
            catalogObject.runResults[currentCycle]['itemCount'].increment()
            if (catalogObject.runResults[currentCycle]['itemCount'].value() % progressBarChunkSize) == 0:
                chunkNumber = catalogObject.runResults[currentCycle]['itemCount'].value() // progressBarChunkSize
                scan_progress_message(False, fix, currentCycle, catalogObject.prettyName,
                                      catalogObject.runResults[currentCycle]['errorCount'].value(), chunkNumber, log)
            try:
                testReference = brain.getObject()
                testReference._p_deactivate()
            except Exception:
                catalogObject.runResults[currentCycle]['errorCount'].increment()
                objectPathString = brain.getPath()
                log.error("Catalog %s contains broken object %s" % (catalogObject.prettyName, objectPathString))
                if fix:
                    log.info("Attempting to uncatalog %s" % (objectPathString))
                    try:
                        catalogObject.runResults[currentCycle]['repairCount'].increment()
                        transact(catalog.uncatalog_object)(objectPathString)
                    except Exception as e:
                        log.exception(e)

        # Final transaction.abort() to try and free up used memory
        log.debug("Calling transaction.abort() to minimize memory footprint")
        transaction.abort()

        scan_progress_message(True, fix, currentCycle, catalogObject.prettyName,
                              catalogObject.runResults[currentCycle]['errorCount'].value(), chunkNumber, log)

        if fix:
            if catalogObject.runResults[currentCycle]['errorCount'].value() == 0:
                break
            if currentCycle > 1:
                if catalogObject.runResults[currentCycle]['errorCount'].value() == catalogObject.runResults[currentCycle-1]['errorCount'].value():
                    break

    if createEvents:
        scriptName = os.path.basename(__file__).split('.')[0]
        eventMsg = ""
        for cycleID in catalogObject.runResults.keys():
            eventMsg += "Cycle %d scanned %d items, found %d errors and attempted %d repairs\n" % \
                        (cycleID, catalogObject.runResults[cycleID]['itemCount'].value(),
                         catalogObject.runResults[cycleID]['errorCount'].value(),
                         catalogObject.runResults[cycleID]['repairCount'].value())
        if not catalogObject.runResults[currentCycle]['errorCount'].value():
            eventSeverity = 1
            if currentCycle == 1:
                eventSummaryMsg = "'%s' - No Errors Detected (%d total items)" % \
                                   (catalogObject.prettyName, catalogObject.initialSize)
            else:
                eventSummaryMsg = "'%s' - No Errors Detected [--fix was successful] (%d total items)" % \
                                   (catalogObject.prettyName, catalogObject.initialSize)
        else:
            eventSeverity = 4
            if fix:
                eventSummaryMsg = "'%s' - %d Errors Remain after --fix [consult log file]  (%d total items)" % \
                                   (catalogObject.prettyName, catalogObject.runResults[currentCycle]['errorCount'].value(), catalogObject.initialSize)
            else:
                eventSummaryMsg = "'%s' - %d Errors Detected [run with --fix]  (%d total items)" % \
                                   (catalogObject.prettyName, catalogObject.runResults[currentCycle]['errorCount'].value(), catalogObject.initialSize)

        log.debug("Creating event with %s, %s" % (eventSummaryMsg, eventSeverity))
        ZenToolboxUtils.send_summary_event(
            eventSummaryMsg, eventSeverity,
            scriptName, catalogObject.prettyName,
            documentationURL, dmd, eventMsg
        )

    return (catalogObject.runResults[currentCycle]['errorCount'].value() != 0)


def build_catalog_list(dmd, log):
    """Builds a list of catalogs that are (present and not empty)"""

    catalogsToCheck = [
        CatalogScanInfo('CiscoUCS.ucsSearchCatalog', 'dmd.Devices.CiscoUCS.ucsSearchCatalog'),
        CatalogScanInfo('CloudStack.HostCatalog', 'dmd.Devices.CloudStack.HostCatalog'),
        CatalogScanInfo('CloudStack.RouterVMCatalog', 'dmd.Devices.CloudStack.RouterVMCatalog'),
        CatalogScanInfo('CloudStack.SystemVMCatalog', 'dmd.Devices.CloudStack.SystemVMCatalog'),
        CatalogScanInfo('CloudStack.VirtualMachineCatalog', 'dmd.Devices.CloudStack.VirtualMachineCatalog'),
        CatalogScanInfo('Devices.deviceSearch', 'dmd.Devices.deviceSearch'),
        CatalogScanInfo('Devices.searchRRDTemplates', 'dmd.Devices.searchRRDTemplates'),
        CatalogScanInfo('Events.eventClassSearch', 'dmd.Events.eventClassSearch'),
        CatalogScanInfo('global_catalog', 'dmd.global_catalog'),
        CatalogScanInfo('HP.Proliant.deviceSearch', 'dmd.Devices.Server.HP.Proliant.deviceSearch'),
        CatalogScanInfo('IPv6Networks.ipSearch', 'dmd.IPv6Networks.ipSearch'),
        CatalogScanInfo('JobManager.job_catalog', 'dmd.JobManager.job_catalog'),
        CatalogScanInfo('Layer2.macs_catalog', 'dmd.Devices.macs_catalog'),
        CatalogScanInfo('maintenanceWindowSearch', 'dmd.maintenanceWindowSearch'),
        CatalogScanInfo('Manufacturers.productSearch', 'dmd.Manufacturers.productSearch'),
        CatalogScanInfo('Mibs.mibSearch', 'dmd.Mibs.mibSearch'),
        CatalogScanInfo('Networks.ipSearch', 'dmd.Networks.ipSearch'),
        CatalogScanInfo('Services.serviceSearch', 'dmd.Services.serviceSearch'),
        CatalogScanInfo('Storage.iqnCatalog', 'dmd.Devices.Storage.iqnCatalog'),
        CatalogScanInfo('Storage.wwnCatalog', 'dmd.Devices.Storage.wwnCatalog'),
        CatalogScanInfo('vCloud.vCloudVMSearch', 'dmd.Devices.vCloud.vCloudVMSearch'),
        CatalogScanInfo('VMware.vmwareGuestSearch', 'dmd.Devices.VMware.vmwareGuestSearch'),
        CatalogScanInfo('vSphere.lunCatalog', 'dmd.Devices.vSphere.lunCatalog'),
        CatalogScanInfo('vSphere.pnicCatalog', 'dmd.Devices.vSphere.pnicCatalog'),
        CatalogScanInfo('vSphere.vnicCatalog', 'dmd.Devices.vSphere.vnicCatalog'),
        CatalogScanInfo('XenServer.PIFCatalog', 'dmd.Devices.XenServer.PIFCatalog'),
        CatalogScanInfo('XenServer.VIFCatalog', 'dmd.Devices.XenServer.VIFCatalog'),
        CatalogScanInfo('XenServer.XenServerCatalog', 'dmd.Devices.XenServer.XenServerCatalog'),
        CatalogScanInfo('ZenLinkManager.layer2_catalog', 'dmd.ZenLinkManager.layer2_catalog'),
        CatalogScanInfo('ZenLinkManager.layer3_catalog', 'dmd.ZenLinkManager.layer3_catalog'),
        CatalogScanInfo('zenPackPersistence', 'dmd.zenPackPersistence')
    ]

    log.debug("Checking %d defined catalogs for (presence and not empty)" % (len(catalogsToCheck)))

    intermediateCatalogList = []

    for catalogObject in catalogsToCheck:
        try:
            tempBrains = eval(catalogObject.dmdPath)
            if len(tempBrains) > 0:
                log.debug("Catalog %s exists, has items - adding to list" % (catalogObject.prettyName))
                intermediateCatalogList.append(catalogObject)
            else:
                log.debug("Skipping catalog %s - exists but has no items" % (catalogObject.prettyName))
        except AttributeError:
            log.debug("Skipping catalog %s - catalog not found" % (catalogObject.prettyName))
        except Exception, e:
            log.exception(e)

    return intermediateCatalogList


def main():
    """Scans catalogs for broken references.  If --fix, attempts to remove broken references."""

    executionStart = time.time()
    scriptName = os.path.basename(__file__).split('.')[0]
    parser = ZenToolboxUtils.parse_options(scriptVersion, scriptName + scriptSummary + documentationURL)
    # Add in any specific parser arguments for %scriptName
    parser.add_argument("-f", "--fix", action="store_true", default=False,
                        help="attempt to remove any invalid references")
    parser.add_argument("-n", "--cycles", action="store", default="12", type=int,
                        help="maximum times to cycle (with --fix, <= 12)")
    parser.add_argument("-l", "--list", action="store_true", default=False,
                        help="output all supported catalogs")
    parser.add_argument("-c", "--catalog", action="store", default="",
                        help="only scan/fix specified catalog")
    cliOptions = vars(parser.parse_args())
    log, logFileName = ZenToolboxUtils.configure_logging(scriptName, scriptVersion, cliOptions['tmpdir'])
    log.info("Command line options: %s" % (cliOptions))
    if cliOptions['debug']:
        log.setLevel(logging.DEBUG)

    print "\n[%s] Initializing %s v%s (detailed log at %s)" % \
          (time.strftime("%Y-%m-%d %H:%M:%S"), scriptName, scriptVersion, logFileName)

    # Attempt to get the zenoss.toolbox lock before any actions performed
    if not ZenToolboxUtils.get_lock("zenoss.toolbox", log):
        sys.exit(1)

    # Obtain dmd ZenScriptBase connection
    dmd = ZenScriptBase(noopts=True, connect=True).dmd
    log.debug("ZenScriptBase connection obtained")

    anyIssue = False
    global maxCycles
    if cliOptions['fix']:
        if cliOptions['cycles'] > 12:
            maxCycles = 12
        else:
            maxCycles = cliOptions['cycles']
    else:
        maxCycles = 1

    validCatalogList = build_catalog_list(dmd, log)
    if cliOptions['list']:
        print "List of supported Zenoss catalogs to examine:\n"
        for item in validCatalogList:
            print item.prettyName
        log.info("Zencatalogscan finished - list of supported catalogs output to CLI")
    else:
        if cliOptions['catalog']:
            foundItem = False
            for item in validCatalogList:
                if cliOptions['catalog'] == item.prettyName:
                    foundItem = True
                    anyIssue = scan_catalog(item, cliOptions['fix'],
                                            dmd, log, not cliOptions['skipEvents'])
            if not foundItem:
                print("Catalog '%s' unrecognized - unable to scan" % (cliOptions['catalog']))
                log.error("CLI input '%s' doesn't match recognized catalogs" % (cliOptions['catalog']))
                exit(1)
        else:
            for item in validCatalogList:
                anyIssue = scan_catalog(item, cliOptions['fix'],
                                        dmd, log, not cliOptions['skipEvents']) or anyIssue

    # Print final status summary, update log file with termination block
    print("\n[%s] Execution finished in %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                 datetime.timedelta(seconds=int(time.time() - executionStart))))
    log.info("zencatalogscan completed in %1.2f seconds" % (time.time() - executionStart))
    log.info("############################################################")

    if not cliOptions['skipEvents']:
        if anyIssue:
            eventSummaryMsg = "%s encountered errors (took %1.2f seconds)" % (scriptName, (time.time() - executionStart))
            eventSeverity = 4
        else:
            eventSummaryMsg = "%s completed without errors (took %1.2f seconds)" % (scriptName, (time.time() - executionStart))
            eventSeverity = 2

        ZenToolboxUtils.send_summary_event(
            eventSummaryMsg, eventSeverity,
            scriptName, "executionStatus",
            documentationURL, dmd
        )

    if anyIssue and not cliOptions['fix']:
        print("** WARNING ** Issues were detected - Consult KB article at")
        print("      https://support.zenoss.com/hc/en-us/articles/203118075\n")
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
