#########################################################################################################
#                                                                                                       #
#  OpenATVbuildstatus: shows current build status of images and estimates time to next image build      #
#  Coded by Mr.Servo @ OpenATV (c) 2023                                                                 #
#  -----------------------------------------------------------------------------------------------------#
#  This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.    #
#  This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep       #
#  the license), but it may not be commercially distributed. Advertise with this tool is not allowed.   #
#  For other uses, permission from the authors is necessary.                                            #
#                                                                                                       #
#########################################################################################################

# PYTHON IMPORTS
from datetime import datetime
from json import loads
from os import makedirs
from os.path import join, exists
from requests import get, exceptions
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

# PLUGIN IMPORTS
from . import PLUGINPATH, _  # for localized messages
from .Buildstatus import Buildstatus

# PLUGIN GLOBALS
BS = Buildstatus()

config.plugins.OpenATVstatus = ConfigSubsection()
config.plugins.OpenATVstatus.animate = ConfigSelection(default="50", choices=[("0", _("off")), ("70", _("slower")), ("50", _("normal")), ("30", _("faster"))])
config.plugins.OpenATVstatus.favarch = ConfigSelection(default="current", choices=[("current", _("selected box"))] + BS.archlist)
config.plugins.OpenATVstatus.favboxes = ConfigText(default="", fixed_size=False)

VERSION = "V1.8"
MODULE_NAME = __name__.split(".")[-1]
FAVLIST = [tuple(x.strip() for x in item.replace("(", "").replace(")", "").split(",")) for item in config.plugins.OpenATVstatus.favboxes.value.split(";")] if config.plugins.OpenATVstatus.favboxes.value else []
PICURL = "https://raw.githubusercontent.com/oe-alliance/remotes/master/boxes/"
TMPPATH = "/tmp/boxpictures/"


def readSkin(skin):
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
				print("[Skin] Error: Unable to parse skin data in '%s' - '%s'!" % (skinfile, error))
	except OSError as error:
		print("[Skin] Error: Unexpected error opening skin file '%s'! (%s)" % (skinfile, error))
	return skintext


class Carousel():
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

	def start(self, choicelist, index, callback):
		if not choicelist:
			self.error = "[%s] ERROR in module 'start': choicelist is empty or None!" % MODULE_NAME
			return
		self.choicelist = choicelist
		self.callback = callback
		self.buildRotateList()
		self.moveToIndex(index)
		self.prevstr = self.rlist[0]
		self.currstr = self.rlist[1]
		self.nextstr = self.rlist[2]
		self.maxlen = max(len(self.prevstr), len(self.currstr), len(self.nextstr))

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
		self.stepcount = 0
		self.callactive = True
		self.carouselTimer = eTimer()
		self.carouselTimer.callback.append(self.turn)
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


