#########################################################################################################
#                                                                                                       #
#  Buildstatus for openATV is a multiplatform tool (runs on Enigma2 & Windows and probably many others) #
#  Coded by Mr.Servo @ openATV (c) 2023                                                                 #
#  Learn more about the tool by running it in the shell: "python Buildstatus.py -h"                     #
#  -----------------------------------------------------------------------------------------------------#
#  This plugin is licensed under the GNU version 3.0 <https://www.gnu.org/licenses/gpl-3.0.en.html>.    #
#  This plugin is NOT free software. It is open source, you are allowed to modify it (if you keep       #
#  the license), but it may not be commercially distributed. Advertise with this tool is not allowed.   #
#  For other uses, permission from the authors is necessary.                                            #
#                                                                                                       #
#########################################################################################################

# PYTHON IMPORTS
from datetime import datetime, timedelta
from getopt import getopt, GetoptError
from json import loads, dump
from re import search, findall, S, M
from requests import get, exceptions
from sys import exit, argv
from twisted.internet.reactor import callInThread

MODULE_NAME = __name__.split(".")[-1]


class Buildstatus():
	def __init__(self):
		self.url = None
		self.error = None
		self.htmldict = None
		self.callback = None
		self.archlist = []  # list of available architectures with extension '_oldest' or '_latest'
		self.platlist = []  # list of available platforms
		self.platdict = {}  # dict of available platforms and relating urls

	def start(self):  # loads json-platformdata from build server
		try:
			response = get("http://api.mynonpublic.com/content.json".encode(), timeout=(3.05, 6))
			response.raise_for_status()
		except exceptions.RequestException as err:
			self.error = "[%s] ERROR in module 'start': '%s" % (MODULE_NAME, str(err))
			return {}
		try:
			dictdata = loads(response.content)
			if dictdata:
				self.platdict = dictdata
				self.platlist = sorted(list(self.platdict["versionurls"].keys()))
				helplist = [x.split(" ")[0].lower() for x in self.platlist]
				archlist = []
				for arch in helplist:  # separate dupes in platforms in "latest" and "oldest"
					if helplist.count(arch) > 1:
						release = "oldest" if "%s_latest" % arch in archlist else "latest"
					else:
						release = "latest"
					archlist.append("%s_%s" % (arch.lower(), release))
				self.archlist = sorted(list(set(archlist)))
				return dictdata
			self.error = "[%s] ERROR in module 'start': server access failed." % MODULE_NAME
		except Exception as err:
			self.error = "[%s] ERROR in module 'start': invalid json data from server. %s" % (MODULE_NAME, str(err))
		return {}

	def stop(self):
		self.callback = None
		self.error = None

	def getpage(self):  # loads html-imagedata from build server
		self.error = None
		if self.url:
			if self.callback:
				print("[%s] accessing buildservers for data..." % MODULE_NAME)
			try:
				response = get(self.url.encode(), timeout=(3.05, 6))
				response.raise_for_status()
			except exceptions.RequestException as err:
				self.error = "[%s] ERROR in module 'getpage': '%s" % (MODULE_NAME, str(err))
				return
			try:
				htmldata = response.content.decode()
				if htmldata:
					return htmldata
				self.error = "[%s] ERROR in module 'getpage': server access failed." % MODULE_NAME
			except Exception as err:
				self.error = "[%s] ERROR in module 'getpage': invalid data from server %s" % (MODULE_NAME, str(err))
		else:
			self.error = "[%s] ERROR in module 'getpage': missing url" % MODULE_NAME

	def getbuildinfos(self, platform, callback=None):  # loads imagesdata from build server
		self.callback = callback
		self.error = None
		if platform in self.platlist:
			self.url = self.platdict["versionurls"][platform]["url"]
		else:
			self.url = None
			self.error = "[%s] ERROR in module 'getbuildinfos': '%s" % (MODULE_NAME, "invalid platform '%s'" % platform)
			return {}
		if callback:
			callInThread(self.createdict, callback)
		else:
			return self.createdict()

	def getplatform(self, currarch):  # get platform from architecture
		archparts = currarch.split("_")
		if len(archparts) == 1:  # old shortnames with missing extension? (for compatibiliy reasons only)
			archparts.append("oldest")
		platform = None
		if self.platdict and archparts[1] in ["oldest", "latest"]:
			hitlist = []
			for tempplat in self.platlist:
				if archparts[0].upper() in tempplat:
					hitlist.append(tempplat)  # add current platforms to hitlist
			if hitlist:
				platform = hitlist[-1] if archparts[1] == "latest" else hitlist[0]
		return platform

	def createdict(self, callback=None):  # coordinates 'get html-imagesdata & create imagesdict'
		htmldata = self.getpage()
		if htmldata:
			self.htmldict = self.htmlparse(htmldata)  # complete dict of all platform boxes
		else:
			self.htmldict = None
			self.error = "[%s] ERROR in module 'createdict': htmldata is None." % MODULE_NAME
		if callback:
			if not self.error:
				print("[%s] buildservers successfully accessed..." % MODULE_NAME)
			callback(None if self.error else self.htmldict)
		return None if self.error else self.htmldict

	def htmlparse(self, htmldata):  # parse html-imagesdata & create imagesdict
		htmldict = dict()
		title = search(r'<title>(.*?)</title>', htmldata)
		headline = findall(r"<th>(.*?)</th>", str(findall(r'<thead>\s*<tr>(.*?)</tr>\s*</thead>', htmldata, flags=S)))
		htmldict["headline"] = ", ".join(headline)
		htmldict["title"] = title.group(1) if title else ""
		versionnames = findall(r'">(.*?)</button>', htmldata)
		versionurls = findall(r"location.href='(.*?)'", htmldata)
		htmldict["versionurls"] = dict()
		for idx, version in enumerate(versionnames):
			htmldict["versionurls"][version] = dict()
			htmldict["versionurls"][version]["url"] = versionurls[idx]
		datablocks = search(r"<tbody>(.*?)</tbody>", htmldata, flags=S)
		datablocks = datablocks.group(1) if datablocks else None
		datablocks = findall(r"\s*<tr>(.*?)</tr>\s*", datablocks, flags=S) if datablocks else []
		htmldict["boxinfo"] = dict()
		for datablock in datablocks:
			boxinfo = findall(r'<td\s*class="(.*?)">(.*?)</td>', datablock, flags=M)
			dateset = findall(r'<td>(.*?)</td>', datablock)
			boxname = boxinfo[0][1]
			htmldict["boxinfo"][boxname] = dict()  # boxname
			htmldict["boxinfo"][boxname]["BoxNameClass"] = boxinfo[0][0]
			htmldict["boxinfo"][boxname]["BuildStatus"] = boxinfo[1][1]
			htmldict["boxinfo"][boxname]["BuildClass"] = boxinfo[1][0]
			htmldict["boxinfo"][boxname]["StartBuild"] = dateset[0]
			htmldict["boxinfo"][boxname]["StartFeedSync"] = dateset[1]
			htmldict["boxinfo"][boxname]["EndBuild"] = dateset[2]
			htmldict["boxinfo"][boxname]["SyncTime"] = dateset[3]
			htmldict["boxinfo"][boxname]["BuildTime"] = dateset[4]
		return htmldict

	def findbuildbox(self):  # find boxname current image is build for
		if self.htmldict is None:
			self.error = "[%s] ERROR in module 'findbuildbox': '%s" % (MODULE_NAME, "self.htmldict is None")
			return
		hit = None
		boxinfo = self.htmldict["boxinfo"]
		for boxname in list(boxinfo.keys()):
			if "Building" in boxinfo[boxname]["BuildStatus"]:
				hit = boxname
				break
		return hit

	def evaluate(self, box=None):  # evaluate box data
		if self.htmldict is None:
			self.error = "[%s] ERROR in module 'evaluate': '%s" % (MODULE_NAME, "self.htmldict is None")
			return None, 0, None, 0, 0
		buildbox = self.findbuildbox()
		boxinfo = self.htmldict["boxinfo"]
		nextbuild = timedelta()
		cycletime = timedelta()
		boxesahead = 0
		boxcounter = 0
		collect = True
		foundbox = False
		failed = 0
		for boxname in list(boxinfo.keys()):
			time = boxinfo[boxname]["BuildTime"].strip().split(":")
			if len(time) < 3:
				time = [0, 0, 0]
			if boxname == buildbox:  # aktuell gebaute Box
				collect = True
				if not foundbox:
					nextbuild = timedelta()  # reset
					boxesahead = 0
			else:
				h, m, s = time
				cycletime += timedelta(hours=int(h), minutes=int(m), seconds=int(s))
			if collect and len(time) > 1:
				h, m, s = time
				nextbuild += timedelta(hours=int(h), minutes=int(m), seconds=int(s))
				boxesahead += 1
			if boxname == box:  # eigener Boxname
				foundbox = True
				collect = False
			if "Failed" in boxinfo[boxname]["BuildStatus"]:
				failed += 1
			boxcounter += 1
		if box is not None and not foundbox:
			self.error = "[%s] WARNING in module 'evaluate': '%s'" % (MODULE_NAME, "Box not found in this platform. Try another platform.")
			return timedelta(), 0, cycletime, boxcounter, failed
		return nextbuild, boxesahead - 1, cycletime, boxcounter, failed

	def strf_delta(self, td):  # converts deltatime-format in hours (e.g. '2 days, 01:00' in '49:00:00')
		h, r = divmod(int(td.total_seconds()), 60 * 60)
		m, s = divmod(r, 60)
		h, m, s = (str(x).zfill(2) for x in (h, m, s))
		return f"{h}:{m}:{s}"


