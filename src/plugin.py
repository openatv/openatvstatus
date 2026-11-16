########################################################################################################
#                                                                                                      #
#  OpenATVstatus: shows current build status of images and estimates time to next image build          #
#  Coded by Mr.Servo @ OpenATV (c) 2023-2026                                                           #
#  ----------------------------------------------------------------------------------------------------#
#  This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.   #
#  This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep      #
#  the license), but it may not be commercially distributed. Advertise with this tool is not allowed.  #
#  For other uses, permission from the authors is necessary.                                           #
#                                                                                                      #
########################################################################################################
from Plugins.Plugin import PluginDescriptor
from . import _  # for localized messages

def main(session, **kwargs):
	from .ui import ATVfavorites
	session.open(ATVfavorites)


def menu(menuid, **kwargs):
	return [("OpenATV Status", main, "openatv_status", 20)] if menuid == "support" else []


def Plugins(**kwargs):
	description = _("Current overview of the OpenATV images building servers")
	return [
		PluginDescriptor(
			name="OpenATV Status",
			icon="plugin.png",
			description=description,
			where=PluginDescriptor.WHERE_PLUGINMENU,
			fnc=main
		),
		PluginDescriptor(
			name="OpenATV Status",
			description=description,
			where=PluginDescriptor.WHERE_MENU,
			fnc=menu
		)
	]
