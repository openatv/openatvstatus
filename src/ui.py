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

# PYTHON IMPORTS
from datetime import datetime, timedelta
from json import loads
from re import search
from shutil import rmtree
from os import makedirs
from os.path import exists, join
from zoneinfo import ZoneInfo
from requests import get
from requests.exceptions import RequestException
from twisted.internet.reactor import callFromThread, callInThread

# ENIGMA IMPORTS
from enigma import BT_HALIGN_CENTER, BT_KEEP_ASPECT_RATIO, BT_SCALE, BT_VALIGN_CENTER, eTimer, getPeerStreamingBoxes
from Components.ActionMap import ActionMap
from Components.config import ConfigSelection, ConfigSubsection, ConfigText, config
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.SystemInfo import BoxInfo
from Components.Sources.List import List
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.Setup import Setup
from Tools.Directories import SCOPE_PLUGINS, resolveFilename
from Tools.LoadPixmap import LoadPixmap

# PLUGIN IMPORTS
from . import _, __version__  # for localized messages
from .Buildstatus import Buildstatus

# PLUGIN GLOBALS
BS = Buildstatus()
BS.start()

helplist = [x.split(" ")[0] for x in BS.archlist]
archlist = []
for arch in helplist:
	archparts = arch.split("_")
	version = _("oldest available version") if archparts[1] == "oldest" else _("latest available version")
	archlist.append((arch, f"{archparts[0].upper()} ({version})"))
archlist = sorted(list(set(archlist)))
datechoices = [("%d.%m.%Y", "dd.mm.yyyy"), ("%d/%m/%Y", "dd/mm/yyyy"), ("%d-%m-%Y", "dd-mm-yyyy"), ("%Y/%m/%d", "yyyy/mm/dd"),
				("%Y-%d-%m", "yyyy-mm-dd"), ("%-d.%-m.%Y", "d.m.yyyy"), ("%-m/%-d/%Y", "m/d/yyyy"), ("%Y/%-m/%-d", "yyyy/m/d")]
config.plugins.OpenATVstatus = ConfigSubsection()
config.plugins.OpenATVstatus.animate = ConfigSelection(default="50", choices=[("0", _("off")), ("70", _("slower")), ("50", _("normal")), ("30", _("faster"))])
config.plugins.OpenATVstatus.favarch = ConfigSelection(default="current", choices=[("current", _("selected box"))] + archlist)
config.plugins.OpenATVstatus.nextbuild = ConfigSelection(default="relative", choices=[("relative", _("relative time")), ("absolute", _("absolute time"))])
config.plugins.OpenATVstatus.timezone = ConfigSelection(default="local", choices=[("local", _("local time (this box)")), ("server", _("server time (UTC)"))])
config.plugins.OpenATVstatus.dateformat = ConfigSelection(default="%d.%m.%Y", choices=datechoices)
config.plugins.OpenATVstatus.favboxes = ConfigText(default="", fixed_size=False)


class ATVglobs:
	VERSION = f"v{__version__}"
	MODULE_NAME = __name__.split(".")[-2]
	FAVLIST = [tuple(x.strip() for x in item.replace("(", "").replace(")", "").split(",")) for item in config.plugins.OpenATVstatus.favboxes.value.split(";")] if config.plugins.OpenATVstatus.favboxes.value else []
	PICURL = "https://raw.githubusercontent.com/oe-alliance/remotes/master/boxes/"
	TEMPPATH = "/tmp/OpenATVstatus/"
	ICONPATH = resolveFilename(SCOPE_PLUGINS, "Extensions/OpenATVstatus/icons/")

	def fmtDateTime(self, datetimestr):
		if datetimestr:
			if datetimestr != "00:00:00":
				berlin = datetime.strptime(datetimestr, "%Y/%m/%d, %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Berlin"))  # server time
				utc = berlin.astimezone(ZoneInfo("UTC"))  # UTC time as a uniform basis
				time = utc.astimezone() if config.plugins.OpenATVstatus.timezone.value == "local" else utc  # local time on user demand
				timefmt = f"{config.plugins.OpenATVstatus.dateformat.value} %H:%M h"
				return f"{time.strftime(timefmt)}"
			else:
				datetimestr = ""
		return datetimestr

	def round_minutes(self, timestr):
		if timestr:
			timestr = timestr.split(",")  # handle those exceptions: e.g. '-1 day, 23:59:24'
			tlist = timestr[0].strip().split(":") if len(timestr) == 1 else timestr[1].strip().split(":")
			timestr = f"{int(timedelta(hours=int(tlist[0]), minutes=int(tlist[1]), seconds=(int(tlist[2]) + 30) // 60 * 60).total_seconds() / 60)} min"
		return timestr

	def getArchitectureWithFallback(self, requested_arch):
		"""Find a platform for the requested architecture with intelligent fallback."""
		if not requested_arch or not BS.platlist:
			return BS.platlist[0] if BS.platlist else None
		# Try the requested architecture first
		platform = BS.getplatform(requested_arch)
		if platform:
			return platform
		# If not found, try intelligent fallback based on architecture
		archparts = requested_arch.split("_")
		base_arch = archparts[0].upper() if len(archparts) > 0 else ""
		# Try same architecture with _latest
		if base_arch:
			fallback_arch = f"{base_arch}_latest"
			platform = BS.getplatform(fallback_arch)
			if platform:
				return platform
		# Try fallback priority list: ARM_latest → MIPSEL_latest → DEVEL_latest
		for fallback in ["ARM_latest", "MIPSEL_latest", "DEVEL_latest"]:
			platform = BS.getplatform(fallback)
			if platform:
				return platform
		# Last resort: return first available
		return BS.platlist[0] if BS.platlist else None


