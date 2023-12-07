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
from datetime import timedelta
from getopt import getopt, GetoptError
from json import loads, dump
from re import search, findall, S, M
from requests import get, exceptions
from sys import exit, argv
from twisted.internet.reactor import callInThread

MODULE_NAME = __name__.split(".")[-1]


class Buildstatus():
	def __init__(self):
		self.error = None
		self.url = None
		self.htmldict = None
		self.callback = None
		self.archlist = []  # list of available architectures (=shortnames of plattforms)
		self.platlist = []  # list of available platforms (=longnames of platforms)
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
				self.platlist = list(self.platdict["versionurls"].keys())
				self.archlist = [x.split(" ")[0].upper() for x in self.platlist]  # get architecture (=shortname) from platform (=keyname)
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
		if not platform:
			self.error = "[%s] ERROR in module 'start': '%s" % (MODULE_NAME, "platform is None")
		self.url = self.platdict["versionurls"][platform]["url"]
		if callback:
			if self.error:
				callback()
			else:
				callInThread(self.createdict, callback)
		else:
			return None if self.error else self.createdict()

	def getplatform(self, currarch):  # get platform (=keyname) from currarch (=shortname)
		platform = None
		if self.platdict and currarch in self.archlist:
			for platform in self.platlist:
				if currarch.upper() in platform:
					break
		return platform

	def createdict(self, callback=None):  # coordinates 'get html-imagesdata & create imagesdict'
		htmldata = self.getpage()
		if htmldata:
			self.htmldict = self.htmlparse(htmldata)  # complete dict of all platform boxes
		else:
			self.htmldict = None
			self.error = "[%s] ERROR in module 'getpage': htmldata is None." % MODULE_NAME
		if callback:
			if not self.error:
				print("[%s] buildservers successfully accessed..." % MODULE_NAME)
			callback()
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
			print("[%s] WARNING in module 'evaluate': '%s" % (MODULE_NAME, "Box not found in this architecture. Try another architecture."))
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
	architectures = False
	platforms = False
	currarch = "ARM"
	filename = None
	boxname = None
	cycletime = None
	counter = 0
	failed = 0
	helpstring = "Buildstatus v1.2: try 'python Buildstatus.py -h' for more information"
	BS = Buildstatus()
	BS.start()  # interactive call without threading
	if BS.error:
		print(BS.error.replace(mainfmt, "").strip())
		exit()
	try:
		opts, args = getopt(argv, "a:j:e:bcvsph", ["architecture =", "json =", "evaluate =", "buildbox", "cycle", "verbose", "supported", "platforms", "help"])
	except GetoptError:
		print(helpstring)
		exit(2)
	if not opts:
		verbose = True
	for opt, arg in opts:
		opt = opt.lower().strip()
		arg = arg.lower().strip()
		if opt == "-h":
			print("Usage: python Buildstatus.py [options...] <data>\n"
			"-a, --architecture <data>\tUse architecture: %s {'arm' is default}\n"
			"-b, --buildbox\t\t\tShow the box for which currently built an image\n"
			"-c, --cycle\t\t\tShow the estimated duration of a complete build cycle\n"
			"-v, --verbose\t\t\tPerform with complete image build status overview\n"
			"-e, --evaluate <boxname>\tevaluates time until image will be build for desired box\n"
			"-s, --supported\t\t\tShow all currently supported architectures\n"
			"-p, --platforms\t\t\tShow all currently supported platforms\n"
			"-j, --json <filename>\t\tFile output formatted in JSON" % ", ".join(BS.archlist))
			exit()
		elif opt in ("-a", "--architecture"):
			currarch = arg.upper()
			verbose = True
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
			architectures = True
		elif opt in ("-p", "--platforms"):
			platforms = True
	currplat = BS.getplatform(currarch)
	if not currplat:
		print("ERROR in module 'main': unknown architecture. Allowed is: %s" % ", ".join(x.split(" ")[0].upper() for x in BS.archlist))
		exit()
	BS.getbuildinfos(currplat)
	if buildbox:
		buildboxname = BS.findbuildbox()
		if buildboxname:
			print("Currently the image is built for: '%s'" % buildboxname)
		if BS.error:
			print(BS.error.replace(mainfmt, "").strip())
			BS.error = None
	if evaluate:
		if boxname:
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(boxname)
			if nextbuild is not None:
				print("estimated time for next image '%s': %s h (%s boxes ahead)" % (boxname, BS.strf_delta(nextbuild), boxesahead))
			if BS.error:
				print(BS.error.replace(mainfmt, "").strip())
				BS.error = None
		else:
			print("ERROR in module 'main': missing boxname")
			exit()
	if cycle:
		if not cycletime:
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate()
		if cycletime:
			print("estimated durance of complete cycle (%s): %s h" % (currplat, BS.strf_delta(cycletime)))
		if BS.error:
			print(BS.error.replace(mainfmt, "").strip())
			BS.error = None
	if architectures:
		if BS.error:
			print(BS.error.replace(mainfmt, "").strip())
		if architectures:
			print("available architectures: %s" % ", ".join(x for x in BS.archlist))
		else:
			print("ERROR in module 'main': no architectures found")
	if platforms:
		if BS.error:
			print(BS.error.replace(mainfmt, "").strip())
		if platforms:
			print("available platforms: %s" % ", ".join(x for x in BS.platlist))
		else:
			print("ERROR in module 'main': no platforms found")

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


if __name__ == "__main__":
	main(argv[1:])
