# -*- coding: utf-8 -*-
import base64
import ConfigParser
import fileinput
import json
import os
import re
import requests

from enigma import eTimer, getDesktop, iServiceInformation
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Sources.List import List
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

from __init__ import _

class WebifException(Exception):
    pass

class OscamConfig:
    """Auslesen der Config-Files einer laufenden Oscam-Installation
    
    Momentan nur die oscam.conf auslesen, um emmlogdir und Webif-Zugangsdaten
    zu ermitteln.
    
    Außerdem eine Methode zum Auslesen der gespeicherten unique EMMs
    """
    
    EMM_OK        = 1
    EMM_NOT_FOUND = 2
    EMM_VAR_LOG   = 3
    
    def __init__(self, confdir):
        self.confdir = confdir
        self.cp = ConfigParser.SafeConfigParser()
        self.webif = None
        self.emmlogdir = None
        self._readOscamUser()
    
    def _readOscamUser(self):
        read = self.cp.read(self.confdir + '/oscam.conf')
        if read:
            try:
                self.emmlogdir = self.cp.get('global', 'emmlogdir')
                if self.emmlogdir == '':
                    self.emmlogdir = self.confdir
            except ConfigParser.NoOptionError:
                self.emmlogdir = self.confdir

            try:
                hostname = self.cp.get('global', 'serverip')
            except ConfigParser.NoOptionError:
                hostname = 'localhost'

            try:
                self.cp.set('webif', 'hostname', hostname)
                self.webif = self.cp.items('webif')
            except ConfigParser.NoSectionError:
                pass
    
    def getWebif(self):
        if self.webif:
            return dict(self.webif)
        return None
    
    def _formatDate(self, date):
        m = re.match(r"(\d+)/(\d+)/(\d+) (.*)", date)
        if m:
            return m.group(3)+"."+m.group(2)+"."+m.group(1)+" "+m.group(4)
        return date
    
    #
    # Die Datei mit den gespeicherten Unique EMM einlesen, alle gespeicherten
    # EMMs mit letztem aufgetretenem Datum zurückliefern. Zur Darstellung
    # am TV die Serial und Data unkenntlich machen.
    #
    def getSavedEmm(self, reader):

        logfile = self.emmlogdir + '/' + reader + '_unique_emm.log'
        seen = {}
        ret = []
        hint = self.EMM_OK

        print "[OSS OscamConfig.getSavedEmm] versuche '%s' zu lesen" % logfile

        try:
            with open(logfile, 'r') as log:
                for line in log:
                    m = re.search(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+[0-9A-Z]{16}\s+([0-9A-F]+)\s+", line.rstrip())
                    if m:
                        date = m.group(1)
                        key = m.group(2)
                        try:
                            if seen[key]['first'] > date:
                                seen[key]['first'] = date
                            if seen[key]['last'] < date:
                                seen[key]['last'] = date
                        except KeyError:
                            seen[key] = {}
                            seen[key]['first'] = date
                            seen[key]['last'] = date
        except IOError as e:
            print "[OSS OscamConfig.getSavedEmm] I/O error: %s" % e.strerror
            hint = self.EMM_NOT_FOUND
            if self.emmlogdir[0:8] == '/var/log':
                hint = self.EMM_VAR_LOG

        if seen:
            keys = sorted(seen, key=lambda x: seen[x]['last'], reverse=True)
            for key in keys:
                payload = key[0:6] + ' ' + key[6:8] + ' ######## ' + key[16:30] + ' ...'
                ret.append( ( self._formatDate(seen[key]['first']), self._formatDate(seen[key]['last']), payload, key) )
                
        return { 'emm': ret, 'hint': hint }
    
    #
    # Blank out emmlogdir directive in oscam.conf.
    #
    def reconfigEmmlogdir(self):
        file = fileinput.input(files=self.confdir+'/oscam.conf', inplace=True, backup='.bak')

        for line in file:
            line = line.strip()
            m = re.search(r"(emmlogdir\s*=)", line)
            if m:
                line = m.group(1)
            print line

        file.close()
    

class OscamWebif:
    """Methoden, um über das Webif an Daten zu gelangen:
    - läuft eine V13 oder V14?
    - mit welchem Label?
    - wann laufen die Entitlements ab?
    - ein EMM schreiben
    """
    def __init__(self, host, port, user=None, password=None):
        self.webif = 'http://'+host+':'+port
        self.user = user
        self.password = password
        
        self.timer = eTimer()
        self.timer.callback.append(self.extractPayload)
        
        self.callback = None
        
        if password:
            password = '########'
        if user:
            user = '########'
        print "[OSS OscamWebif.__init__] OscamWebif(%s, %s, %s, %s)" % (host, port, user, password)

    #
    # Get requested web interface url.
    #
    # @param url string - url
    # @return string - contents of url
    #
    def _get(self, url):
        try:
            if self.user:
                r = requests.get(url, auth=requests.auth.HTTPDigestAuth(self.user, self.password))
            else:
                r = requests.get(url)
            print "[OSS OscamWebif._get] URL: %s => %s" % (url, r.status_code)
            if r.status_code != 200:
                raise WebifException(r.status_code)
        except Exception as e:
            print "[OSS OscamWebif._get] catch exception", e
            raise WebifException(521)
        return r.text
    
    #
    # Read status page from Oscam JSON API
    # @return string - json text with status information
    #
    def getStatus(self):
        url = self.webif+'/oscamapi.json?part=status'
        return self._get(url)

    #
    # @param date string - input date string
    # @return string - formatted date string
    # 
    def _formatDate(self, date):
        m = re.match(r"(\d+)-(\d+)-(\d+)T.*", date)
        if m:
            return m.group(3)+". "+m.group(2)+". "+m.group(1)
        return date
    
    #
    # Use Oscam JSON API to find out, if we have a local V13/V14 or 
    # Teleclub card running. We return reader and CAID of that card.
    #
    # @return None|dict
    #
    def getStatusSky(self):
        status = self.getStatus()
        reader = None
        caid = None
        if status:
            obj = json.loads(status)
            clients = obj['oscam']['status']['client']
            for client in clients:
                conn = client['connection']
                if conn['$'] == 'CARDOK':
                    for ent in conn['entitlements']:
                        if ent['caid'] in ['09C4', '098C', '09B6']:
                            reader = client['rname_enc']
                            caid = ent['caid']
                            break
                    else:
                        ent = self.getTiers(client['rname_enc'])
                        if ent['caid'] in ['09C4', '098C', '09B6']:
                            reader = client['rname_enc']
                            caid = ent['caid']
                            break
            if reader and caid:
                return { 'reader': reader, 'caid': caid }
        
        return None
    
    #
    # Write EMM via web interface form.
    #
    # @param reader string - label of affected reader
    # @param caid string - caid of affected reader
    # @param emm strig - emm to write to card
    # @param callback function - where to return to after writing
    #
    def writeEmm(self, reader, caid, emm, callback):
        url = self.webif+'/emm_running.html?label=%s&emmfile=&emmcaid=%s&ep=%s&action=Launch' % (reader,caid,emm)
        self._get(url)
        callback()

    #
    # Read payload from one line of live log data.
    #
    # @return string|None - payload if pattern matches.
    #
    def getPayloadFromLine(self,line):
        m = re.search('(0F 0[46] .. .. .. .. .. ..)', line)
        if m:
            return m.group(1)
        return None
    
    #
    # Read last payload from 10 seconds live log.
    # Call callback function after read out.
    #
    def extractPayload(self):
        url = self.webif+'/logpoll.html?debug=0'
        logpoll = self._get(url)
        payload = None
        try:
            obj = json.loads(logpoll)
            lines = obj['oscam']['lines']

            foundPayloadHeader = False
            lookAhead = 2
            for line in lines:
                decoded = base64.b64decode(line['line'])
                if foundPayloadHeader:
                    lookAhead -= 1
                    if lookAhead == 0:
                        payload = self.getPayloadFromLine(decoded)
                        foundPayloadHeader = False
                        continue
                if 'Decrypted payload' in decoded:
                    lookAhead = 2
                    foundPayloadHeader = True
        except Exception as e:
            print "[OSS OscamWebif.extractPayload] catch exception", e
        
        if self.callback:
            self.callback(payload)

    #
    # Read payload from live log.
    # Switch to debug level 4, set a timer, finish read out in timer callback.
    #
    # @param callback function - where to return after finishing timer callback.
    #
    def fetchPayload(self, callback):
        url = self.webif+'/logpoll.html?debug=4'
        self._get(url)
        self.callback = callback
        self.timer.start(10000, True)
    
    #
    # Read tier ID's
    #
    # @param reader string - label of reader
    #
    def getTiers(self, reader):
        url = self.webif+'/oscamapi.json?part=entitlement&label=%s' % reader
        entitlements = self._get(url)
        tiers = []
        expires = None
        caid = None
        try:
            obj = json.loads(entitlements)
            for line in obj['oscam']['entitlements']:
                tiers.append( line['id'][-4:] )
                if not expires and line['id'][-4:-2] == '00':
                    expires = self._formatDate(line['expireDate'])
                caid = line['caid']
        except:
            pass
        return { 'tiers': tiers, 'expires': expires, 'caid': caid }


class CardStatus:
    """Class that holds gathered information from running Oscam instance.
    Is independent of enigma2 session, so testably without running enigma2.
    Is inherited from OscamStatus.
    """
    
    def __init__(self, session):
        self.session = session
        
        self.oscamConfdir = None
        self.oscamWebifSupport = None
        self.oscamLivelogSupport = None
        self.localhostAccess = None
        self.status = None
        self.tiers = None
        self.hint = None
        self.expires = None
        self.list = None
        self.webif = None
        self.oscamConfig = None
        
        self.getOscamInformation()

    #
    # Look in oscam.version from temp file for ConfigDir parameter
    # and supported features.
    #
    # @param tempdir string - directory where oscam.version lives.
    # set self.oscamConfdir string - path to Oscam configuration directory
    # set self.oscamWebifSupport bool - is webif support compiled into Oscam
    # set self.oscamLivelogSupport - is live log support compiled into Oscam
    #
    def readOscamVersion(self, tempdir):
        try:
            for line in open(os.path.join(tempdir, 'oscam.version'), 'rb'):
                if 'ConfigDir:' in line:
                    self.oscamConfdir = line.split(":")[1].strip()
                    print "[OSS CardStatus.readOscamVersion] confdir:", self.oscamConfdir
                    
                if 'Web interface support:' in line:
                    self.oscamWebifSupport = line.split(":")[1].strip() == 'yes'
                    print "[OSS CardStatus.readOscamVersion] webif support:", self.oscamWebifSupport
                    
                if 'LiveLog support:' in line:
                    self.oscamLivelogSupport = line.split(":")[1].strip() == 'yes'
                    print "[OSS CardStatus.readOscamVersion] livelog support:", self.oscamLivelogSupport
                    
        except:
            print "[OSS CardStatus.readOscamVersion] kann", tempdir, "nicht öffnen."
    
    #
    # Find Oscam temp dir from running Oscam process.
    # Check if process was startet with param -t or --temp-dir
    #
    # @return string - temp dir where oscam.version lives.
    #
    def getOscamTempdir(self):
        tempdir = None

        pids = [pid for pid in os.listdir('/proc') if pid.isdigit()]
        for pid in pids:
            try:
                cmdline = open(os.path.join('/proc', pid, 'cmdline'), 'rb').read()
                cmdpart = cmdline.lower().split('\0')
                # @tested
                if '/oscam' in cmdpart[0] or cmdpart[0][0:5] == 'oscam':
                    nextIsTempDir = False
                    for part in cmdpart:
                        # @tested
                        if '--temp-dir' in part:
                            tempdir = part[11:]
                            break
                        # @tested
                        if part == '-t':
                            nextIsTempDir = True
                            continue
                        if nextIsTempDir:
                            tempdir = part.rstrip('/')
                            nextIsTempDir = False
                    break
            except IOError: # proc has terminated
                continue
        
        return tempdir
    
    #
    # Find out where oscam.conf lives.
    # First try to to read out /tmp/.oscam/oscam.version
    # If that does not exist, try to find it from running Oscam
    #
    def getOscamInformation(self):
        tempdir = '/tmp/.oscam'
        
        # @tested
        if os.path.exists(tempdir):
            self.readOscamVersion(tempdir)
            return
        
        # @tested
        tempdir = self.getOscamTempdir()
        if tempdir and os.path.exists(tempdir):
            self.readOscamVersion(tempdir)
    
    #
    # Get an OscamWebif object for communication via Web interface.
    #
    def getOscamWebif(self):
        if self.oscamWebifSupport:
            user = self.oscamConfig.getWebif()
            try:
                httpuser = user['httpuser']
            except KeyError:
                httpuser = None
            try:
                httppwd = user['httppwd']
            except KeyError:
                httppwd = None

            self.localhostAccess = True
            try:
                httpallowed = user['httpallowed']
                print "[OSS CardStatus.getOscamWebif] httpallowed:", httpallowed
                if '127.0.0.' not in httpallowed and '::1' not in httpallowed:
                    self.localhostAccess = False
            except:
                pass

            return OscamWebif(user['hostname'], user['httpport'], httpuser, httppwd)
        
        else:
            print "[OSS CardStatus.getOscamWebif] no webif support"
            raise WebifException(501)
    
    #
    # Read tier IDs and expire date from Oscam web interface.
    #
    # set self.expires - expire date from webif
    # set self.tiers - tiers list from webif
    # set self.localhostAccess - can localhost access webif
    # set self.webif - @class OscamWebif
    # set self.status - reader and caid for Sky from webif
    #
    def getCardStatus(self):
        #
        # Jetzt aus der oscam.conf die Webif-Config auslesen
        #
        if self.oscamConfdir:
            # Über die Oscam-Webapi V13/V14-Reader suchen
            self.oscamConfig = OscamConfig(self.oscamConfdir)
            self.webif = self.getOscamWebif()
            try:
                self.status = self.webif.getStatusSky()
            except WebifException as e:
                print "[OSS CardStatus.getCardStatus] catch exception", e

            if self.status:
                # gespeicherte unique EMMs anzeigen
                self.getSavedEmm()
                
                # Tier-IDs und Expire-Datum der Karte auslesen
                try:
                    tiers = self.webif.getTiers(self.status['reader'])
                    self.tiers = tiers['tiers']
                    self.expires = tiers['expires']
                except WebifException as e:
                    print "[OSS CardStatus.getCardStatus] catch exception", e
        else:
            print "[OSS CardStatus.getCardStatus] no oscam conf dir found"

    #
    # Read unique EMM's from Oscam config dir
    #
    def getSavedEmm(self):
        retemm = self.oscamConfig.getSavedEmm(self.status['reader'])
        self.hint = retemm['hint']
        self.list = [ ("Erstes Vorkommen", "Letztes Vorkommen", "EMM", "")]
        self.list.extend( retemm['emm'] )
    

class OscamStatus(Screen, CardStatus):
    version = "2017-03-28 1.1 beta"
    skin = { "fhd": """
        <screen name="OscamStatus" position="0,0" size="1920,1080" title="Oscam Sky DE Status" flags="wfNoBorder">
            <widget name="expires" position="20,20" size="600,36" font="Regular;25" />
            <widget name="payload" position="620,20" size="700,36" font="Regular;25" />
            <widget name="f0tier" position="1340,20" size="400,36" font="Regular;25" />
            <widget name="headline" position="20,60" size="1320,76" font="Regular;25" />
            <widget name="cardtype" position="1340,60" size="400,76" font="Regular;25" />
            <widget render="Listbox" source="emmlist" enableWrapAround="0"
                position="20,100" size="1880,880" transparent="1"  
                font="Regular;25" zPosition="5" scrollbarMode="showOnDemand"
                scrollbarSliderBorderWidth="0" scrollbarWidth="5"> 
                <convert type="TemplatedMultiContent">{
                    "template": [
                        MultiContentEntryText(
                            pos = (10, 10), 
                            size = (380, 40), 
                            font = 0, 
                            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP, 
                            text = 0),
                        MultiContentEntryText(
                            pos = (400, 10), 
                            size = (380, 40), 
                            font = 0, 
                            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP, 
                            text = 1),
                        MultiContentEntryText(
                            pos = (790, 10), 
                            size = (1000, 40), 
                            font = 0, 
                            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP | RT_WRAP, 
                            text = 2), 
                        ], 
                    "fonts": [gFont("Regular", 24)],
                    "itemHeight": 50 }
                </convert>
            </widget>
            <widget name="key_red" position="20,1000" zPosition="1" size="400,50" font="Regular;20" halign="center" valign="center" backgroundColor="#f01010" foregroundColor="#ffffff" transparent="0" />
            <widget name="key_green" position="440,1000" zPosition="1" size="400,50" font="Regular;20" halign="center" valign="center" backgroundColor="#10a010" foregroundColor="#ffffff" transparent="0" />
        </screen>
        """,
        
        "hd": """
        <screen name="OscamStatus" position="0,0" size="1280,720" title="Oscam Sky DE Status" flags="wfNoBorder">
            <widget name="expires" position="10,10" size="400,24" font="Regular;18" />
            <widget name="payload" position="420,10" size="430,24" font="Regular;18" />
            <widget name="f0tier" position="860,10" size="330,24" font="Regular;18" />
            <widget name="headline" position="10,40" size="840,45" font="Regular;18" />
            <widget name="cardtype" position="860,40" size="330,45" font="Regular;18" />
            <widget render="Listbox" source="emmlist" enableWrapAround="0"
                position="10,90" size="1260,560" transparent="1"  
                font="Regular;18" zPosition="5" scrollbarMode="showOnDemand"
                scrollbarSliderBorderWidth="0" scrollbarWidth="5"> 
                <convert type="TemplatedMultiContent">{
                    "template": [
                        MultiContentEntryText(
                            pos = (10, 10), 
                            size = (250, 33), 
                            font = 0, 
                            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP, 
                            text = 0),
                        MultiContentEntryText(
                            pos = (270, 10), 
                            size = (250, 33), 
                            font = 0, 
                            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP, 
                            text = 1),
                        MultiContentEntryText(
                            pos = (530, 10), 
                            size = (640, 33), 
                            font = 0, 
                            flags = RT_HALIGN_LEFT | RT_VALIGN_TOP | RT_WRAP, 
                            text = 2), 
                        ], 
                    "fonts": [gFont("Regular", 18)],
                    "itemHeight": 40 }
                </convert>
            </widget>
            <widget name="key_red" position="10,666" zPosition="1" size="300,33" font="Regular;16" halign="center" valign="center" backgroundColor="#f01010" foregroundColor="#ffffff" transparent="0" />
            <widget name="key_green" position="320,666" zPosition="1" size="300,33" font="Regular;16" halign="center" valign="center" backgroundColor="#10a010" foregroundColor="#ffffff" transparent="0" />
        </screen>
        """ }
    
    hintText = {
        1: 'Liste der gespeicherten EMMs - mit OK zum Schreiben auswählen.',
        2: 'Keine EMMs gefunden. 90 Minuten auf einem Sky-Kanal warten.',
        3: 'Keine EMMs. Tipp: "emmlogdir" mit "grün" fixen.'
    }
    
    def __init__(self, session):
        self.session = session
        self.status = None
        self.list = None
        self.tiers = None
        self.expires = None
        self.hint = None
        self.emmToWrite = None
        self.payload = None

        self.adaptScreen()
        self.skin = OscamStatus.skin[self.useskin]
        
        CardStatus.__init__(self, session)
        Screen.__init__(self, session)

        self['actions'] =  ActionMap(['ColorActions', 'WizardActions'], {
            "back": self.cancel,
            "ok": self.ok,
            "red": self.red,
            "green": self.green,
        }, -1)
        
        self['key_red'] = Label(_("Payload ermitteln"))
        self['key_green'] = Label()
        self['payload'] = Label(_("Payload: rot drücken"))
        self['f0tier'] = Label()
        self['cardtype'] = Label()
        self['headline'] = Label()
        self['expires'] = Label()
        self['emmlist'] = List()
        
        self.onLayoutFinish.append(self.showCardStatus)

    def cancel(self):
        self.close()
    
    #
    # Write selected EMM to card after confirmation.
    #
    def ok(self):
        self.emmToWrite = str(self['emmlist'].getCurrent()[3])
        if self.emmToWrite != "":
            self.session.openWithCallback(
                self.writeEmm, 
                MessageBox, 
                _("Folgendes EMM wirklich schreiben?\n%s") % self.emmToWrite, 
                type = MessageBox.TYPE_YESNO,
                timeout = -1
            )
    
    #
    # Fetch card payload from Oscam livelog after confirmation.
    #
    def red(self):
        self.payload = None
        if self.isProviderSky() or self.getCardtype() == "Teleclub":
            if self.oscamLivelogSupport:
                self.session.openWithCallback(
                    self.fetchPayload, 
                    MessageBox, 
                    _("Das Ermitteln des Payloads dauert etwa 10 Sekunden.\nFortfahren?"), 
                    type = MessageBox.TYPE_YESNO,
                    timeout = -1
                )
            else:
                self.session.open(
                    MessageBox, 
                    _("Der Payload kann nicht ermittelt werden, da Oscam ohne Livelog-Support übersetzt wurde."), 
                    MessageBox.TYPE_INFO
                )
        else:
            self.session.open(
                MessageBox, 
                _("Der Payload kann nur auf einem abonnierten Sky-Sender ermittelt werden."), 
                MessageBox.TYPE_INFO
            )
            
    
    #
    # Blank out emmlogdir directive in oscam.conf after confirmation.
    #
    def green(self):
        if self.hint == OscamConfig.EMM_VAR_LOG:
            self.session.openWithCallback(
                self.reconfigEmmlogdir,
                MessageBox, 
                _("Die oscam.conf kann jetzt so angepasst werden,\ndass EMM's dauerhaft gespeichert werden. Fortfahren?"), 
                type = MessageBox.TYPE_YESNO,
                timeout = -1
            )
    
    #
    # Compute text for "f0tier" label
    #
    def getF0text(self):
        f0text = _("unbekannt")
        if self.tiers:
            if "00F0" in self.tiers:
                f0text = _("ja")
            else:
                f0text = _("nein")
        return f0text
    
    #
    # Compute text for "cardtype" label
    #
    def getCardtype(self):
        cardtype = "unbekannt"
        if self.status:
            caid = self.status['caid']
            if caid == "09C4":
                cardtype = "V13"
            elif caid == "098C":
                cardtype = "V14"
            elif caid == "09B6":
                cardtype = "Teleclub"
        return cardtype
    
    #
    # Compute card status information and set Screen elements accordingly.
    #
    def showCardStatus(self):
        self.getCardStatus()
    
        self['f0tier'].setText(_("F0-Tier vorhanden: %s") % self.getF0text() )
        self['cardtype'].setText( _("Kartentyp: %s") % self.getCardtype() )
        
        if self.expires:
            self['expires'].setText(_("Karte läuft ab am: %s") % str(self.expires))
        else:
            self['expires'].setText(_("Status konnte nicht ermittelt werden."))
            
        if self.status:
            try:
                self['headline'].setText(_(self.hintText[self.hint]))
            except KeyError:
                pass

            self['emmlist'].setList(self.list)
            
            if self.list and len(self.list) < 2 and self.hint == OscamConfig.EMM_VAR_LOG:
                self['key_green'].setText(_("Emmlogdir fixen"))
            
        else:
            if self.localhostAccess:
                self['headline'].setText(_("Ist Oscam gestartet? Läuft eine lokale V13/V14 Karte?"))
            else:
                self['headline'].setText(_("In oscam.conf muss für 127.0.0.1 Zugriff erlaubt werden."))

    # 
    # Write selected EMM to card using web interface
    # Callback function on OK click.
    #
    def writeEmm(self, retval):
        if retval:
            try:
                self.webif.writeEmm(self.status['reader'], self.status['caid'], self.emmToWrite, self.callbackWriteEmm)
            except WebifException as e:
                print "[OSS OscamStatus.writeEmm] catch exception", e
    
    #
    # Web interface callback after writing EMM
    #
    def callbackWriteEmm(self):
        try:
            tiers = self.webif.getTiers(self.status['reader'])
            self.expires = tiers['expires']
            self['expires'].setText(_("Karte läuft ab am: %s") % str(self.expires))
        except WebifException as e:
            print "[OSS OscamStatus.callbackWriteEmm] catch exception", e


    #
    # Read payload information
    # Callback action on RED click.
    #
    def fetchPayload(self, retval):
        if retval:
            self['payload'].setText(_("Payload wird ermittelt"))
            try:
                self.webif.fetchPayload(self.callbackFetchPayload)
            except WebifException as e:
                print "[OSS OscamStatus.fetchPayload] catch exception", e

    #
    # Web interface callback after reading payload
    #
    def callbackFetchPayload(self, payload):
        self.payload = payload
        if self.payload:
            self['payload'].setText(_("Payload: %s") % str(self.payload))
        else:
            self['payload'].setText(_("Payload konnte nicht ermittelt werden."))
        info = ""
        if self.payload.startswith("0F 04 00 00 00 00") or self.payload.startswith("0F 06 00 00 00 00"):
            info = "Die Karte ist aktiv und nicht gepairt"
        elif self.payload.startswith("0F 04 00 10 20 00") or self.payload.startswith("0F 06 00 10 20 00"):
            info = "Die Karte muss verlängert werden"
        elif self.payload.startswith("0F 04 00 10 00 00") or self.payload.startswith("0F 06 00 10 00 00"):
            info = "Die Karte ist gepairt"
        elif self.payload.startswith("0F 04 00 00 20 00") or self.payload.startswith("0F 06 00 00 20 00"):
            info = "Dieser Sender ist nicht abonniert"
        self.session.open(MessageBox, _("Der Payload ist: %s\n%s") % (self.payload, info), MessageBox.TYPE_INFO)
    
    #
    # Blank out emmlogdir directive in oscam.conf
    # Callback action on GREEN click if applicable
    #
    def reconfigEmmlogdir(self, retval):
        if retval:
            self.oscamConfig.reconfigEmmlogdir()
            self.hint = OscamConfig.EMM_NOT_FOUND
            self['key_green'].setText('')
            self['headline'].setText(_(self.hintText[self.hint]))
    
    #
    # Check whether we are serving Sky
    #
    def isProviderSky(self):
        service = self.session.nav.getCurrentService()
        info = service and service.info()
        if info:
            if info.getName().replace('\xc2\x86','').replace('\xc2\x87','').startswith('Sky 1'):
                return False
            onid = info.getInfo(iServiceInformation.sONID)
            isCrypted = info.getInfo(iServiceInformation.sIsCrypted)
            return onid == 133 and isCrypted == 1
        return False

    #
    # Compute size of desktop. Screen will be chosen accordingly.
    #
    def adaptScreen(self):
        fb_w = getDesktop(0).size().width()
        if fb_w < 1920:
            self.useskin = "hd"
        else:
            self.useskin = "fhd"
    