class Carousel(ATVglobs):
	def __init__(self, delay=50):
		self.delay = delay
		self.error = None
		self.stepcount = 0
		self.forward = True
		self.carouselTimer = None
		self.callactive = False
		self.prevstr = ""
		self.currstr = ""
		self.nextstr = ""
		self.carouselTimer = eTimer()
		self.carouselTimer.callback.append(self.turn)

	def start(self, choicelist, index, callback):
		if not choicelist:
			self.error = f"[{self.MODULE_NAME}] ERROR in module 'start': choicelist is empty or None!"
			return
		self.choicelist = choicelist
		self.callback = callback
		self.buildRotateList()
		self.moveToIndex(index)
		self.prevstr = self.rlist[0]
		self.currstr = self.rlist[1]
		self.nextstr = self.rlist[2]
		self.maxlen = max([len(item) for item in self.rlist])

	def stop(self):
		self.callback = None
		self.setStandby()

	def setStandby(self):
		self.callactive = False
		if self.carouselTimer:
			self.carouselTimer.stop()

	def setDelay(self, delay=50):
		self.delay = delay

	def buildRotateList(self):
		self.rlist = self.choicelist.copy()
		for idx in range(3 - len(self.choicelist)):  # fill-up tiny rlists only
			self.rlist += self.choicelist

	def moveToIndex(self, index):
		index = index % len(self.choicelist)
		self.buildRotateList()
		if index == 0:
			self.rlist = self.rlist[-1:] + self.rlist[:-1]  # rotate backward
		elif index > 1:
			for idx in range(index - 1):
				self.rlist = self.rlist[1:] + self.rlist[:1]  # rotate forward to desired position

	def turnForward(self):  # pre-calculated constants to improve performance of 'self.turn'
		self.forward = True
		self.prevold = self.rlist[0]
		self.currold = self.rlist[1]
		self.nextold = self.rlist[2]
		self.rlist = self.rlist[1:] + self.rlist[:1]  # rotate forward
		self.prevnew = self.rlist[0]
		self.currnew = self.rlist[1]
		self.nextnew = self.rlist[2]
		self.setTimer()

	def turnBackward(self):  # pre-calculated constants to improve performance of 'self.turn'
		self.forward = False
		self.prevnew = self.rlist[0]
		self.currnew = self.rlist[1]
		self.nextnew = self.rlist[2]
		self.rlist = self.rlist[-1:] + self.rlist[:-1]  # rotate backward
		self.prevold = self.rlist[0]
		self.currold = self.rlist[1]
		self.nextold = self.rlist[2]
		self.setTimer()

	def setTimer(self):
		if self.carouselTimer:
			self.stepcount = 0
			self.callactive = True
			self.carouselTimer.start(self.delay, False)

	def turn(self):  # rotates letters
		self.stepcount += 1
		step = self.stepcount if self.forward else -self.stepcount
		self.prevstr = f"{self.prevold[step:]}{self.prevnew[:step]}"
		self.currstr = f"{self.currold[step:]}{self.currnew[:step]}"
		self.nextstr = f"{self.nextold[step:]}{self.nextnew[:step]}"
		if abs(step) > self.maxlen:
			self.setStandby()
		if self.callactive and self.callback:
			self.callback((self.prevstr, self.currstr, self.nextstr))


