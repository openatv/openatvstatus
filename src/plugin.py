########################################################################################################
#                                                                                                      #
#  OpenATVstatus: shows current build status of images and estimates time to next image build          #
#  Coded by Mr.Servo @ OpenATV (c) 2023                                                                #
#  ----------------------------------------------------------------------------------------------------#
#  This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.   #
#  This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep      #
#  the license), but it may not be commercially distributed. Advertise with this tool is not allowed.  #
#  For other uses, permission from the authors is necessary.                                           #
#                                                                                                      #
########################################################################################################

# PYTHON IMPORTS
from datetime import datetime, timezone, timedelta
from json import loads
from os import makedirs
from os.path import join, exists
from re import search
from requests import get, exceptions
from shutil import rmtree
from twisted.internet.reactor import callInThread
from xml.etree.ElementTree import tostring, parse

# ENIGMA IMPORTS
from enigma import getDesktop, eTimer, getPeerStreamingBoxes, BT_SCALE, BT_KEEP_ASPECT_RATIO, BT_HALIGN_CENTER, BT_VALIGN_CENTER
from Components.ActionMap import ActionMap
from Components.config import config, ConfigSubsection, ConfigSelection, ConfigText, getConfigListEntry
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.Sources.List import List
from Components.SystemInfo import BoxInfo
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Tools.LoadPixmap import LoadPixmap
from Tools.Directories import resolveFilename, SCOPE_PLUGINS

# PLUGIN IMPORTS
from . import PLUGINPATH, _  # for localized messages
from .Buildstatus import Buildstatus

# PLUGIN GLOBALS
BS = Buildstatus()
BS.start()

helplist = [x.split(" ")[0] for x in BS.archlist]
archlist = []
for arch in helplist:
	archparts = arch.split("_")
	version = _("oldest available version") if archparts[1] == "oldest" else _("latest available version")
	archlist.append((arch, "%s (%s)" % (archparts[0].upper(), version)))
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


class ATVglobs():
	VERSION = "V2.7"
	MODULE_NAME = __name__.split(".")[-2]
	FAVLIST = [tuple(x.strip() for x in item.replace("(", "").replace(")", "").split(",")) for item in config.plugins.OpenATVstatus.favboxes.value.split(";")] if config.plugins.OpenATVstatus.favboxes.value else []
	PICURL = "https://raw.githubusercontent.com/oe-alliance/remotes/master/boxes/"
	TEMPPATH = "/tmp/OpenATVstatus/"
	ICONPATH = resolveFilename(SCOPE_PLUGINS, "Extensions/OpenATVstatus/icons/")

	def readSkin(self, skin):
		skintext = ""
		skinfile = join(PLUGINPATH, "skin_%s.xml" % ("fHD" if getDesktop(0).size().width() > 1300 else "HD"))
		try:
			with open(skinfile, "r") as file:
				try:
					domskin = parse(file).getroot()
					for element in domskin:
						if element.tag == "screen" and element.attrib["name"] == skin:
							skintext = tostring(element).decode()
							break
				except Exception as error:
					print("[%s] ERROR in module 'readSkin': Unable to parse skin data in '%s' - '%s'!" % (self.MODULE_NAME, skinfile, error))
		except OSError as error:
			print("[%s] ERROR in module 'readSkin': Unexpected error opening skin file '%s'! (%s)" % (self.MODULE_NAME, skinfile, error))
		return skintext

	def fmtDateTime(self, datetimestr):
		if datetimestr:
			if datetimestr != "00:00:00":
				time = datetime.strptime(datetimestr, "%Y/%m/%d, %H:%M:%S").replace(tzinfo=timezone.utc)
				if config.plugins.OpenATVstatus.timezone.value == "local":
					time = time.astimezone()
				return f"{time.strftime(f'{config.plugins.OpenATVstatus.dateformat.value}, %H:%M h')}"
			else:
				datetimestr = ""
		return datetimestr

	def roundMinutes(self, timestr):
		if timestr:
			timestr = timestr.split(",")  # handle those exceptions: e.g. '-1 day, 21:09:24'
			tlist = timestr[0].split(":") if len(timestr) == 1 else timestr[1].split(":")
			timestr = f"{int(timedelta(hours=int(tlist[0]), minutes=int(tlist[1]), seconds=(int(tlist[2]) + 30) // 60 * 60).total_seconds() / 60)} min"
		return timestr


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
			self.error = "[%s] ERROR in module 'start': choicelist is empty or None!" % self.MODULE_NAME
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
		self.prevstr = "%s%s" % (self.prevold[step:], self.prevnew[:step])
		self.currstr = "%s%s" % (self.currold[step:], self.currnew[:step])
		self.nextstr = "%s%s" % (self.nextold[step:], self.nextnew[:step])
		if abs(step) > self.maxlen:
			self.setStandby()
		if self.callactive and self.callback:
			self.callback((self.prevstr, self.currstr, self.nextstr))