class ATVfavorites(Screen):
	def __init__(self, session):
		self.session = session
		BS.start()
		self.skin = readSkin("ATVfavorites")
		Screen.__init__(self, session, self.skin)
		self.setTitle(_("Favorites"))
		self.boxlist = []
		self.foundFavs = []
		self.platdict = dict()
		self.currindex = 0
		self["version"] = Label(VERSION)
		self["curr_date"] = Label(datetime.now().strftime("%x"))
		self["platinfo"] = Label()
		self["key_red"] = Label(_("remove box from favorites"))
		self["key_blue"] = Label(_("Images list"))
		self["key_ok"] = Label(_("Boxdetails"))
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
														"menu": self.openConfig,
													}, -1)
		self.onLayoutFinish.append(self.onLayoutFinished)
		makedirs(TMPPATH, exist_ok=True)

	def onLayoutFinished(self):
		self["menu"].setList([])
		callInThread(self.createMenulist)

	def createMenulist(self):
		boxlist = []
		usedarchs = []
		baselist = []
		piclist = []
		menulist = []
		if FAVLIST and BS.platlist:
			self["menu"].style = "default"
			for favorite in FAVLIST:
				if favorite[1] not in usedarchs:
					usedarchs.append(favorite[1])
			for currarch in usedarchs:
				currplat = [plat for plat in BS.platlist if currarch.upper() in plat][0]
				BS.getbuildinfos(currplat)
				if BS.htmldict:
					for box in [item for item in FAVLIST if item[1] in set([item[1]])]:
						if box[1] in currarch and box[0] in BS.htmldict["boxinfo"]:
							boxlist.append((box[0], currarch))
							bd = BS.htmldict["boxinfo"][box[0]]
							palette = {"Building": 0x00B028, "Failed": 0xFF0400, "Complete": 0xFFFFFF, "Waiting": 0xFFAE00}
							color = palette.get(bd["BuildStatus"], 0xB0B0B0)
							nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(box[0])
							if box[1] not in self.platdict:
								self.platdict[currplat] = dict()
								self.platdict[currplat]["cycletime"] = BS.strf_delta(cycletime)
								self.platdict[currplat]["boxcounter"] = "%s" % counter
								self.platdict[currplat]["boxfailed"] = "%s" % failed
							nextbuild = "%sh" % BS.strf_delta(nextbuild) if nextbuild else ""
							buildtime = bd["BuildTime"].strip()
							buildtime = "%sh" % buildtime if buildtime else ""
							textlist = [box[0], box[1], bd["BuildStatus"], nextbuild, "%s" % boxesahead, bd["StartBuild"], bd["EndBuild"], buildtime, color]
							baselist.append(textlist)
							picfile = join(TMPPATH, "%s.png" % box[0])
							if exists(picfile):
								pixmap = LoadPixmap(cached=True, path=picfile)
							else:
								pixmap = None
								piclist.append(box[0])
							menulist.append(tuple(textlist + [pixmap]))
							self["menu"].updateList(menulist)
			self.baselist = baselist
			self.boxlist = boxlist
			for picname in piclist:
				callInThread(self.imageDownload, picname)
		else:
			self["menu"].style = "emptylist"
			self["menu"].updateList([(_("No favorites (box, platform) set yet."), _("Please select favorite(s) in the image lists."))])
		self["menu"].setIndex(self.currindex)
		self.refreshstatus()

	def imageDownload(self, boxname):
		try:
			response = get(("%s%s.png" % (PICURL, boxname)).encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except exceptions.RequestException as error:
			print("[%s] ERROR in module 'imageDownload': %s" % (MODULE_NAME, str(error)))
		else:
			with open(join(TMPPATH, "%s.png" % boxname), "wb") as f:
				f.write(response.content)
		self.downloadCallback()

	def downloadCallback(self):
		menulist = []
		for textlist in self.baselist:
			menulist.append(tuple(textlist + [LoadPixmap(cached=True, path=join(TMPPATH, "%s.png" % textlist[0]))]))
		self["menu"].updateList(menulist)

	def refreshstatus(self):
		if FAVLIST:
			self.currindex = self["menu"].getSelectedIndex()
			if self.boxlist and self.currindex is not None:
				currplat = BS.getplatform(self.boxlist[self.currindex][1])
				platdict = self.platdict[currplat]
				self["platinfo"].setText("%s: %s, %s: %sh, %s %s, %s: %s" % (_("platform"), currplat, _("last build cycle"), platdict["cycletime"], platdict["boxcounter"], _("boxes"), _("failed"), platdict["boxfailed"]))

	def msgboxReturn(self, answer):
		if answer is True and self.boxlist and self.currindex is not None:
			FAVLIST.remove(self.foundFavs[0])
			config.plugins.OpenATVstatus.favboxes.value = ";".join("(%s)" % ",".join(item) for item in FAVLIST) if FAVLIST else ""
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
			self.foundFavs = [item for item in FAVLIST if item == self.boxlist[self.currindex]]
			if self.foundFavs:
				self.session.openWithCallback(self.msgboxReturn, MessageBox, _("Do you really want to remove Box '%s-%s' from favorites?") % self.boxlist[self.currindex], MessageBox.TYPE_YESNO, default=False)

	def keyBlue(self):
		if BS.archlist:
			if self.boxlist and self.currindex is not None:
				currbox = self.boxlist[self.currindex]
				if currbox:
					self.session.openWithCallback(self.createMenulist, ATVimageslist, currbox)
			else:
				currarch = BS.archlist[0] if config.plugins.OpenATVstatus.favarch.value == "current" else config.plugins.OpenATVstatus.favarch.value
				self.session.openWithCallback(self.createMenulist, ATVimageslist, ("", currarch))

	def keyUp(self):
		self["menu"].up()
		self.refreshstatus()

	def keyDown(self):
		self["menu"].down()
		self.refreshstatus()

	def keyPageUp(self):
		self["menu"].pageUp()
		self.refreshstatus()

	def keyPageDown(self):
		self["menu"].pageDown()
		self.refreshstatus()

	def keyTop(self):
		self["menu"].top()
		self.refreshstatus()

	def keyBottom(self):
		self["menu"].bottom()
		self.refreshstatus()

	def exit(self):
		BS.stop()
		self.close()

	def openConfig(self):
		self.session.open(ATVconfig)


class ATVimageslist(Screen):
	def __init__(self, session, box):
		self.session = session
		self.currbox = box
		self.currarch = box[1]
		self.skin = readSkin("ATVimageslist")
		Screen.__init__(self, session, self.skin)
		self.setTitle(_("Images list"))
		self.boxlist = []
		self.platidx = BS.archlist.index(self.currarch)
		self.currindex = 0
		self.favindex = 0
		self.foundFavs = []
		self["prev_plat"] = Label()
		self["curr_plat"] = Label()
		self["next_plat"] = Label()
		self["prev_label"] = Label()
		self["curr_label"] = Label()
		self["next_label"] = Label()
		self["version"] = Label(VERSION)
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
									 "ColorActions"], {"ok": self.keyOk,
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
														"menu": self.openConfig,
													}, -1)
		self.CS = Carousel(delay=int(config.plugins.OpenATVstatus.animate.value))
		self.CS.start(BS.platlist, self.platidx, self.CarouselCallback)
		self.onLayoutFinish.append(self.onLayoutFinished)

	def onLayoutFinished(self):
		self["prev_label"].setText(_("previous"))
		self["curr_label"].setText(_("current platform"))
		self["next_label"].setText(_("next"))
		self["menu"].setList([])
		self.setPlatformStatic()
		self.refreshplatlist()

	def refreshplatlist(self):
		self.currarch = BS.archlist[self.platidx]
		BS.getbuildinfos(BS.platlist[self.platidx], self.makeimagelist)

	def makeimagelist(self):
		menulist = []
		boxlist = []
		if BS.htmldict:
			for boxname in BS.htmldict["boxinfo"]:
				boxlist.append((boxname, self.currarch))
				bd = BS.htmldict["boxinfo"][boxname]
				palette = {"Building": 0x00B028, "Failed": 0xFF0400, "Complete": 0xB0B0B0, "Waiting": 0xFFAE00}
				color = 0xFDFf00 if [item for item in FAVLIST if item == (boxname, self.currarch)] else palette.get(bd["BuildStatus"], 0xB0B0B0)
				buildtime = bd["BuildTime"].strip()
				buildtime = "%sh" % buildtime if buildtime else ""
				menulist.append(tuple([boxname, bd["BuildStatus"], bd["StartBuild"], bd["StartFeedSync"], bd["EndBuild"], bd["SyncTime"], buildtime, color]))
			self["menu"].updateList(menulist)
			self.boxlist = boxlist
		if self.currbox:
			foundbox = [item for item in boxlist if item == self.currbox]
			if foundbox:
				self["menu"].setIndex(self.boxlist.index(foundbox[0]))
			self.currbox = None
		self.refreshstatus()

	def refreshstatus(self):
		self.currindex = self["menu"].getSelectedIndex()
		if self.boxlist and self.currindex is not None:
			if [item for item in FAVLIST if item == self.boxlist[self.currindex]]:
				self["key_red"].setText(_("remove box from favorites"))
			else:
				self["key_red"].setText(_("add box to favorites"))
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(self.boxlist[self.currindex][0])
			if nextbuild:
				self["boxinfo"].setText(_("next build ends in %sh, still %s boxes before") % (BS.strf_delta(nextbuild), boxesahead))
			else:
				self["boxinfo"].setText(_("image is under construction, the duration is unclear..."))
			if cycletime:
				self["platinfo"].setText("%s: %sh, %s %s, %s: %s" % (_("last build cycle"), BS.strf_delta(cycletime), counter, _("boxes"), _("failed"), failed))
			else:
				self["boxinfo"].setText(_("no box found in this platform!"))
				self["platinfo"].setText(_("nothing to do - no build cycle"))
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

	def CarouselCallback(self, rotated):
		self["prev_plat"].setText(rotated[0])
		self["curr_plat"].setText(rotated[1])
		self["next_plat"].setText(rotated[2])

	def keyOk(self):
		if self.boxlist and self.currindex is not None:
			currbox = self.boxlist[self.currindex] if self.boxlist else None
			if currbox:
				self.session.open(ATVboxdetails, currbox)

	def msgboxReturn(self, answer):
		if answer is True and self.boxlist and self.currindex is not None:
			FAVLIST.remove(self.foundFavs[0])
			config.plugins.OpenATVstatus.favboxes.value = ";".join("(%s)" % ",".join(item) for item in FAVLIST) if FAVLIST else ""
			config.plugins.OpenATVstatus.favboxes.save()
			self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully removed from favorites!") % self.boxlist[self.currindex], type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)
			self.refreshplatlist()

	def keyRed(self):
		if self.boxlist and self.currindex is not None:
			self.foundFavs = [item for item in FAVLIST if item == self.boxlist[self.currindex]]
			if self.foundFavs:
				self.session.openWithCallback(self.msgboxReturn, MessageBox, _("Do you really want to remove Box '%s-%s' from favorites?") % self.boxlist[self.currindex], MessageBox.TYPE_YESNO, default=False)
			else:
				FAVLIST.append(self.boxlist[self.currindex])
				config.plugins.OpenATVstatus.favboxes.value = ";".join("(%s)" % ",".join(item) for item in FAVLIST) if FAVLIST else ""
				config.plugins.OpenATVstatus.favboxes.save()
				self.session.open(MessageBox, text=_("Box '%s-%s' was sucessfully added to favorites!") % self.boxlist[self.currindex], type=MessageBox.TYPE_INFO, timeout=2, close_on_any_key=True)
				self.refreshplatlist()

	def keyGreen(self):
		if self.boxlist:
			findbuildbox = (BS.findbuildbox(), self.currarch)
			if findbuildbox[0]:
				self["menu"].setIndex(self.boxlist.index(findbuildbox))
				self.refreshstatus()
			else:
				self.session.open(MessageBox, text=_("At the moment no image is built on the platform '%s'!") % BS.getplatform(self.currarch), type=MessageBox.TYPE_INFO, timeout=5, close_on_any_key=True)

	def keyYellow(self):
		if self.boxlist and FAVLIST:
			self.favindex = (self.favindex + 1) % len(FAVLIST)
			self.currbox = FAVLIST[self.favindex]
			if self.currbox in self.boxlist:
				self["menu"].setIndex(self.boxlist.index(self.currbox))
				self.refreshstatus()
			else:
				self.platidx = BS.archlist.index(self.currbox[1])
				self.CS.moveToIndex(self.platidx)
				self.setPlatformStatic()
				self.refreshplatlist()

	def keyUp(self):
		self["menu"].up()
		self.refreshstatus()

	def keyDown(self):
		self["menu"].down()
		self.refreshstatus()

	def keyPageUp(self):
		self["menu"].pageUp()
		self.refreshstatus()

	def keyPageDown(self):
		self["menu"].pageDown()
		self.refreshstatus()

	def keyTop(self):
		self["menu"].top()
		self.refreshstatus()

	def keyBottom(self):
		self["menu"].bottom()
		self.refreshstatus()

	def exit(self):
		BS.stop()
		self.CS.stop()
		self.close()

	def openConfig(self):
		self.session.open(ATVconfig)


