# -*- coding: utf-8 -*-
from enigma import eTimer, getDesktop
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Sources.List import List
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen

from __init__ import _

import base64
import ConfigParser
import json
import re
import requests
import subprocess

class OscamConfig:
    """Auslesen der Config-Files einer laufenden Oscam-Installation
    
    Momentan nur die oscam.conf auslesen, um emmlogdir und Webif-Zugangsdaten
    zu ermitteln.
    
    Außerdem eine Methode zum Auslesen der gespeicherten unique EMMs
    """
    def __init__(self, confdir):
        self.confdir = confdir
        self.cp = ConfigParser.ConfigParser()
        self.webif = None
        self.emmlogdir = None
    
    def readOscamUser(self):
        read = self.cp.read(self.confdir + '/oscam.conf')
        if read:
            try:
                self.webif = self.cp.items('webif')
            except ConfigParser.NoSectionError:
                pass

            try:
                self.emmlogdir = self.cp.get('global', 'emmlogdir')
            except ConfigParser.NoOptionError:
                self.emmlogdir = self.confdir
    
    def getWebif(self):
        if not self.webif:
            self.readOscamUser()
        if self.webif:
            return dict(self.webif)
        return None
    
    def formatDate(self, date):
        m = re.match("(\d+)/(\d+)/(\d+) (.*)", date)
        if m:
            return m.group(3)+"."+m.group(2)+"."+m.group(1)+" "+m.group(4)
        return date
    
    #
    # Die Datei mit den gespeicherten Unique EMM einlesen, alle gespeicherten
    # EMMs mit letztem aufgetretenem Datum zurückliefern. Zur Darstellung
    # am TV die Serial und Data unkenntlich machen.
    #
    def getSavedEmm(self, reader):

        def getitem(x):
            return seen[x]['last']

        logfile = self.emmlogdir + '/' + reader + '_unique_emm.log'
        seen = {}
        ret = []

        with open(logfile, 'r') as log:
            for line in log:
                elems = re.split(" +", line.rstrip())
                key = elems[3]
                date = elems[0] + ' ' + elems[1]
                try:
                    if seen[key]['first'] > date:
                        seen[key]['first'] = date
                    if seen[key]['last'] < date:
                        seen[key]['last'] = date
                except:
                    seen[key] = {}
                    seen[key]['first'] = date
                    seen[key]['last'] = date

        keys = sorted(seen, key=getitem, reverse=True)
        for key in keys:
            payload = key[0:6] + ' ' + key[6:8] + ' ######## ' + key[16:30] + ' ...'
            ret.append( ( self.formatDate(seen[key]['first']), self.formatDate(seen[key]['last']), payload, key) )
            print "[OSS]", payload
        return ret
    

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

    def _get(self,url):
        if self.user:
            status = requests.get(url, auth=requests.auth.HTTPDigestAuth(self.user, self.password)).content
        else:
            status = requests.get(url).content
        return status
    
    def getStatus(self):
        url = self.webif+'/oscamapi.json?part=status'
        return self._get(url)

    def formatDate(self, date):
        m = re.match("(\d+)-(\d+)-(\d+)T.*", date)
        if m:
            return m.group(3)+". "+m.group(2)+". "+m.group(1)
        return date
    
    #
    # Das Oscam-JSON-API liefert alle nötigen Informationen, um
    # festzustellen, ob es eine laufende lokale V13/V14 gibt.
    # Den Reader-Label sowie die CAID zurückgeben.
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
                        if ent['caid'] in ['09C4', '098C']:
                            reader = client['rname_enc']
                            caid = ent['caid']
                            break
            if reader and caid:
                return { 'reader': reader, 'caid': caid }
        
        return None
    
    #
    # Das Formular zum Schreiben eines EMM ans Webif abschicken
    #
    def writeEmm(self, reader, caid, emm):
        url = self.webif+'/emm_running.html?label=%s&emmfile=&emmcaid=%s&ep=%s&action=Launch' % (reader,caid,emm)
        return self._get(url)

    #
    # Regex um den Payload aus den Daten auszulesen
    #
    def getPayloadFromLine(self,line):
        m = re.search('(0F 0[46] .. .. .. .. .. ..)', line)
        if m:
            return m.group(1)
        return None
    
    #
    # Payload aus 9 Sekunden Debug-Log ermittlen
    #
    def extractPayload(self):
        url = self.webif+'/logpoll.html?debug=0'
        print "OSS] call:", url
        logpoll = self._get(url)
        payload = None
        try:
            obj = json.loads(logpoll)
            lines = obj['oscam']['lines']

            foundPayloadHeader = False
            for line in lines:
                decoded = base64.b64decode(line['line'])
                if foundPayloadHeader:
                    lookAhead -= 1
                    if lookAhead == 0:
                        payload = self.getPayloadFromLine(decoded)
                        break
                if 'Decrypted payload' in decoded:
                    lookAhead = 2
                    foundPayloadHeader = True
        except Exception as e:
            print "[OSS]", e
            pass
        
        print "[OSS]", self.callback
        return self.callback(payload)

    #
    # Den Payload auslesen
    #
    def fetchPayload(self, callback):
        url = self.webif+'/logpoll.html?debug=4'
        self._get(url)
        self.callback = callback
        self.timer.start(10000, True)
    
    #
    # Tier-IDs auslesen
    #
    def getTiers(self, reader):
        url = self.webif+'/oscamapi.json?part=entitlement&label=%s' % reader
        entitlements = self._get(url)
        tiers = []
        expires = None
        try:
            obj = json.loads(entitlements)
            for line in obj['oscam']['entitlements']:
                tiers.append( line['id'][-4:] )
                if not expires and line['id'][-4:-2] == '00':
                    expires = self.formatDate(line['expireDate'])
        except:
            pass
        return { 'tiers': tiers, 'expires': expires }