def main(argv):  # shell interface
	mainfmt = "[__main__]"
	buildbox = False
	cycle = False
	evaluate = False
	verbose = False
	architecture = False
	supported = False
	usable = False
	filename = None
	boxname = None
	cycletime = None
	currarch = "arm_latest"
	currplat = ""
	counter = 0
	failed = 0
	helpstring = "Buildstatus v1.2: try 'python Buildstatus.py -h' for more information"
	BS = Buildstatus()
	if BS.error:
		print("Error: %s" % BS.error.replace(mainfmt, "").strip())
		exit()
	try:
		opts, args = getopt(argv, "a:p:j:e:bcvsuh", ["architecture =", "platform=", "json =", "evaluate =", "buildbox", "cycle", "verbose", "supported", "usable", "help"])
	except GetoptError as error:
		print("Error: %s\n%s" % (error, helpstring))
		exit(2)
	if not opts:
		verbose = True
	for opt, arg in opts:
		opt = opt.lower().strip()
		arg = arg.lower().strip()
		if opt == "-h":
			print("Usage  : python Buildstatus.py [options...] <data>\n"
			"Example: python Buildstatus.py -a arm_latest -v -e gbue4k -s -u\n"
			"-a, --architecture <data>\tUse architecture: %s\n"
			"-p, --platform <data>\t\tUse platform: %s\n"
			"-b, --buildbox\t\t\tShow the box for which currently built an image\n"
			"-c, --cycle\t\t\tShow the estimated duration of a complete build cycle\n"
			"-v, --verbose\t\t\tPerform with complete image build status overview\n"
			"-e, --evaluate <boxname>\tEvaluates time until image will be build for desired box\n"
			"-s, --supported\t\t\tShow all currently supported architectures\n"
			"-u, --usable\t\t\tShow all currently usable platforms\n"
			"-j, --json <filename>\t\tFile output formatted in JSON")
			exit()
		if opt in ("-a", "--architecture"):
			currarch = arg.lower()
		elif opt in ("-p", "--platform"):
			currplat = arg.upper()
		elif opt in ("-j", "--json"):
			filename = arg
		elif opt in ("-b", "--buildbox"):
			buildbox = True
		elif opt in ("-c", "--cycle"):
			cycle = True
		elif opt in ("-e", "--evaluate"):
			boxname = arg
			evaluate = True
		elif opt in ("-v", "--verbose"):
			verbose = True
		elif opt in ("-s", "--supported"):
			supported = True
		elif opt in ("-u", "--usable"):
			usable = True
	BS.start()  # interactive call without threading
	archlist = BS.archlist
	platlist = BS.platlist
	if not currplat:
		currplat = BS.getplatform(currarch)
		if BS.error:
			print("Error: %s" % BS.error.replace(mainfmt, "").strip())
			exit()
	if not currarch:
		print("Unknown architecture '%s'. Supported is: %s" % (currarch, ", ".join(x.split(" ")[0] for x in archlist)))
		exit()
	currplat = currplat.replace("_", " ")
	if currplat not in platlist:
		print("Unknown platform '%s'. Supported is: %s" % (currplat.replace(" ", "_"), ", ".join(x.replace(' ', '_') for x in platlist)))
		exit()
	BS.getbuildinfos(currplat)
	if BS.error:
		print("Error: %s" % BS.error.replace(mainfmt, "").strip())
		exit()
	if BS.htmldict and verbose:
		separator = "+--------------------+--------------+----------------------+----------------------+----------------------+-----------+------------+"
		row = "| {0:<18} | {1:<12} | {2:<20} | {3:<20} | {4:<20} | {5:<9} | {6:<10} |"
		print("%s%s%s" % ("+", "-" * 129, "+"))
		print("| {0:<128}|".format(BS.htmldict["title"]))
		print(separator)
		print(row.format(*BS.htmldict["headline"].split(", ")))
		print(separator)
		for counter, box in enumerate(BS.htmldict["boxinfo"]):
			bi = BS.htmldict["boxinfo"][box]
			print(row.format(box, bi["BuildStatus"].rjust(12), bi["StartBuild"], bi["StartFeedSync"], bi["EndBuild"], bi["SyncTime"].rjust(9), bi["BuildTime"].rjust(10)))
		print(separator)
		print("| {0:<50}{1:<48}{2:<30}|".format("current platform: %s" % currplat.upper(), "boxes found: %s" % counter, "building errors found: %s" % str(failed).rjust(3)))
		print("%s%s%s" % ("+", "-" * 129, "+"))
	if BS.htmldict and filename:
		with open(filename, "w") as f:
			dump(BS.htmldict, f)
		print("File '%s' was successfully created." % filename)
	if buildbox:
		buildboxname = BS.findbuildbox()
		if BS.error:
			print("Error: %s" % BS.error.replace(mainfmt, "").strip())
			exit()
		if buildboxname:
			print("Currently the image is built for: '%s'" % buildboxname)
		else:
			print("At the moment no image is built on the platform!")
	if evaluate:
		if boxname:
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(boxname)
			if BS.error:
				print("Error: %s" % BS.error.replace(mainfmt, "").strip())
				exit()
			if nextbuild:
				print("Estimated duration for next image for '%s' in %sh at %s (%s boxes ahead) " % (boxname, BS.strf_delta(nextbuild), (datetime.now() + nextbuild).strftime("%Y/%m/%d, %H:%M:%S"), boxesahead))
			else:
				print("Server paused, unclear how many boxes are ahead!")
		else:
			print("Missing boxname")
			exit()
	if cycle:
		if not cycletime:
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate()
			if BS.error:
				print("Error: %s" % BS.error.replace(mainfmt, "").strip())
				exit()
		print("Estimated durance of complete cycle (%s): %s h" % (currplat, BS.strf_delta(cycletime)))
	if supported:
		if not architecture and archlist:
			print("Available architectures: %s" % ", ".join(x for x in archlist))
		else:
			print("No architectures found")
	if usable:
		if platlist:
			print("Available platforms: %s" % ", ".join(x.lower().replace(" ", "_") for x in platlist))
		else:
			print("No platforms found")


if __name__ == "__main__":
	main(argv[1:])