class ATVfavorites(Screen, ATVglobs):
	def __init__(self, session):
		self.session = session
		self.skin = self.readSkin("ATVfavorites")
		Screen.__init__(self, session, self.skin)
		self.setTitle(_("Favorites"))
		self.boxlist = []
		self.foundFavs = []
		self.platdict = dict()
		self.currindex = 0
		self["version"] = Label(self.VERSION)
		self["platinfo"] = Label()
		self["red"] = Label("")
		self["key_red"] = Label(_("remove box from favorites"))
		self["key_blue"] = Label(_("Images list"))
		self["key_ok"] = Label(_("Boxdetails"))
		self["key_menu"] = Label(_("Settings"))
		self["menu"] = List([])
		self["actions"] = ActionMap(["WizardActions",
									 "DirectionActions",
									 "MenuActions",
									 "ColorActions"], {"ok": self.keyOk,
			   											"back": self.exit,
														"cancel": self.exit,
														"red": self.keyRed,
														"blue": self.keyBlue,
														"up": self.keyUp,
														"down": self.keyDown,
														"right": self.keyPageDown,
														"left": self.keyPageUp,
														"nextBouquet": self.keyPageDown,
														"prevBouquet": self.keyPageUp,
														"menu": self.openConfig
													}, -1)
		self.onLayoutFinish.append(self.onLayoutFinished)
		try:
			if not exists(self.TEMPPATH):
				makedirs(self.TEMPPATH, exist_ok=True)
		except OSError as error:
			self.session.open(MessageBox, "Dateipfad für Boxbilder konnte nicht neu angelegt werden:\n'%s'" % error, type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)

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
							nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(box[0])
							if box[1] not in self.platdict:
								self.platdict[currplat] = dict()
								self.platdict[currplat]["cycletime"] = f"{BS.strf_delta(cycletime)[:5]} h"
								self.platdict[currplat]["boxcounter"] = "%s" % counter
								self.platdict[currplat]["boxfailed"] = "%s" % failed
							if BS.findbuildbox():
								nextbuild = self.fmtDateTime((datetime.now(timezone.utc) + nextbuild).strftime("%Y/%m/%d, %H:%M:%S")) if config.plugins.OpenATVstatus.nextbuild.value == "absolute" and nextbuild else f"{BS.strf_delta(nextbuild)[:5]} h"
							else:
								nextbuild, boxesahead = "server paused", "unclear"
							buildtime = self.roundMinutes(bd["BuildTime"].strip())
							statuslist.append(box)  # collect all server status (avoids flickering in menu)
							textlist = [box[0], box[1], bd["BuildStatus"], buildtime, "%s" % boxesahead, self.fmtDateTime(bd["StartBuild"]), self.fmtDateTime(bd["EndBuild"]), nextbuild, color, None]
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
				menulist.append((textlist[:-1] + [boxpix] + [None]))  # remove last entry 'serverstatus' from textlist (no need for skin)
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
			response = get(("%s%s.png" % (self.PICURL, boxname)).encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except exceptions.RequestException as error:
			print("[%s] ERROR in module 'imageDownload': %s" % (self.MODULE_NAME, str(error)))
		else:
			if exists(self.TEMPPATH):
				with open(join(self.TEMPPATH, "%s.png" % boxname), "wb") as f:
					f.write(response.content)
		self.updateMenulist()

	def imageDisplay(self, box):
		picfile = join(self.TEMPPATH, "%s.png" % box[0])
		return LoadPixmap(cached=True, path=picfile) if exists(picfile) else None

	def updateMenulist(self):
		menulist = []
		for textlist in self.baselist:
			picfile = join(self.TEMPPATH, "%s.png" % textlist[0])
			boxpix = LoadPixmap(cached=True, path=picfile) if exists(picfile) else None
			picfile = join(self.ICONPATH, textlist[9]) if textlist[9] else None
			statuspix = LoadPixmap(cached=True, path=picfile) if picfile and exists(picfile) else None
			menulist.append(tuple(textlist[:-1] + [boxpix] + [statuspix]))  # remove last entry 'serverstatus' from textlist (no need for skin)
		self["menu"].updateList(menulist)

	def getServerStatus(self, box):
		box = list(box)
		if box[0]:
			url = "https://ampel.mynonpublic.com/status/index.php?boxname=%s" % box[0]
			try:
				response = get(url.encode(), timeout=(3.05, 6))
				response.raise_for_status()
			except exceptions.RequestException as error:
				print("[%s] ERROR in module 'getServerStatus': %s" % (self.MODULE_NAME, str(error)))
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
					self.error = "[%s] ERROR in module 'getstatus': server access failed." % self.MODULE_NAME
				except Exception as err:
					self.error = "[%s] ERROR in module 'getstatus': invalid data from server %s" % (self.MODULE_NAME, str(err))
		else:
			self.error = "[%s] ERROR in module 'getstatus': missing boxname" % self.MODULE_NAME

	def updateStatus(self):
		if self.FAVLIST:
			self.currindex = self["menu"].getSelectedIndex()
			if self.boxlist and self.currindex is not None:
				currplat = self.boxlist[self.currindex][1]
				if currplat in self.platdict.keys():
					platdict = self.platdict[currplat]
					self["platinfo"].setText("%s: %s, %s: %s, %s %s, %s: %s" % (_("platform"), currplat, _("last build cycle"), platdict["cycletime"], platdict["boxcounter"], _("boxes"), _("failed"), platdict["boxfailed"]))
				else:
					self["platinfo"].setText("%s: %s, %s: %s, %s %s, %s: %s" % (_("platform"), _("invalid"), _("last build cycle"), _("unclear"), _("unclear"), _("boxes"), _("failed"), _("unclear")))

	def msgboxCB(self, answer):
		if answer is True and self.boxlist and self.currindex is not None:
			self.FAVLIST.remove(self.foundFavs[0])
			config.plugins.OpenATVstatus.favboxes.value = ";".join("(%s)" % ",".join(item) for item in self.FAVLIST) if self.FAVLIST else ""
			config.plugins.OpenATVstatus.favboxes.save()
			removedbox = self.boxlist[self.currindex]
			self.createMenulist()
			self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully removed from favorites!") % removedbox, type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)

	def keyOk(self):
		if self.boxlist and self.currindex is not None:
			currbox = self.boxlist[self.currindex] if self.boxlist else None
			if currbox:
				self.session.open(ATVboxdetails, currbox)

	def keyRed(self):
		if self.boxlist and self.currindex is not None:
			self.foundFavs = [item for item in self.FAVLIST if item == self.boxlist[self.currindex]]
			if self.foundFavs:
				self.session.openWithCallback(self.msgboxCB, MessageBox, _("Do you really want to remove Box '%s-%s' from favorites?") % self.boxlist[self.currindex], MessageBox.TYPE_YESNO, timeout=20, default=False)

	def keyBlue(self):
		if config.plugins.OpenATVstatus.favarch.value == "current":
			if self.boxlist and self.currindex is not None:
				currbox = self.boxlist[self.currindex]
			elif BS.platlist:
				currbox = ("", BS.platlist[0])
			else:
				return
		else:
			currbox = ("", BS.getplatform(config.plugins.OpenATVstatus.favarch.value))
		self.oldfavlist = self.FAVLIST[:]
		if currbox[1] in BS.platlist:
			self.session.openWithCallback(self.ATVimageslistCB, ATVimageslist, currbox)

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
	def __init__(self, session, box):
		self.session = session
		self.currbox = box
		self.currplat = box[1]
		self.skin = self.readSkin("ATVimageslist")
		Screen.__init__(self, session, self.skin)
		self.setTitle(_("Images list"))
		self.boxlist = []
		self.htmldict = {}
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
		self["actions"] = ActionMap(["WizardActions", "DirectionActions", "MenuActions", "ChannelSelectBaseActions", "ColorActions"],
							{"ok": self.keyOk,
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
		BS.getbuildinfos(BS.platlist[self.platidx], callback=self.refreshCallback)

	def refreshCallback(self, htmldict):
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
				buildtime = self.roundMinutes(bd["BuildTime"].strip())
				synctime = self.roundMinutes(bd["SyncTime"].strip())
				menulist.append(tuple([bd["No"],boxname, bd["BuildStatus"], self.fmtDateTime(bd["StartBuild"]), self.fmtDateTime(bd["StartFeedSync"]), self.fmtDateTime(bd["EndBuild"]), synctime, buildtime, color]))
			self["menu"].updateList(menulist)
			self.boxlist = boxlist
		if self.currbox:
			foundbox = [item for item in boxlist if item == self.currbox]
			if foundbox:
				self["menu"].setIndex(self.boxlist.index(foundbox[0]))
			self.currbox = None
		self.updateStatus()

	def updateStatus(self):
		self.currindex = self["menu"].getSelectedIndex()
		if self.boxlist and self.currindex is not None:
			if [item for item in self.FAVLIST if item == self.boxlist[self.currindex]]:
				self["key_red"].setText(_("remove box from favorites"))
			else:
				self["key_red"].setText(_("add box to favorites"))
			currbox = self.boxlist[self.currindex][0]
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(currbox)
			if BS.findbuildbox():
				boxinfo = _("Next build ends in %s, still %s boxes ahead") % (f"{BS.strf_delta(nextbuild)[:5]} h", boxesahead)
			else:
				boxinfo = _("Server paused, unclear how many boxes are ahead...")
			buildstatus = BS.htmldict["boxinfo"][self.boxlist[self.currindex][0]]["BuildStatus"] if BS.htmldict else ""
			if nextbuild:
				self["boxinfo"].setText(boxinfo)
			elif buildstatus == "Building":
				self["boxinfo"].setText(_("Image is under construction, the duration is unclear..."))
			elif buildstatus == "Waiting":
				self["boxinfo"].setText(_("Image is waiting with priority, the duration is unclear..."))
			if cycletime:
				cycletime = f"{BS.strf_delta(cycletime)[:5]} h"
				self["platinfo"].setText("%s: %s, %s %s, %s: %s" % (_("last build cycle"), cycletime, counter, _("boxes"), _("failed"), failed))
			else:
				self["boxinfo"].setText(_("No box found in this platform!"))
				self["platinfo"].setText(_("Nothing to do - no build cycle"))
				self["menu"].setList([])

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
			currbox = self.boxlist[self.currindex] if self.boxlist else None
			if currbox:
				self.session.open(ATVboxdetails, currbox)

	def msgboxCB(self, answer):
		if answer is True and self.boxlist and self.currindex is not None:
			self.FAVLIST.remove(self.foundFavs[0])
			config.plugins.OpenATVstatus.favboxes.value = ";".join("(%s)" % ",".join(item) for item in self.FAVLIST) if self.FAVLIST else ""
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
				config.plugins.OpenATVstatus.favboxes.value = ";".join("(%s)" % ",".join(item) for item in self.FAVLIST) if self.FAVLIST else ""
				config.plugins.OpenATVstatus.favboxes.save()
				self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully added to favorites!") % self.boxlist[self.currindex], type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)
				self.refreshplatlist()

	def keyGreen(self):
		if self.boxlist:
			findbuildbox = (BS.findbuildbox(), self.currplat)
			if findbuildbox[0]:
				self["menu"].setIndex(self.boxlist.index(findbuildbox))
				self.updateStatus()
			else:
				self.session.open(MessageBox, text=_("At the moment no image is built on the platform '%s'!") % self.currplat, type=MessageBox.TYPE_INFO, timeout=5, close_on_any_key=True)

	def keyYellow(self):
		if self.boxlist and self.FAVLIST:
			self.favindex = (self.favindex + 1) % len(self.FAVLIST)
			self.currbox = self.FAVLIST[self.favindex]
			if self.currbox in self.boxlist:
				self["menu"].setIndex(self.boxlist.index(self.currbox))
				self.updateStatus()
			else:
				self.platidx = BS.platlist.index(self.currbox[1])
				self.CS.moveToIndex(self.platidx)
				self.setPlatformStatic()
				self.refreshplatlist()

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
		self.CS.stop()
		self.close()

	def openConfig(self):
		self.session.openWithCallback(self.openConfigCB, ATVconfig)

	def openConfigCB(self):
		self.makeimagelist()


class ATVboxdetails(Screen, ATVglobs):
	def __init__(self, session, box):
		self.session = session
		self.box = box
		self.skin = self.readSkin("ATVboxdetails")
		Screen.__init__(self, session, self.skin)
		self.setTitle(_("Boxdetails"))
		self["version"] = Label(self.VERSION)
		self["curr_date"] = Label(datetime.now().strftime("%x"))
		self["status"] = Label()
		self["picture"] = Pixmap()
		self["details"] = Label()
		self["key_red"] = Label(_("Cancel"))
		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"],
							{"ok": self.exit,
							"back": self.exit,
							"cancel": self.exit,
							"red": self.exit,
							}, -1)
		self.onLayoutFinish.append(self.onLayoutFinished)

	def onLayoutFinished(self):
		self["picture"].hide()
		self.picfile = join(self.TEMPPATH, "%s.png" % self.box[0])
		if exists(self.picfile):
			self.idownloadCB()
		else:
			callInThread(self.imageDownload, self.box[0])
		status = "offline"
		details = ""
		if self.box[0] == BoxInfo.getItem("BoxName"):
			status = "online"
			details += "%s:\t%s\n" % (_("Model"), BoxInfo.getItem("displaymodel"))
			details += "%s:\t%s\n" % (_("Brand"), BoxInfo.getItem("displaybrand"))
			details += "%s:\t%s\n" % (_("Image"), BoxInfo.getItem("displaydistro"))
			details += "%s:\t%s.%s\n" % (_("Version"), BoxInfo.getItem("imageversion"), BoxInfo.getItem("imgrevision"))
			details += "%s:\t%s\n" % (_("Chipset"), BoxInfo.getItem("socfamily"))
			self["details"].setText(details)
		else:
			streamurls = getPeerStreamingBoxes()
			if streamurls:
				streamurl = [x for x in streamurls if self.box[0] in x]  # example streamurls: ['http://gbue4k.local:8001', 'http://sf8008.local:8001']
				bd = self.getAPIdata("%s:80/api/about" % streamurl[0][:streamurl[0].rfind(":")]) if streamurl else None
				if bd and bd["info"]:
					status = "online"
					details += "%s:\t%s\n" % (_("Model"), bd.get("info", {}).get("model", ""))
					details += "%s:\t%s\n" % (_("Brand"), bd.get("info", {}).get("brand", ""))
					details += "%s:\t%s\n" % (_("Image"), bd.get("info", {}).get("friendlyimagedistro", ""))
					details += "%s:\t%s\n" % (_("Version"), bd.get("info", {}).get("imagever", ""))
					details += "%s:\t%s\n" % (_("Chipset"), "%sh" % bd.get("info", {}).get("chipset", ""))
					self["status"].setText("online")
				else:
					details += "%s:\t%s\n" % (_("Model"), self.box[0])
					details += "\n%s" % (_("Box is OFFLINE! No current details available"))
		self["status"].setText(status)
		self["details"].setText(details)

	def getAPIdata(self, apiurl):
		try:
			response = get(apiurl, timeout=(3.05, 6))
			response.raise_for_status()
			return loads(response.content)
		except exceptions.RequestException as error:
			print("[%s] ERROR in module 'getAPIdata': %s" % (self.MODULE_NAME, str(error)))

	def imageDownload(self, boxname):
		try:
			response = get(("%s%s.png" % (self.PICURL, boxname)).encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except exceptions.RequestException as error:
			print("[%s] ERROR in module 'imageDownload': %s" % (self.MODULE_NAME, str(error)))
		else:
			with open(join(self.TEMPPATH, "%s.png" % boxname), "wb") as f:
				f.write(response.content)
		self.idownloadCB()

	def idownloadCB(self):
		self["picture"].instance.setPixmapScaleFlags(BT_SCALE | BT_KEEP_ASPECT_RATIO | BT_HALIGN_CENTER | BT_VALIGN_CENTER)
		self["picture"].instance.setPixmapFromFile(self.picfile)
		self["picture"].show()

	def exit(self):
		self.close()


class ATVconfig(ConfigListScreen, Screen, ATVglobs):
	def __init__(self, session):
		skin = self.readSkin("ATVconfig")
		self.skin = skin
		Screen.__init__(self, session, skin)
		ConfigListScreen.__init__(self, [])
		self.setTitle(_("Settings"))
		self["version"] = Label(self.VERSION)
		self["curr_date"] = Label(datetime.now().strftime("%x"))
		self["key_red"] = Label(_("Cancel"))
		self["key_green"] = Label(_("Save settings"))
		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {"cancel": self.keyCancel,
																		  "red": self.keyCancel,
																		  "green": self.keyGreen
																		  }, -1)
		clist = []
		clist.append(getConfigListEntry(_("Preferred box architecture:"), config.plugins.OpenATVstatus.favarch, _("Specify which box architecture should be preferred when the images list will be called.")))
		clist.append(getConfigListEntry(_("Animation for change of platform:"), config.plugins.OpenATVstatus.animate, _("Sets the animation speed for the carousel function when changing platforms in images list.")))
		clist.append(getConfigListEntry(_("Time indication of 'NextBuild':"), config.plugins.OpenATVstatus.nextbuild, _("Show 'NextBuild' as relative time in hours or as absolute time.")))
		clist.append(getConfigListEntry(_("Time zone:"), config.plugins.OpenATVstatus.timezone, _("Show time as local time or as standard time (UTC) from server.")))
		clist.append(getConfigListEntry(_("Date format:"), config.plugins.OpenATVstatus.dateformat, _("Show date in desired format.")))
		self["config"].setList(clist)

	def keyGreen(self):
		config.plugins.OpenATVstatus.save()
		self.close()

	def keyCancel(self):
		for x in self["config"].list:
			x[1].cancel()
		self.close()


def main(session, **kwargs):
		session.open(ATVfavorites)


def autostart(reason, **kwargs):
	pass


def Plugins(**kwargs):
	return PluginDescriptor(name="OpenATV Status", icon="plugin.png", description=_("Current overview of the OpenATV images building servers"), where=PluginDescriptor.WHERE_PLUGINMENU, fnc=main)