class OscamStatus(Screen):
    version = "2016-10-09 0.4"
    skin = { "fhd": """
        <screen name="OscamStatus" position="0,0" size="1920,1080" title="Oscam Status" flags="wfNoBorder">
            <widget name="expires" position="20,20" size="600,36" font="Regular;25" />
            <widget name="payload" position="620,20" size="700,36" font="Regular;25" />
            <widget name="f0tier" position="1340,20" size="400,36" font="Regular;25" />
            <widget name="headline" position="20,60" size="1880,76" font="Regular;25" />
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
        </screen>
    """, 
    "hd": """
        <screen name="OscamStatus" position="0,0" size="1280,720" title="Oscam Status" flags="wfNoBorder">
            <widget name="expires" position="10,10" size="400,24" font="Regular;18" />
            <widget name="payload" position="420,10" size="430,24" font="Regular;18" />
            <widget name="f0tier" position="860,10" size="330,24" font="Regular;18" />
            <widget name="headline" position="10,40" size="1260,45" font="Regular;18" />
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
        </screen>
    """ }
    
    def __init__(self, session):
        self.session = session
        self.status = None
        self.list = None
        self.tiers = None
        self.expires = None

        self.adaptScreen()
	self.skin = OscamStatus.skin[self.useskin]
        
	Screen.__init__(self, session)
        self["actions"] =  ActionMap(["ColorActions", "WizardActions"], {
                "back": self.cancel,
                "ok": self.ok,
                "red": self.red,
        }, -1)
        
        self["key_red"] = Label(_("Payload ermitteln"))
        self["key_green"] = Label()
        self["payload"] = Label(_("Payload: rot drücken"))
        self["f0tier"] = Label(_("F0-Tier vorhanden: unbekannt"))
        
        self.fetchStatus()
        if self.status:
            self["headline"] = Label(_("Liste der gespeicherten EMMs - mit OK zum Schreiben auswählen:"))
            if self.tiers:
                if "00F0" in self.tiers:
                    f0text = _("ja")
                else:
                    f0text = _("nein")
                self["f0tier"].setText(_("F0-Tier vorhanden: %s") % f0text)
        else:
            self["headline"] = Label(_("Ist Oscam gestartet? Läuft eine lokale V13/V14 Karte?"))

        if self.expires:
            self["expires"] = Label(_("Karte läuft ab am: %s") % str(self.expires))
        else:
            self["expires"] = Label(_("Status konnte nicht ermittelt werden."))
            
        self["emmlist"] = List(self.list)


    def cancel(self):
        self.close()
    
    def ok(self):
        self.emmToWrite = str(self["emmlist"].getCurrent()[3])
        if self.emmToWrite != "":
            self.session.openWithCallback(
                self.writeEmm, 
                MessageBox, 
                _("Folgendes EMM wirklich schreiben?\n%s") % self.emmToWrite, 
                type = MessageBox.TYPE_YESNO,
                timeout = -1
            )
    
    def red(self):
        self.payload = None
        self.session.openWithCallback(
            self.fetchPayload, 
            MessageBox, 
            _("Das Ermitteln des Payloads dauert etwa 10 Sekunden.\nDazu muss auf einem Sky-Sender geschaltet sein. Fortfahren?"), 
            type = MessageBox.TYPE_YESNO,
            timeout = -1
        )
    
    #
    # Das Default-Oscam-Config-Dir ermitteln
    #
    def getConfdirFromOscamHelp(self, oscam):
        process = subprocess.Popen(oscam + " --help | grep ConfigDir", shell=True, stdout=subprocess.PIPE)
        for line in  process.communicate()[0].split("\n"):
            m = re.search(":\s*(\S*)", line)
            if m:
                return m.group(1)
        return None
    
    def fetchStatus(self):
        #
        # In der Prozessliste einen laufenden Oscam-Prozess finden
        #
        confdir = None
        process = subprocess.Popen("ps axw | grep [o]scam", shell=True, stdout=subprocess.PIPE)
        for line in  process.communicate()[0].split("\n"):
            #
            # Anhand des Parameters -c das Config-Dir finden
            #
            m = re.search(r"-c (\S+).*$", line)
            if m:
                confdir = m.group(1)
                break
            else:
                #
                # Oscam läuft, wurde aber nicht mit Parameter -c gestartet
                # Dann kann das Config-Dir über Aufruf von oscam --help 
                # ausgelesen werden. Zunächst einmal den Namen des laufenden
                # Binaries ermitteln
                #
                m = re.search(r"\s(\S*oscam\S*)(\s|$)", line)
                if m:
                    oscam = m.group(1)
                    confdir = self.getConfdirFromOscamHelp(oscam)
                    if confdir:
                        break

        #
        # Jetzt aus der oscam.conf die Webif-Config auslesen
        #
        if confdir:
            config = OscamConfig(confdir)
            user = config.getWebif()
            try:
                httpuser = user['httpuser']
                httppwd = user['httppwd']
            except KeyError:
                httpuser = None
                httppwd = None
            #
            # Über die Oscam-Webapi V13/V14-Reader suchen
            #
            self.webif = OscamWebif('localhost', user['httpport'], httpuser, httppwd)
            self.status = self.webif.getStatusSky()

            #
            # Wenn ein Reader für Sky-CAID's vorhanden ist,
            # versuchen, aus dem Oscam-Config-Dir die unique EMMs zu holen
            #
            if self.status:
                self.list = [ ("Erstes Vorkommen", "Letztes Vorkommen", "EMM: Länge Serial Data", "")]
                self.list.extend( config.getSavedEmm(self.status['reader']) )
                tiers = self.webif.getTiers(self.status['reader'])
                self.tiers = tiers['tiers']
                self.expires = tiers['expires']

    # 
    # Das ausgewählte EMM über das Webinterface auf die Karte schreiben
    #
    def writeEmm(self, retval):
        if retval:
            self.webif.writeEmm(self.status['reader'], self.status['caid'], self.emmToWrite)
    
    #
    # Den Payload ermitteln
    #
    def fetchPayload(self,retval):
        if retval:
            self["payload"].setText(_("Payload wird ermittelt"))
            self.payload = self.webif.fetchPayload(self.callbackFetchPayload)

    #
    # Callback vom Webif, wenn Payload auslesen fertig ist
    #
    def callbackFetchPayload(self,payload):
        self.payload = payload
        if self.payload:
            self["payload"].setText(_("Payload: %s") % str(self.payload))
        else:
            self["payload"].setText(_("Payload konnte nicht ermittelt werden."))
        self.session.open(MessageBox, _("Der Payload ist: %s") % self.payload, MessageBox.TYPE_INFO)
    
    # Anhand der Desktop-Größe einige Variablen anpassen;
    # so sollte es egal sein, ob ein SD, HD oder FHD-Skin benutzt wird.
    def adaptScreen(self):
        fb_w = getDesktop(0).size().width()
        if fb_w < 1920:
            self.useskin = "hd"
        else:
            self.useskin = "fhd"
    