#########################################################################################################
#                                                                                                       #
#  Buildstatus for openATV is a multiplatform tool (runs on Enigma2 & Windows and probably many others) #
#  Coded by Mr.Servo @ openATV (c) 2023-2025                                                            #
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


class Buildstatus:
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
			response = get("http://api.mynonpublic.com/content.json", timeout=(3.05, 6))
			response.raise_for_status()
		except exceptions.RequestException as err:
			self.error = f"[{MODULE_NAME}] ERROR in module 'start': '{str(err)}"
			return {}
		try:
			dictdata = loads(response.text)
			if dictdata:
				self.platdict = dictdata
				self.platlist = sorted(self.platdict["versionurls"].keys())
				helplist = [x.split(" ")[0].lower() for x in self.platlist]
				archlist = []
				for arch in helplist:  # separate dupes in platforms in "latest" and "oldest"
					if helplist.count(arch) > 1:
						release = "oldest" if f"{arch}_latest" in archlist else "latest"
					else:
						release = "latest"
					archlist.append(f"{arch.lower()}_{release}")
				self.archlist = sorted(set(archlist))
				return dictdata
			self.error = f"[{MODULE_NAME}] ERROR in module 'start': server access failed."
		except Exception as err:
			self.error = f"[{MODULE_NAME}] ERROR in module 'start': invalid json data from server. {str(err)}"
		return {}

	def stop(self):
		self.callback = None
		self.error = None

	def getpage(self):  # loads html-imagedata from build server
		self.error = None
		if self.url:
			if self.callback:
				print(f"[{MODULE_NAME}] accessing buildservers for data...")
			try:
				response = get(self.url, timeout=(3.05, 6))
				response.raise_for_status()
			except exceptions.RequestException as err:
				self.error = f"[{MODULE_NAME}] ERROR in module 'getpage': '{str(err)}"
				return
			try:
				htmldata = response.text
				if htmldata:
					return htmldata
				self.error = f"[{MODULE_NAME}] ERROR in module 'getpage': server access failed."
			except Exception as err:
				self.error = f"[{MODULE_NAME}] ERROR in module 'getpage': invalid data from server {str(err)}"
		else:
			self.error = f"[{MODULE_NAME}] ERROR in module 'getpage': missing url"

	def getbuildinfos(self, platform, callback=None):  # loads imagesdata from build server
		self.callback = callback
		self.error = None
		if platform in self.platlist:
			self.url = self.platdict["versionurls"][platform]["url"]
		else:
			self.url = None
			self.error = f"[{MODULE_NAME}] ERROR in module 'getbuildinfos': invalid platform: {platform})"
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
			self.error = f"[{MODULE_NAME}] ERROR in module 'createdict': htmldata is None."
		if callback:
			if not self.error:
				print(f"[{MODULE_NAME}] buildservers successfully accessed...")
			callback(None if self.error else self.htmldict)
		return None if self.error else self.htmldict

	def htmlparse(self, htmldata):  # parse html-imagesdata & create imagesdict
		htmldict = {}
		title = search(r'<title>(.*?)</title>', htmldata)
		headline = findall(r"<th>(.*?)</th>", str(findall(r'<thead>\s*<tr>(.*?)</tr>\s*</thead>', htmldata, flags=S)))
		htmldict["headline"] = ", ".join(headline)
		htmldict["title"] = title.group(1) if title else ""
		versionnames = findall(r'">(.*?)</button>', htmldata)
		versionurls = findall(r"location.href='(.*?)'", htmldata)
		htmldict["versionurls"] = {}
		for idx, version in enumerate(versionnames):
			htmldict["versionurls"][version] = {}
			htmldict["versionurls"][version]["url"] = versionurls[idx]
		datablocks = search(r"<tbody>(.*?)</tbody>", htmldata, flags=S)
		datablocks = datablocks.group(1) if datablocks else None
		datablocks = findall(r"\s*<tr>(.*?)</tr>\s*", datablocks, flags=S) if datablocks else []
		htmldict["boxinfo"] = {}
		for datablock in datablocks:
			boxinfo = findall(r'<td\s*class="(.*?)">(.*?)</td>', datablock, flags=M)
			dateset = findall(r'<td>(.*?)</td>', datablock)
			boxname = boxinfo[1][1]
			htmldict["boxinfo"][boxname] = {}  # boxname
			htmldict["boxinfo"][boxname]["No"] = boxinfo[0][1]
			htmldict["boxinfo"][boxname]["BoxNameClass"] = boxinfo[1][0]
			htmldict["boxinfo"][boxname]["OemName"] = boxinfo[2][1]
			htmldict["boxinfo"][boxname]["OemNameClass"] = boxinfo[2][0]
			htmldict["boxinfo"][boxname]["BuildStatus"] = boxinfo[3][1]
			htmldict["boxinfo"][boxname]["BuildClass"] = boxinfo[3][0]
			htmldict["boxinfo"][boxname]["StartBuild"] = dateset[0]
			htmldict["boxinfo"][boxname]["StartFeedSync"] = dateset[1]
			htmldict["boxinfo"][boxname]["EndBuild"] = dateset[2]
			htmldict["boxinfo"][boxname]["SyncTime"] = dateset[3]
			htmldict["boxinfo"][boxname]["BuildTime"] = dateset[4]
		return htmldict

	def findbuildbox(self):  # find boxname current image is build for
		if self.htmldict is None:
			self.error = f"[{MODULE_NAME}] ERROR in module 'findbuildbox': self.htmldict is None"
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
			self.error = f"[{MODULE_NAME}] ERROR in module 'evaluate': self.htmldict is None"
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
			timestr = boxinfo[boxname]["BuildTime"].split(",")  # handle those exceptions: e.g. '-1 day, 23:59:24'
			time = timestr[0].strip().split(":") if len(timestr) == 1 else timestr[1].strip().split(":")
			if len(time) < 3:
				time = [0, 0, 0]
			if boxname == buildbox:  # currently built box
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
			if boxname == box:  # own box name
				foundbox = True
				collect = False
			if "Failed" in boxinfo[boxname]["BuildStatus"]:
				failed += 1
			boxcounter += 1
		if box is not None and not foundbox:
			self.error = f"[{MODULE_NAME}] WARNING in module 'evaluate': Box not found in this platform. Try another platform."
			return timedelta(), 0, cycletime, boxcounter, failed
		return nextbuild, boxesahead - 1, cycletime, boxcounter, failed

	def strf_delta(self, td):  # converts deltatime-format in hours (e.g. '2 days, 01:00' in '49:00:00')
		h, r = divmod(int(td.total_seconds()), 60 * 60)
		m, s = divmod(r, 60)
		h, m, s = (str(x).zfill(2) for x in (h, m, s))
		return f"{h}:{m}:{s}"


