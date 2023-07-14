# PYTHON IMPORTS
from gettext import bindtextdomain, dgettext, gettext
from os.path import join

# ENIGMA IMPORTS
from Components.Language import language
from Tools.Directories import resolveFilename, SCOPE_PLUGINS

PLUGINPATH = resolveFilename(SCOPE_PLUGINS, "Extensions/OpenATVstatus/")


def localeInit():
    bindtextdomain("OpenATVstatus", join(PLUGINPATH, "locale"))


def _(txt):
    t = dgettext("OpenATVstatus", txt)
    if t == txt:
        print("[OpenATVstatus] fallback to default translation for %s" % txt)
        t = gettext(txt)
    return t


localeInit()
language.addCallback(localeInit)