class ATVboxdetails(Screen):
	def __init__(self, session, box):
		self.session = session
		self.box = box
		self.skin = readSkin("ATVboxdetails")
		Screen.__init__(self, session, self.skin)
		self.setTitle(_("Boxdetails"))
		self["version"] = Label(VERSION)
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
		self.picfile = join(TMPPATH, "%s.png" % self.box[0])
		if exists(self.picfile):
			self.downloadCallback()
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
			print("[%s] ERROR in module 'getAPIdata': %s" % (MODULE_NAME, str(error)))

	def imageDownload(self, boxname):
		try:
			response = get(("%s%s.png" % (PICURL, boxname)).encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except exceptions.RequestException as error:
			print("[%s] ERROR in module 'imageDownload': %s" % (MODULE_NAME, str(error)))
		else:
			with open(join(TMPPATH, "%s.png" % boxname), "wb") as f:
				f.write(response.content)
		self.downloadCallback()

	def downloadCallback(self):
		self["picture"].instance.setPixmapScaleFlags(BT_SCALE | BT_KEEP_ASPECT_RATIO | BT_HALIGN_CENTER | BT_VALIGN_CENTER)
		self["picture"].instance.setPixmapFromFile(self.picfile)
		self["picture"].show()

	def exit(self):
		self.close()


class ATVconfig(ConfigListScreen, Screen):
	def __init__(self, session):
		skin = readSkin("ATVconfig")
		self.skin = skin
		Screen.__init__(self, session, skin)
		self.setTitle(_("Settings"))
		self["version"] = Label(VERSION)
		self["curr_date"] = Label(datetime.now().strftime("%x"))
		self["key_red"] = Label(_("Cancel"))
		self["key_green"] = Label(_("Save settings"))
		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {"cancel": self.keyCancel,
																		  "red": self.keyCancel,
																		  "green": self.keyGreen
																		  }, -1)
		self.clist = []
		ConfigListScreen.__init__(self, self.clist)
		self.clist.append(getConfigListEntry(_("Preferred box architecture for images list:"), config.plugins.OpenATVstatus.favarch, _("Specify which box architecture should be preferred when images list will be called. If option 'current' is selected, the architecture of the selected box is taken.")))
		self.clist.append(getConfigListEntry(_("Animation for change of platform:"), config.plugins.OpenATVstatus.animate, _("Sets the animation speed for the carousel function when changing platforms.")))
		self["config"].setList(self.clist)

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