def main(argv):  # shell interface
	mainfmt = "[__main__]"
	buildbox, cycle, evaluate, verbose, architecture, supported, usable = False, False, False, False, False, False, False
	filename, boxname, cycletime = None, None, None
	currarch = "arm_latest"
	currplat = ""
	counter, failed = 0, 0
	helpstring = "Buildstatus v1.3: try 'python Buildstatus.py -h' for more information"
	BS = Buildstatus()
	if BS.error:
		print(f"Error: {BS.error.replace(mainfmt, '').strip()}")
		exit()
	try:
		opts, args = getopt(argv, "a:p:j:e:bcvsuh", ["architecture =", "platform=", "json =", "evaluate =", "buildbox", "cycle", "verbose", "supported", "usable", "help"])
	except GetoptError as error:
		print(f"Error: {error}\n{helpstring}")
		exit(2)
	if not opts:
		verbose = True
	for opt, arg in opts:
		opt = opt.lower().strip()
		arg = arg.lower().strip()
		if opt == "-h":
			print("Usage  : python Buildstatus.py [options...] <data>\n"
			"Example: python Buildstatus.py -a arm_latest -v -e gbue4k -s -u\n"
			"-a, --architecture <data>\tUse architecture\n"
			"-p, --platform <data>\t\tUse platform\n"
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
			print(f"Error: {BS.error.replace(mainfmt, '').strip()}")
			exit()
	if currarch not in archlist:
		print(f"Unknown architecture '{currarch}'. Supported is: {', '.join(x.split(' ')[0] for x in archlist)}")
		exit()
	if currplat:
		currplat = currplat.replace("_", " ")
	if currplat and currplat not in platlist:
		print(f"Unknown platform '{currplat.replace(' ', '_').lower()}'. Supported is: {', '.join(x.replace(' ', '_').lower() for x in platlist)}")
		exit()
	BS.getbuildinfos(currplat)
	if BS.error:
		print(f"Error: {BS.error.replace(mainfmt, '').strip()}")
		exit()
	if BS.htmldict and verbose:
		separator = "+-----+--------------------+--------------------+--------------+----------------------+----------------------+----------------------+-----------+------------+"
		row = "| {:<3} | {:<18} | {:<18} | {:<12} | {:<20} | {:<20} | {:<20} | {:<9} | {:<10} |"
		print(f"+{'-' * 156}+")
		print(f"| {BS.htmldict['title']:<155}|")
		print(separator)
		print(row.format(*BS.htmldict["headline"].split(", ")))
		print(separator)
		for counter, box in enumerate(BS.htmldict["boxinfo"]):
			bi = BS.htmldict["boxinfo"][box]
			print(row.format(bi["No"].rjust(3), box, bi["OemName"], bi["BuildStatus"].rjust(12), bi["StartBuild"], bi["StartFeedSync"], bi["EndBuild"], bi["SyncTime"].rjust(9), bi["BuildTime"].rjust(10)))
		print(separator)
		print("| {:<50}{:<48}{:<57}|".format(f"current platform: {currplat.upper()}", f"boxes found: {counter}", f"building errors found: {str(failed).rjust(3)}"))
		print(f"+{'-' * 156}+")
	if BS.htmldict and filename:
		with open(filename, "w") as f:
			dump(BS.htmldict, f)
		print(f"File '{filename}' was successfully created.")
	if buildbox:
		buildboxname = BS.findbuildbox()
		if BS.error:
			print(f"Error: {BS.error.replace(mainfmt, '').strip()}")
			exit()
		if buildboxname:
			print(f"Currently the image is built for: '{buildboxname}'")
		else:
			print("At the moment no image is built on the platform!")
	if evaluate:
		if boxname:
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate(boxname)
			if BS.error:
				print(f"Error: {BS.error.replace(mainfmt, '').strip()}")
				exit()
			if nextbuild:
				print(f"Estimated duration for next image for '{boxname}' in {BS.strf_delta(nextbuild)}h at {(datetime.now() + nextbuild).strftime('%Y/%m/%d, %H:%M:%S')} ({boxesahead} boxes ahead) ")
			else:
				print("Server paused, unclear how many boxes are ahead!")
		else:
			print("Missing boxname")
			exit()
	if cycle:
		if not cycletime:
			nextbuild, boxesahead, cycletime, counter, failed = BS.evaluate()
			if BS.error:
				print(f"Error: {BS.error.replace(mainfmt, '').strip()}")
				exit()
		print(f"Estimated durance of complete cycle ({currplat}): {BS.strf_delta(cycletime)} h")
	if supported:
		if not architecture and archlist:
			print(f"Available architectures: {', '.join(x for x in archlist)}")
		else:
			print("No architectures found")
	if usable:
		if platlist:
			print(f"Available architectures: {', '.join(x for x in archlist)}")
		else:
			print("No platforms found")


if __name__ == "__main__":
	main(argv[1:])
