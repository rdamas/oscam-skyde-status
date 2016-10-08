# -*- coding: utf-8 -*-

from Components.Language import language
from Tools.Directories import resolveFilename, SCOPE_PLUGINS
import os, gettext

PluginLanguageDomain = "OscamSkydeStatus"
PluginLanguagePath = "Extensions/OscamSkydeStatus/locale"

def localeInit():
	lang = language.getLanguage()[:2]
	os.environ["LANGUAGE"] = lang
	print "[Oscam Skyde Status] set language to ", lang
	gettext.bindtextdomain(PluginLanguageDomain, resolveFilename(SCOPE_PLUGINS, PluginLanguagePath))

def _(txt):
	t = gettext.dgettext(PluginLanguageDomain, txt)
	if t == txt:
		print "[Oscam Skyde Status] fallback to default Enigma2 Translation for", txt
		t = gettext.gettext(txt)
	return t

def isDebug():
    return os.path.exists(resolveFilename(SCOPE_PLUGINS, "Extensions/OscamSkydeStatus/__debug__"))

localeInit()
language.addCallback(localeInit)