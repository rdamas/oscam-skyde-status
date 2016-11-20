# -*- coding: utf-8 -*-

from Components.Language import language
from Tools.Directories import SCOPE_PLUGINS
from Tools.Directories import resolveFilename
import gettext
import os

PluginLanguageDomain = "OscamSkydeStatus"
PluginLanguagePath = "Extensions/OscamSkydeStatus/locale"

def localeInit():
    lang = language.getLanguage()[:2]
    os.environ["LANGUAGE"] = lang
    print "[OSS] set language to ", lang
    gettext.bindtextdomain(PluginLanguageDomain, resolveFilename(SCOPE_PLUGINS, PluginLanguagePath))

def _(txt):
    t = gettext.dgettext(PluginLanguageDomain, txt)
    if t == txt:
        t = gettext.gettext(txt)
        if isDebug():
            print "[OSS] fallback to default Enigma2 Translation for", txt
    return t

def isDebug():
    try:
        return isDebug.mode
    except:
        isDebug.mode = os.path.exists(resolveFilename(SCOPE_PLUGINS, "Extensions/OscamSkydeStatus/__debug__"))
        return isDebug.mode

localeInit()
language.addCallback(localeInit)