class ATVfavorites(Screen, ATVglobs):
	skin = """
		<screen name="ATVfavorites" position="center,center" size="820,653" resolution="1280,720" title="" flags="wfNoBorder">
			<ePixmap position="10,10" size="300,50" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/openATV_HD.png" alphatest="blend" zPosition="1" />
			<widget name="version" position="290,40" size="40,20" font="Regular;16" halign="left" valign="center" />
			<widget source="Title" render="Label" position="410,16" size="330,48" font="Regular;36" halign="left" valign="bottom" />
			<widget source="global.CurrentTime" render="Label" position="680,10" size="140,60" font="Regular;50" noWrap="1" halign="center" valign="bottom" foregroundColor="#00FFFFFF" backgroundColor="#1A0F0F0F" transparent="1">
				<convert type="ClockToText">Default</convert>
			</widget>
			<widget source="global.CurrentTime" render="Label" position="520,10" size="160,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="#00FFFFFF" backgroundColor="#1A0F0F0F" transparent="1">
				<convert type="ClockToText">Format:%A</convert>
			</widget>
			<widget source="global.CurrentTime" render="Label" position="520,34" size="160,26" font="Regular;16" noWrap="1" halign="right" valign="bottom" foregroundColor="#00FFFFFF" backgroundColor="#1A0F0F0F" transparent="1">
				<convert type="ClockToText">Format:%e. %B</convert>
			</widget>
			<widget source="menu" render="Listbox" position="10,70" size="800,500" enableWrapAround="1" scrollbarMode="showOnDemand">
				<convert type="TemplatedMultiContent">
					{"templates":
						{"default": (100, [
							MultiContentEntryPixmapAlphaTest(pos=(14,10), size=(166,42), png=9, flags=BT_SCALE),
							MultiContentEntryText(text="BuildStatus:", pos=(200,6), size=(130,26), font=0, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER),
							MultiContentEntryText(text="BuildTime:", pos=(200,36), size=(130,26), font=0, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER),
							MultiContentEntryText(text="BoxesAhead:", pos=(200,66), size=(130,26), font=0, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER),
							MultiContentEntryText(text="StartBuild:", pos=(440,6), size=(110,26), font=0, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER),
							MultiContentEntryText(text="EndBuild:", pos=(440,36), size=(110,26), font=0, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER),
							MultiContentEntryText(text="NextBuild:", pos=(440,66), size=(110,26), font=0, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER),
							MultiContentEntryText(pos=(14,50), size=(166,26), font=1, color=0x20FFFFFF, color_sel=0x20FFFFFF, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=0),
							MultiContentEntryText(pos=(14,70), size=(166,26), font=2, color=0x20B0B0B0, color_sel=0x20B0B0B0, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=1),
							MultiContentEntryText(pos=(330,6), size=(110,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=2),
							MultiContentEntryText(pos=(330,36), size=(110,26), font=0, color=0x20FFFFFF, color_sel=0x20FFFFFF, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=3),
							MultiContentEntryText(pos=(330,66), size=(110,26), font=0, color=0x20FFFFFF, color_sel=0x20FFFFFF, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=4),
							MultiContentEntryText(pos=(550,6), size=(210,26), font=0, color=0x20FFFFFF, color_sel=0x20FFFFFF, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=5),
							MultiContentEntryText(pos=(550,36), size=(210,26), font=0, color=0x20FFFFFF, color_sel=0x20FFFFFF, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=6),
							MultiContentEntryText(pos=(550,66), size=(210,26), font=0, color=0x20FFFFFF, color_sel=0x20FFFFFF, flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=7),
							MultiContentEntryPixmapAlphaTest(pos=(760,20), size=(24,60), png=10, flags=BT_SCALE)
							]),
							"emptylist": (220, [
							MultiContentEntryText(pos=(10,143), size=(773,40), font=2, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, text=0),
							MultiContentEntryText(pos=(10,183), size=(773,40), font=2, flags=RT_HALIGN_CENTER|RT_VALIGN_CENTER, color=0x20F47D19, color_sel=0x2020F47D19, text=1)
							],False)
						},
					"fonts": [gFont("Regular",21), gFont("Regular",18), gFont("Regular",16)],
					"itemHeight":100
					}
				</convert>
			</widget>
			<eLabel position="10,570" size="780,28" backgroundColor="grey" zPosition="-1" />
			<widget name="platinfo" position="16,570" size="793,28" font="Regular;20" halign="center" valign="center" foregroundColor="black" backgroundColor="grey" />
			<widget name="red" position="33,610" size="6,43" backgroundColor="red" zPosition="1" />
			<ePixmap position="314,616" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_ok_HD.png" alphatest="blend" zPosition="1" />
			<ePixmap position="494,616" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_info_HD.png" alphatest="blend" zPosition="1" />
			<ePixmap position="660,616" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_menu_HD.png" alphatest="blend" zPosition="1" />
			<widget name="key_red" position="50,616" size="300,28" font="Regular;20" foregroundColor="grey" />
			<widget name="key_ok" position="350,616" size="370,28" font="Regular;20" foregroundColor="grey" />
			<widget name="key_info" position="530,616" size="120,28" font="Regular;20" halign="left" foregroundColor="grey" />
			<widget name="key_menu" position="696,616" size="170,28" font="Regular;20" halign="left" foregroundColor="grey" />
		</screen>"""

	def __init__(self, session):
		self.session = session
		Screen.__init__(self, session)
		self.setTitle(_("Favorites"))
		self.boxlist = []
		self.foundFavs = []
		self.platdict = {}
		self.currindex = 0
		self["version"] = Label(self.VERSION)
		self["platinfo"] = Label()
		self["red"] = Label("")
		self["key_red"] = Label(_("remove box from favorites"))
		self["key_ok"] = Label(_("Images list"))
		self["key_info"] = Label(_("Boxdetails"))
		self["key_menu"] = Label(_("Settings"))
		self["menu"] = List([])
		self["actions"] = ActionMap(["WizardActions",
									"DirectionActions",
									"MenuActions",
									"ColorActions"], {"ok": self.keyOk,
													"back": self.exit,
													"cancel": self.exit,
													"red": self.keyRed,
													"blue": self.keyOk,
													"up": self.keyUp,
													"down": self.keyDown,
													"right": self.keyPageDown,
													"left": self.keyPageUp,
													"nextBouquet": self.keyPageDown,
													"prevBouquet": self.keyPageUp,
													"info": self.keyInfo,
													"menu": self.openConfig
													}, -1)
		self.onLayoutFinish.append(self.onLayoutFinished)
		try:
			if not exists(self.TEMPPATH):
				makedirs(self.TEMPPATH, exist_ok=True)
		except OSError as error:
			self.session.open(MessageBox, f"Dateipfad für Boxbilder konnte nicht neu angelegt werden:\n'{error}'", type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)

	def onLayoutFinished(self):
		callInThread(self.createMenulist)

	def createMenulist(self):
		boxlist = []
		baselist = []
		boxpiclist = []
		statuslist = []
		self.currindex = 0
		self["menu"].setList([])
		if self.FAVLIST and BS.platlist:
			self["menu"].style = "default"
			usedarchs = []
			for favorite in self.FAVLIST:
				currfav = favorite[1]
				if currfav not in usedarchs:
					usedarchs.append(currfav)
			menulist = []
			for currarch in usedarchs:
				# for compatibility reasons: use oldest available platform if architecture version-no. is missing (older plugin releases)
				currplat = [plat for plat in BS.platlist if currarch.split(" ")[0].upper() in plat][0] if len(currarch.split(" ")) == 1 else currarch
				htmldict = BS.getbuildinfos(currplat)
				boxpix = None
				textlist = ["no box", "no platform", "unclear", "no server", "no server", "no server found", "no server found", "no server found", 0xFF0400, None]
				if htmldict:  # favorites' platform found
					for box in [item for item in self.FAVLIST if item[1] in set([item[1]])]:
						if box[1] in currarch and box[0] in htmldict["boxinfo"]:
							boxlist.append((box[0], currarch))
							bd = htmldict["boxinfo"][box[0]]
							palette = {"Building": 0x00B028, "Failed": 0xFF0400, "Complete": 0xFFFFFF, "Waiting": 0xFFAE00}
							color = palette.get(bd["BuildStatus"], 0xB0B0B0)
							nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(box[0], htmldict)
							if box[1] not in self.platdict:
								self.platdict[currplat] = {}
								self.platdict[currplat]["cycletime"] = f"{BS.strf_delta(cycletime)[:5]} h"
								self.platdict[currplat]["boxcounter"] = f"{counter}"
								self.platdict[currplat]["boxfailed"] = f"{failed}"
							if BS.findbuildbox(htmldict):
								nextbuild = self.fmtDateTime((datetime.now(tz=ZoneInfo("Europe/Berlin")) + nextbuild).strftime("%Y/%m/%d, %H:%M:%S")) if config.plugins.OpenATVstatus.nextbuild.value == "absolute" and nextbuild else f"{BS.strf_delta(nextbuild)[:5]} h"
							else:
								nextbuild, boxesahead = "server paused", "unclear"
							buildtime = self.round_minutes(bd["BuildTime"].strip())
							statuslist.append(box)  # collect all server status (avoids flickering in menu)
							textlist = [box[0], box[1], bd["BuildStatus"], buildtime, f"{boxesahead}", self.fmtDateTime(bd["StartBuild"]), self.fmtDateTime(bd["EndBuild"]), nextbuild, color, None]
							baselist.append(textlist)
							boxpix = self.imageDisplay(box)
							if not boxpix:
								boxpiclist.append(box[0])  # collect missing box pictures (avoids flickering in menu)
				else:  # favorites' platform not found
					for box in self.FAVLIST:
						if box[1] == currarch:
							boxlist.append((box[0], currarch))
							textlist = [box[0], box[1], "unclear", "no server", "no server", "no server found", "no server found", "no server found", 0xFF0400, None]
							baselist.append(textlist)
							boxpix = self.imageDisplay(box)
							if not boxpix:
								boxpiclist.append(box[0])  # collect missing box pictures (avoids flickering in menu)
				menulist.append(textlist[:-1] + [boxpix] + [None])  # remove last entry 'serverstatus' from textlist (no need for skin)
			self["menu"].updateList(menulist)
			self["red"].show()
			self["key_red"].show()
			self.baselist = baselist
			self.boxlist = boxlist
			for box in statuslist:  # download missing server status
				callInThread(self.getServerStatus, box)
			for boxname in boxpiclist:  # download missing box pictures
				callInThread(self.imageDownload, boxname)
		else:
			self["red"].hide()
			self["key_red"].hide()
			self["menu"].style = "emptylist"
			self["menu"].updateList([(_("No favorites (box, platform) set yet."), _("Please select favorite(s) in the image lists."))])
		self["menu"].setIndex(self.currindex)
		self.updateStatus()

	def imageDownload(self, boxname):
		try:
			response = get(f"{self.PICURL}{boxname}.png".encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except RequestException as error:
			print(f"[{self.MODULE_NAME}] ERROR in module 'imageDownload': {error}")
		else:
			if exists(self.TEMPPATH):
				with open(join(self.TEMPPATH, f"{boxname}.png"), "wb") as f:
					f.write(response.content)
		self.updateMenulist()

	def imageDisplay(self, box):
		picfile = join(self.TEMPPATH, f"{box[0]}.png")
		return LoadPixmap(cached=True, path=picfile) if exists(picfile) else None

	def updateMenulist(self):
		menulist = []
		for textlist in self.baselist:
			picfile = join(self.TEMPPATH, f"{textlist[0]}.png")
			boxpix = LoadPixmap(cached=True, path=picfile) if exists(picfile) else None
			picfile = join(self.ICONPATH, textlist[9]) if textlist[9] else None
			statuspix = LoadPixmap(cached=True, path=picfile) if picfile and exists(picfile) else None
			menulist.append(tuple(textlist[:-1] + [boxpix] + [statuspix]))  # remove last entry 'serverstatus' from textlist (no need for skin)
		self["menu"].updateList(menulist)

	def getServerStatus(self, box):
		box = list(box)
		if box[0]:
			url = f"https://ampel.mynonpublic.com/status/index.php?boxname={box[0]}"
			try:
				response = get(url.encode(), timeout=(3.05, 6))
				response.raise_for_status()
			except RequestException as error:
				print(f"[{self.MODULE_NAME}] ERROR in module 'getServerStatus': {error}")
			else:
				try:
					htmldata = response.content.decode()
					if htmldata:
						server = search(r"src='(.*?)'/></center>", htmldata)
						if server:
							for idx in range(len(self.baselist)):
								if box == self.baselist[idx][:2]:
									self.baselist[idx][9] = server.group(1)  # replace last entry 'serverstatus'
									self.updateMenulist()
					self.error = f"[{self.MODULE_NAME}] ERROR in module 'getstatus': server access failed."
				except Exception as err:
					self.error = f"[{self.MODULE_NAME}] ERROR in module 'getstatus': invalid data from server {err}"
		else:
			self.error = f"[{self.MODULE_NAME}] ERROR in module 'getstatus': missing boxname"

	def updateStatus(self):
		if self.FAVLIST:
			self.currindex = self["menu"].getSelectedIndex()
			if self.boxlist and self.currindex is not None:
				currplat = self.boxlist[self.currindex][1]
				if currplat in self.platdict.keys():
					platdict = self.platdict[currplat]
					self["platinfo"].setText(f"{_('platform')}: {currplat}, {_('last build cycle')}: {platdict['cycletime']}, {platdict['boxcounter']} {_('boxes')}, {_('failed')}: {platdict['boxfailed']}")
				else:
					self["platinfo"].setText(f"{_('platform')}: {_('invalid')}, {_('last build cycle')}: {_('unclear')}, {_('unclear')} {_('boxes')}, {_('failed')}: {_('unclear')}")

	def msgboxCB(self, answer):
		if answer is True and self.boxlist and self.currindex is not None:
			self.FAVLIST.remove(self.foundFavs[0])
			config.plugins.OpenATVstatus.favboxes.value = ";".join(f"({','.join(item)})" for item in self.FAVLIST) if self.FAVLIST else ""
			config.plugins.OpenATVstatus.favboxes.save()
			removedbox = self.boxlist[self.currindex]
			self.createMenulist()
			self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully removed from favorites!") % removedbox, type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)

	def keyOk(self):
		if config.plugins.OpenATVstatus.favarch.value == "current":
			if self.boxlist and self.currindex is not None:
				currbox = self.boxlist[self.currindex]
			elif BS.platlist:
				currbox = ("", BS.platlist[0])
			else:
				return
		else:
			# Get the selected favorite box
			if self.boxlist and self.currindex is not None:
				boxname = self.boxlist[self.currindex][0]
				fav_arch = self.boxlist[self.currindex][1]  # Use the favorite's original architecture
			else:
				return
			# Try the favorite's original architecture first, then fallback
			platform = BS.getplatform(fav_arch)
			if platform is None:
				platform = self.getArchitectureWithFallback(fav_arch)
			currbox = (boxname, platform) if platform else None
		self.oldfavlist = self.FAVLIST[:]
		if currbox and currbox[1] in BS.platlist:
			self.session.openWithCallback(self.ATVimageslistCB, ATVimageslist, currbox)

	def keyInfo(self):
		if self.boxlist and self.currindex is not None:
			currbox = self.boxlist[self.currindex]
			if currbox:
				self.session.open(ATVboxdetails, currbox)

	def keyRed(self):
		if self.boxlist and self.currindex is not None:
			self.foundFavs = [item for item in self.FAVLIST if item == self.boxlist[self.currindex]]
			if self.foundFavs:
				self.session.openWithCallback(self.msgboxCB, MessageBox, _("Do you really want to remove Box '%s-%s' from favorites?") % self.boxlist[self.currindex], MessageBox.TYPE_YESNO, timeout=20, default=False)

	def ATVimageslistCB(self):
		if self.oldfavlist != self.FAVLIST:  # any changes when running 'ATVimageslist'?
			self.createMenulist()

	def keyUp(self):
		self["menu"].up()
		self.updateStatus()

	def keyDown(self):
		self["menu"].down()
		self.updateStatus()

	def keyPageUp(self):
		self["menu"].pageUp()
		self.updateStatus()

	def keyPageDown(self):
		self["menu"].pageDown()
		self.updateStatus()

	def keyTop(self):
		self["menu"].top()
		self.updateStatus()

	def keyBottom(self):
		self["menu"].bottom()
		self.updateStatus()

	def exit(self):
		BS.stop()
		if exists(self.TEMPPATH):
			rmtree(self.TEMPPATH)
		self.close()

	def openConfig(self):
		self.session.openWithCallback(self.openConfigCB, ATVconfig)

	def openConfigCB(self):
			self.createMenulist()


class ATVimageslist(Screen, ATVglobs):
	skin = """
		<screen name="ATVimageslist" position="center,center" size="1240,690" resolution="1280,720" title="" flags="wfNoBorder">
			<ePixmap position="10,10" size="300,50" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/openATV_HD.png" alphatest="blend" zPosition="1" />
			<widget name="version" position="290,40" size="40,20" font="Regular;16" halign="left" valign="center" />
			<widget source="Title" render="Label" position="360,16" size="170,48" font="Regular;36" halign="left" valign="bottom" />
			<widget name="prev_label" position="530,10" size="170,24" font="Regular;16" halign="right" valign="top" />
			<widget name="curr_label" position="723,10" size="280,24" font="Regular;16" halign="center" valign="top" />
			<widget name="next_label" position="1030,10" size="170,24" font="Regular;16" halign="left" valign="top" />
			<ePixmap position="713,33" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_left_HD.png" alphatest="blend" zPosition="1" />
			<ePixmap position="986,33" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_right_HD.png" alphatest="blend" zPosition="1" />
			<widget name="prev_plat" position="530,26" size="170,36" font="Regular;24" halign="right" valign="bottom" />
			<widget name="curr_plat" position="723,20" size="280,50" font="Regular;33" halign="center" valign="bottom" foregroundColor="green" />
			<widget name="next_plat" position="1030,26" size="170,36" font="Regular;24" halign="left" valign="bottom" />
			<ePixmap position="1193,33" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/bouquet_HD.png" alphatest="blend" zPosition="1" />
			<widget name="curr_date" position="1100,6" size="120,30" font="Regular;20" halign="right" valign="top" />
			<eLabel position="10,70" size="1220,30" backgroundColor="grey" zPosition="-1" />
			<eLabel text="Nr" position="10,70" size="35,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="BoxName" position="55,70" size="190,30" font="Regular;20" halign="left" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="BuildStatus" position="200,70" size="110,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="StartBuild" position="310,70" size="230,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="StartFeedSync" position="540,70" size="230,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="EndBuild" position="770,70" size="230,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="SyncTime" position="1000,70" size="100,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel text="BuildTime" position="1110,70" size="100,30" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<widget source="menu" render="Listbox" position="10,100" size="1220,494" scrollbarMode="showOnDemand">
				<convert type="TemplatedMultiContent">
					{"template": [
					MultiContentEntryText(pos=(0,0), size=(35,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=0),
					MultiContentEntryText(pos=(45,0), size=(190,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER, text=1),
					MultiContentEntryText(pos=(190,0), size=(110,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=2),
					MultiContentEntryText(pos=(300,0), size=(230,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=3),
					MultiContentEntryText(pos=(530,0), size=(230,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=4),
					MultiContentEntryText(pos=(760,0), size=(230,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=5),
					MultiContentEntryText(pos=(990,0), size=(100,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=6),
					MultiContentEntryText(pos=(1100,0), size=(100,26), font=0, color=MultiContentTemplateColor(8), color_sel=MultiContentTemplateColor(8), flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER, text=7)
					],
					"fonts": [gFont("Regular",20), gFont("Regular",12)],
					"itemHeight":26
					}
				</convert>
			</widget>
			<eLabel position="10,594" size="1220,30" backgroundColor="grey" zPosition="-1" />
			<widget name="boxinfo" position="15,594" size="570,28" font="Regular;20" halign="left" valign="center" foregroundColor="black" backgroundColor="grey" />
			<widget name="platinfo" position="585,594" size="640,28" font="Regular;20" halign="right" valign="center" foregroundColor="black" backgroundColor="grey" />
			<eLabel name="red" position="36,643" size="6,43" backgroundColor="red" zPosition="1" />
			<eLabel name="green" position="316,643" size="6,43" backgroundColor="green" zPosition="1" />
			<eLabel name="yellow" position="596,643" size="6,43" backgroundColor="yellow" zPosition="1" />
			<ePixmap position="873,650" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_ok_HD.png" alphatest="blend" zPosition="1" />
			<ePixmap position="1060,650" size="30,30" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/key_menu_HD.png" alphatest="blend" zPosition="1" />
			<widget name="key_red" position="50,650" size="280,28" font="Regular;20" halign="left" foregroundColor="grey" />
			<widget name="key_green" position="330,650" size="300,28" font="Regular;20" halign="left" foregroundColor="grey" />
			<widget name="key_yellow" position="610,650" size="280,28" font="Regular;20" halign="left" foregroundColor="grey" />
			<widget name="key_ok" position="910,650" size="166,28" font="Regular;20" halign="left" foregroundColor="grey" />
			<widget name="key_menu" position="1096,650" size="250,28" font="Regular;20" halign="left" foregroundColor="grey" />
		</screen>"""

	def __init__(self, session, box):
		self.session = session
		self.currbox = box
		self.currplat = box[1]
		Screen.__init__(self, session)
		self.setTitle(_("Images list"))
		self.boxlist = []
		self.htmldict = {}
		self.request_id = 0
		self.platidx = BS.platlist.index(self.currplat)
		self.currindex = 0
		self.favindex = 0
		self.foundFavs = []
		self["prev_plat"] = Label()
		self["curr_plat"] = Label()
		self["next_plat"] = Label()
		self["prev_label"] = Label()
		self["curr_label"] = Label()
		self["next_label"] = Label()
		self["version"] = Label(self.VERSION)
		self["curr_date"] = Label(datetime.now().strftime("%x"))
		self["boxinfo"] = Label()
		self["platinfo"] = Label()
		self["menu"] = List([])
		self["key_red"] = Label()
		self["key_green"] = Label(_("jump to construction site"))
		self["key_yellow"] = Label(_("jump to favorite(s)"))
		self["key_ok"] = Label(_("Boxdetails"))
		self["key_menu"] = Label(_("Settings"))
		self["actions"] = ActionMap(["WizardActions",
									"DirectionActions",
									"MenuActions",
									"ChannelSelectBaseActions",
									"ColorActions"], {
									"ok": self.keyOk,
									"back": self.exit,
									"cancel": self.exit,
									"red": self.keyRed,
									"green": self.keyGreen,
									"yellow": self.keyYellow,
									"up": self.keyUp,
									"down": self.keyDown,
									"right": self.keyPageDown,
									"left": self.keyPageUp,
									"nextBouquet": self.keyPageUp,
									"prevBouquet": self.keyPageDown,
									"nextMarker": self.nextPlatform,
									"prevMarker": self.prevPlatform,
									"info": self.keyOk,
									"menu": self.openConfig
									}, -1)
		delay = int(config.plugins.OpenATVstatus.animate.value)
		self.CS = Carousel(delay if delay else 50)
		self.CS.start(BS.platlist, self.platidx, self.CarouselCB)
		self.onLayoutFinish.append(self.onLayoutFinished)

	def onLayoutFinished(self):
		self["prev_label"].setText(_("previous"))
		self["curr_label"].setText(_("current platform"))
		self["next_label"].setText(_("next"))
		self["menu"].setList([])
		self.setPlatformStatic()
		self.refreshplatlist()

	def refreshplatlist(self):
		self.currplat = BS.platlist[self.platidx]
		self.request_id += 1
		request_id = self.request_id
		self.htmldict = None
		self.makeimagelist()
		# Keep overlapping requests separate and apply results on the GUI thread.
		buildstatus = Buildstatus()
		buildstatus.platlist = BS.platlist
		buildstatus.platdict = BS.platdict
		buildstatus.getbuildinfos(self.currplat, callback=lambda buildInfo: callFromThread(self.refreshCallback, buildInfo, request_id))

	def refreshCallback(self, htmldict, request_id=None):
		if request_id is not None and request_id != self.request_id:
			return
		self.htmldict = htmldict  # for updateList in case config will be changed
		self.makeimagelist()

	def makeimagelist(self):
		menulist = []
		boxlist = []
		if self.htmldict:
			for boxname in self.htmldict["boxinfo"]:
				boxlist.append((boxname, self.currplat))
				bd = self.htmldict["boxinfo"][boxname]
				palette = {"Building": 0x00B028, "Failed": 0xFF0400, "Complete": 0xB0B0B0, "Waiting": 0xFFAE00}
				color = 0xFDFf00 if [item for item in self.FAVLIST if item == (boxname, self.currplat)] else palette.get(bd["BuildStatus"], 0xB0B0B0)
				buildtime = self.round_minutes(bd["BuildTime"].strip())
				synctime = self.round_minutes(bd["SyncTime"].strip())
				menulist.append(tuple([bd["No"], boxname, bd["BuildStatus"], self.fmtDateTime(bd["StartBuild"]), self.fmtDateTime(bd["StartFeedSync"]), self.fmtDateTime(bd["EndBuild"]), synctime, buildtime, color]))
		self.boxlist = boxlist
		self["menu"].updateList(menulist)
		if self.currbox and self.htmldict is not None:
			foundbox = [item for item in boxlist if item == self.currbox]
			if foundbox:
				self["menu"].setIndex(self.boxlist.index(foundbox[0]))
			self.currbox = None
		self.updateStatus()

	def updateStatus(self):
		self.currindex = self["menu"].getSelectedIndex()
		buildInfo = self.htmldict
		if buildInfo and self.currindex is not None and 0 <= self.currindex < len(self.boxlist):
			if self.boxlist[self.currindex] in self.FAVLIST:
				self["key_red"].setText(_("remove box from favorites"))
			else:
				self["key_red"].setText(_("add box to favorites"))
			currbox = self.boxlist[self.currindex][0]
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(currbox, buildInfo)
			buildstatus = buildInfo["boxinfo"][currbox]["BuildStatus"]
			if nextbuild and BS.findbuildbox(buildInfo):
				boxinfo = _("Next build ends in %s, still %s boxes ahead") % (f"{BS.strf_delta(nextbuild)[:5]} h", boxesahead)
			elif buildstatus == "Building":
				boxinfo = _("Image is under construction, the duration is unclear...")
			elif buildstatus == "Waiting":
				boxinfo = _("Image is waiting with priority, the duration is unclear...")
			else:
				boxinfo = _("Server paused, unclear how many boxes are ahead...")
			self["boxinfo"].setText(boxinfo)
			if cycletime:
				cycletime = f"{BS.strf_delta(cycletime)[:5]} h"
				self["platinfo"].setText(f"{_('last build cycle')}: {cycletime}, {counter} {_('boxes')}, {_('failed')}: {failed}")
			else:
				self["platinfo"].setText(_("Nothing to do - no build cycle"))
		else:
			self.currindex = None
			self["key_red"].setText("")
			self["boxinfo"].setText(_("No box found in this platform!"))
			self["platinfo"].setText("")

	def nextPlatform(self):
		self.platidx = (self.platidx + 1) % len(BS.platlist)
		delay = int(config.plugins.OpenATVstatus.animate.value)
		if delay:
			self.CS.setDelay(delay)  # in case it has changed
			self.CS.turnForward()
		else:
			self.setPlatformStatic()
		self.refreshplatlist()

	def prevPlatform(self):
		self.platidx = (self.platidx - 1) % len(BS.platlist)
		delay = int(config.plugins.OpenATVstatus.animate.value)
		if delay:
			self.CS.setDelay(delay)  # in case it has changed
			self.CS.turnBackward()
		else:
			self.setPlatformStatic()
		self.refreshplatlist()

	def setPlatformStatic(self):
		self["prev_plat"].setText(BS.platlist[self.platidx - 1] if self.platidx > 0 else BS.platlist[len(BS.platlist) - 1])
		self["curr_plat"].setText(BS.platlist[self.platidx])
		self["next_plat"].setText(BS.platlist[self.platidx + 1] if self.platidx < len(BS.platlist) - 1 else BS.platlist[0])

	def CarouselCB(self, rotated):
		self["prev_plat"].setText(rotated[0])
		self["curr_plat"].setText(rotated[1])
		self["next_plat"].setText(rotated[2])

	def keyOk(self):
		if self.boxlist and self.currindex is not None:
			currbox = self.boxlist[self.currindex]
			if currbox:
				self.session.open(ATVboxdetails, currbox)

	def msgboxCB(self, answer):
		if answer is True and self.boxlist and self.currindex is not None:
			self.FAVLIST.remove(self.foundFavs[0])
			config.plugins.OpenATVstatus.favboxes.value = ";".join(f"({','.join(item)})" for item in self.FAVLIST) if self.FAVLIST else ""
			config.plugins.OpenATVstatus.favboxes.save()
			self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully removed from favorites!") % self.boxlist[self.currindex], type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)
			self.refreshplatlist()

	def keyRed(self):
		if self.boxlist and self.currindex is not None:
			self.foundFavs = [item for item in self.FAVLIST if item == self.boxlist[self.currindex]]
			if self.foundFavs:
				self.session.openWithCallback(self.msgboxCB, MessageBox, _("Do you really want to remove Box '%s-%s' from favorites?") % self.boxlist[self.currindex], MessageBox.TYPE_YESNO, timeout=20, default=False)
			else:
				self.FAVLIST.append(self.boxlist[self.currindex])
				config.plugins.OpenATVstatus.favboxes.value = ";".join(f"({','.join(item)})" for item in self.FAVLIST) if self.FAVLIST else ""
				config.plugins.OpenATVstatus.favboxes.save()
				self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully added to favorites!") % self.boxlist[self.currindex], type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)
				self.refreshplatlist()

	def keyGreen(self):
		if self.boxlist:
			findbuildbox = (BS.findbuildbox(self.htmldict), self.currplat)
			if findbuildbox in self.boxlist:
				self["menu"].setIndex(self.boxlist.index(findbuildbox))
				self.updateStatus()
			else:
				self.session.open(MessageBox, text=_("At the moment no image is built on the platform '%s'!") % self.currplat, type=MessageBox.TYPE_INFO, timeout=5, close_on_any_key=True)

	def keyYellow(self):
		if self.boxlist and self.FAVLIST:
			for offset in range(1, len(self.FAVLIST) + 1):
				favoriteindex = (self.favindex + offset) % len(self.FAVLIST)
				favorite = self.FAVLIST[favoriteindex]
				if len(favorite) != 2 or not favorite[0] or not favorite[1]:
					continue
				platform = favorite[1] if " " in favorite[1] else BS.getplatform(favorite[1])
				if platform not in BS.platlist:
					continue
				favorite = (favorite[0], platform)
				if platform == self.currplat and favorite not in self.boxlist:
					continue
				self.favindex = favoriteindex
				self.currbox = favorite
				if self.currbox in self.boxlist:
					self["menu"].setIndex(self.boxlist.index(self.currbox))
					self.updateStatus()
				else:
					self.platidx = BS.platlist.index(platform)
					self.CS.moveToIndex(self.platidx)
					self.setPlatformStatic()
					self.refreshplatlist()
				break

	def keyUp(self):
		self["menu"].up()
		self.updateStatus()

	def keyDown(self):
		self["menu"].down()
		self.updateStatus()

	def keyPageUp(self):
		self["menu"].pageUp()
		self.updateStatus()

	def keyPageDown(self):
		self["menu"].pageDown()
		self.updateStatus()

	def keyTop(self):
		self["menu"].top()
		self.updateStatus()

	def keyBottom(self):
		self["menu"].bottom()
		self.updateStatus()

	def exit(self):
		self.request_id += 1  # Discard pending responses after the screen closes.
		self.CS.stop()
		self.close()

	def openConfig(self):
		self.session.openWithCallback(self.openConfigCB, ATVconfig)

	def openConfigCB(self):
		self.makeimagelist()


class ATVboxdetails(Screen, ATVglobs):
	skin = """
		<screen name="ATVboxdetails" position="center,center" size="666,486" resolution="1280,720" title="" flags="wfNoBorder">
			<ePixmap position="10,10" size="300,50" pixmap="/usr/lib/enigma2/python/Plugins/Extensions/OpenATVstatus/icons/openATV_HD.png" alphatest="blend" zPosition="1" />
			<widget name="version" position="290,40" size="40,20" font="Regular;16" halign="left" valign="center" />
			<widget source="Title" position="330,16" size="330,48" font="Regular;36" halign="left" valign="bottom" render="Label" />
			<widget name="curr_date" position="536,6" size="120,26" font="Regular;20" halign="right" valign="top" />
			<eLabel position="536,36" size="120,28" backgroundColor="grey" zPosition="-1" />
			<widget name="status" position="553,33" size="103,26" font="Regular;20" halign="center" valign="center" foregroundColor="black" backgroundColor="grey" />
			<widget name="picture" position="10,70" size="646,234" alphatest="blend" />
			<widget name="details" position="20,306" size="626,132" font="Regular;20" halign="left" valign="top" />
			<eLabel name="red" position="36,446" size="6,43" backgroundColor="red" zPosition="1" />
			<widget name="key_red" position="50,453" size="253,28" font="Regular;20" halign="left" foregroundColor="grey" />
		</screen>"""

	def __init__(self, session, box):
		self.session = session
		self.box = box
		Screen.__init__(self, session)
		self.setTitle(_("Boxdetails"))
		self["version"] = Label(self.VERSION)
		self["curr_date"] = Label(datetime.now().strftime("%x"))
		self["status"] = Label()
		self["picture"] = Pixmap()
		self["details"] = Label()
		self["key_red"] = Label(_("Cancel"))
		self["actions"] = ActionMap(["OkCancelActions",
									"ColorActions"], {"ok": self.exit,
													"back": self.exit,
													"cancel": self.exit,
													"red": self.exit,
													}, -1)
		self.onLayoutFinish.append(self.onLayoutFinished)

	def onLayoutFinished(self):
		self["picture"].hide()
		self.picfile = join(self.TEMPPATH, f"{self.box[0]}.png")
		if exists(self.picfile):
			self.idownloadCB()
		else:
			callInThread(self.imageDownload, self.box[0])
		status = "offline"
		details = ""
		if self.box[0] == BoxInfo.getItem("BoxName"):
			status = "online"
			details += f"{_('Model')}:\t{BoxInfo.getItem('displaymodel')}\n"
			details += f"{_('Brand')}:\t{BoxInfo.getItem('displaybrand')}\n"
			details += f"{_('Image')}:\t{BoxInfo.getItem('displaydistro')}\n"
			details += f"{_('Version')}:\t{BoxInfo.getItem('imageversion')}.{BoxInfo.getItem('imgrevision')}\n"
			details += f"{_('Chipset')}:\t{BoxInfo.getItem('socfamily')}\n"
			self["details"].setText(details)
		else:
			streamurls = getPeerStreamingBoxes()
			if streamurls:
				streamurl = [x for x in streamurls if self.box[0] in x]  # example streamurls: ['http://gbue4k.local:8001', 'http://sf8008.local:8001']
				bd = self.getAPIdata(f"{streamurl[0][:streamurl[0].rfind(':')]}:80/api/about") if streamurl else None
				if bd and bd["info"]:
					status = "online"
					details += f"{_('Model')}:\t{bd.get('info', {}).get('model', '')}\n"
					details += f"{_('Brand')}:\t{bd.get('info', {}).get('brand', '')}\n"
					details += f"{_('Image')}:\t{bd.get('info', {}).get('friendlyimagedistro', '')}\n"
					details += f"{_('Version')}:\t{bd.get('info', {}).get('imagever', '')}\n"
					details += f"{_("Chipset")}:\t{bd.get("info", {}).get("chipset", "")}h\n"
					self["status"].setText("online")
		if not details:
			details += f"{_('Model')}:\t{self.box[0]}\n"
			details += f"\n{_('Box is OFFLINE! No current details available')}"
		self["status"].setText(status)
		self["details"].setText(details)

	def getAPIdata(self, apiurl):
		try:
			response = get(apiurl, timeout=(3.05, 6))
			response.raise_for_status()
			return loads(response.content)
		except RequestException as error:
			print(f"[{self.MODULE_NAME}] ERROR in module 'getAPIdata': {error}")

	def imageDownload(self, boxname):
		try:
			response = get(f"{self.PICURL}{boxname}.png".encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except RequestException as error:
			print(f"[{self.MODULE_NAME}] ERROR in module 'imageDownload': {error}")
		else:
			with open(join(self.TEMPPATH, f"{boxname}.png"), "wb") as f:
				f.write(response.content)
		self.idownloadCB()

	def idownloadCB(self):
		self["picture"].instance.setPixmapScaleFlags(BT_SCALE | BT_KEEP_ASPECT_RATIO | BT_HALIGN_CENTER | BT_VALIGN_CENTER)
		self["picture"].instance.setPixmapFromFile(self.picfile)
		self["picture"].show()

	def exit(self):
		self.close()


class ATVconfig(Setup):
	def __init__(self, session):
		Setup.__init__(self, session, "OpenATVstatus", plugin="Extensions/OpenATVstatus", PluginLanguageDomain="OpenATVstatus")
