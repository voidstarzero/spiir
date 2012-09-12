# Copyright (C) 2009--2011  Kipp Cannon, Chad Hanna, Drew Keppel
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.


#
# =============================================================================
#
#                                   Preamble
#
# =============================================================================
#


import sys
import optparse

# The following snippet is taken from http://gstreamer.freedesktop.org/wiki/FAQ#Mypygstprogramismysteriouslycoredumping.2Chowtofixthis.3F
import pygtk
pygtk.require("2.0")
import gobject
gobject.threads_init()
import pygst
pygst.require('0.10')
import gst

from gstlal import bottle
from gstlal import pipeparts
from glue.ligolw.utils import segments as ligolw_segments
from glue.ligolw import utils
from glue.ligolw import ligolw
from glue import segments
from pylal.datatypes import LIGOTimeGPS


#
# Misc useful functions
#


def channel_dict_from_channel_list(channel_list):
	"""
	Given a list of channels like this ["H1=LSC-STRAIN",
	H2="SOMETHING-ELSE"] produce a dictionary keyed by ifo of channel
	names.  The default values are LSC-STRAIN for all detectors
	"""

	channel_dict = {}
	for channel in channel_list:
		ifo = channel.split("=")[0]
		chan = "".join(channel.split("=")[1:])
		channel_dict[ifo] = chan

	return channel_dict


def pipeline_channel_list_from_channel_dict(channel_dict, ifos = None, opt = "channel-name"):
	"""
	Produce a string of channel name arguments suitable for a pipeline.py
	program that doesn't technically allow multiple options. For example
	--channel-name=H1=LSC-STRAIN --channel-name=H2=LSC-STRAIN
	"""

	outstr = ""
	if ifos is None:
		ifos = channel_dict.keys()
	for i, ifo in enumerate(ifos):
		if i == 0:
			outstr += "%s=%s " % (ifo, channel_dict[ifo])
		else:
			outstr += "--%s=%s=%s " % (opt, ifo, channel_dict[ifo])

	return outstr


def state_vector_on_off_dict_from_bit_lists(on_bit_list, off_bit_list, state_vector_on_off_dict = {"H1" : [0x7, 0x160], "H2" : [0x7, 0x160], "L1" : [0x7, 0x160], "V1" : [0x67, 0x100]}):
	"""
	Produce a dictionary (keyed by detector) of on / off bit tuples from a
	list provided on the command line.
	"""

	for line in on_bit_list:
		ifo = line.split("=")[0]
		bits = "".join(line.split("=")[1:])
		try:
			state_vector_on_off_dict[ifo][0] = int(bits)
		except ValueError: # must be hex
			state_vector_on_off_dict[ifo][0] = int(bits, 16)
	
	for line in off_bit_list:
		ifo = line.split("=")[0]
		bits = "".join(line.split("=")[1:])
		try:
			state_vector_on_off_dict[ifo][1] = int(bits)
		except ValueError: # must be hex
			state_vector_on_off_dict[ifo][1] = int(bits, 16)

	return state_vector_on_off_dict


def state_vector_on_off_list_from_bits_dict(bit_dict):
	"""
	Produce a commandline useful list from a dictionary of on / off state
	vector bits keyed by detector.
	"""

	onstr = ""
	offstr = ""
	for i, ifo in enumerate(bit_dict):
		if i == 0:
			onstr += "%s=%s " % (ifo, bit_dict[ifo][0])
			offstr += "%s=%s " % (ifo, bit_dict[ifo][1])
		else:
			onstr += "--state-vector-on-bits=%s=%s " % (ifo, bit_dict[ifo][0])
			offstr += "--state-vector-off-bits=%s=%s " % (ifo, bit_dict[ifo][1])

	return onstr, offstr


#
# Class to hold the data associated with data sources
#


class GWDataSourceInfo(object):

	def __init__(self, options):
		data_sources = ("frames", "online", "white", "silence", "AdvVirgo", "LIGO", "AdvLIGO")

		# Sanity check the options
		if options.data_source not in data_sources:
			raise ValueError("--data-source not in " + repr(data_sources))
		if options.data_source == "frames" and options.frame_cache is None:
			raise ValueError("--frame-cache not in must be specified when using --data-source=frames")
		if (options.gps_start_time is None or options.gps_end_time is None) and options.data_source == "frames":
			raise ValueError("--gps-start-time and --gps-end-time must be specified unless --data-source=online")
		if len(options.channel_name) == 0:
			raise ValueError("must specify at least one channel in the form --channel-name=IFO=CHANNEL-NAME")
		if options.frame_segments_file is not None and options.data_source != "frames":
			raise ValueError("Can only give --frame-segments-file if --data-source=frames")
		if options.frame_segments_name is not None and options.frame_segments_file is None:
			raise ValueError("Can only specify --frame-segments-name if --frame-segments-file is given")
		
		self.channel_dict = channel_dict_from_channel_list(options.channel_name)

		# Parse the frame segments if they exist
		if options.frame_segments_file is not None:
			self.frame_segments = ligolw_segments.segmenttable_get_by_name(utils.load_filename(options.frame_segments_file, verbose = options.verbose), options.frame_segments_name).coalesce()
		else:
			self.frame_segments = dict([(instrument, None) for instrument in self.channel_dict])

		if options.gps_start_time is not None:
			self.seg = segments.segment(LIGOTimeGPS(options.gps_start_time), LIGOTimeGPS(options.gps_end_time))
			self.seekevent = gst.event_new_seek(1., gst.FORMAT_TIME, gst.SEEK_FLAG_FLUSH | gst.SEEK_FLAG_KEY_UNIT, gst.SEEK_TYPE_SET, self.seg[0].ns(), gst.SEEK_TYPE_SET, self.seg[1].ns())
	
		self.state_vector_on_off_bits = state_vector_on_off_dict_from_bit_lists(options.state_vector_on_bits, options.state_vector_off_bits)
		
		self.frame_cache = options.frame_cache
		self.block_size = options.block_size
		self.data_source = options.data_source
		self.injection_filename = options.injections


def append_options(parser):
	"""
	Append generic data source options to an OptionParser object in order
	to have consistent an unified command lines and parsing throughout the project
	for applications that read GW data.
	"""
	group = optparse.OptionGroup(parser, "Data source options", "Use these options to set up the appropriate data source")
	group.add_option("--data-source", metavar = "source", help = "Set the data source from [frames|online|white|silence|AdvVirgo|LIGO|AdvLIGO].  Required")
	group.add_option("--block-size", metavar = "bytes", default = 16384 * 8 * 512, help = "Data block size to read in bytes. Default 16384 * 8 * 512 (512 seconds of double precision data at 16384 Hz.  This parameter is not used if --data-source=online")
	group.add_option("--frame-cache", metavar = "filename", help = "Set the name of the LAL cache listing the LIGO-Virgo .gwf frame files (optional).  This is required iff --data-source=frames")
	group.add_option("--gps-start-time", metavar = "seconds", help = "Set the start time of the segment to analyze in GPS seconds. Required unless --data-source=online")
	group.add_option("--gps-end-time", metavar = "seconds", help = "Set the end time of the segment to analyze in GPS seconds.  Required unless --data-source=online")
	group.add_option("--injections", metavar = "filename", help = "Set the name of the LIGO light-weight XML file from which to load injections (optional).")
	group.add_option("--channel-name", metavar = "name", action = "append", help = "Set the name of the channels to process.  Can be given multiple times as --channel-name=IFO=CHANNEL-NAME")
	group.add_option("--frame-segments-file", metavar = "filename", help = "Set the name of the LIGO light-weight XML file from which to load frame segments.  Optional iff --data-source=frames")
	group.add_option("--frame-segments-name", default = "datasegments", metavar = "name", help = "Set the name of the segments to extract from the segment tables.  Required iff --frame-segments-file is given")
	group.add_option("--state-vector-on-bits", metavar = "bits", default = [], action = "append", help = "Set the state vector on bits to process (optional).  The default is 0x7 for all detectors. Override with IFO=bits can be given multiple times.  Only currently has meaning for online data.")
	group.add_option("--state-vector-off-bits", metavar = "bits", default = [], action = "append", help = "Set the state vector off bits to process (optional).  The default is 0x160 for all detectors. Override with IFO=bits can be given multiple times.  Only currently has meaning for online data.")
	parser.add_option_group(group)


def mkbasicsrc(pipeline, gw_data_source_info, instrument, verbose = False):
	"""
	All the conditionals and stupid pet tricks for reading real or
	simulated h(t) data in one place.
	"""

	#
	# data source
	#

	# First process fake data or frame data
	if gw_data_source_info.data_source == "white":
		# seek events have to be given to these since the element returned is a tag inject
		src = pipeparts.mkfakesrcseeked(pipeline, instrument, gw_data_source_info.channel_dict[instrument], gw_data_source_info.seekevent, blocksize = gw_data_source_info.block_size)
	elif gw_data_source_info.data_source == "silence":
		# seek events have to be given to these since the element returned is a tag inject
		src = pipeparts.mkfakesrcseeked(pipeline, instrument, gw_data_source_info.channel_dict[instrument], gw_data_source_info.seekevent, blocksize = gw_data_source_info.block_size, wave = 4)
	elif gw_data_source_info.data_source == 'LIGO':
		src = pipeparts.mkfakeLIGOsrc(pipeline, instrument = instrument, channel_name = gw_data_source_info.channel_dict[instrument], blocksize = gw_data_source_info.block_size)
	elif gw_data_source_info.data_source == 'AdvLIGO':
		src = pipeparts.mkfakeadvLIGOsrc(pipeline, instrument = instrument, channel_name = gw_data_source_info.channel_dict[instrument], blocksize = gw_data_source_info.block_size)
	elif gw_data_source_info.data_source == 'AdvVirgo':
		src = pipeparts.mkfakeadvvirgosrc(pipeline, instrument = instrument, channel_name = gw_data_source_info.channel_dict[instrument], blocksize = gw_data_source_info.block_size)
	elif gw_data_source_info.data_source == "frames":
		if instrument == "V1":
			#FIXME Hack because virgo often just uses "V" in the file names rather than "V1".  We need to sieve on "V"
			src = pipeparts.mkframesrc(pipeline, location = gw_data_source_info.frame_cache, instrument = instrument, cache_src_regex = "V", channel_name = gw_data_source_info.channel_dict[instrument], blocksize = gw_data_source_info.block_size, segment_list = gw_data_source_info.frame_segments[instrument])
		else:
			src = pipeparts.mkframesrc(pipeline, location = gw_data_source_info.frame_cache, instrument = instrument, cache_dsc_regex = instrument, channel_name = gw_data_source_info.channel_dict[instrument], blocksize = gw_data_source_info.block_size, segment_list = gw_data_source_info.frame_segments[instrument])
	# Next process online data, fake data must be None for this to have gotten this far
	elif gw_data_source_info.data_source == "online":
		# See https://wiki.ligo.org/DAC/ER2DataDistributionPlan#LIGO_Online_DQ_Channel_Specifica
		state_vector_on_bits, state_vector_off_bits = gw_data_source_info.state_vector_on_off_bits[instrument]

		# FIXME:  be careful hard-coding shared-memory partition
		# FIXME make wait_time adjustable through web interface or command line or both
		src = pipeparts.mklvshmsrc(pipeline, shm_name = {"H1": "LHO_Data", "H2": "LHO_Data", "L1": "LLO_Data", "V1": "VIRGO_Data"}[instrument], wait_time = 120)
		src = pipeparts.mkframecppchanneldemux(pipeline, src, do_file_checksum = True, skip_bad_files = True)

		# strain
		strain = pipeparts.mkqueue(pipeline, None, max_size_buffers = 0, max_size_bytes = 0, max_size_time = gst.SECOND * 60 * 10) # 10 minutes of buffering
		pipeparts.src_deferred_link(src, "%s:%s" % (instrument, gw_data_source_info.channel_dict[instrument]), strain.get_pad("sink"))
		strain = pipeparts.mkaudiorate(pipeline, strain, skip_to_first = True, silent = False)
		@bottle.route("/%s/strain_add_drop.txt" % instrument)
		def strain_add(elem = strain):
			import time
			from pylal.date import XLALUTCToGPS
			t = float(XLALUTCToGPS(time.gmtime()))
			add = elem.get_property("add")
			drop = elem.get_property("drop")
			# FIXME don't hard code the sample rate
			return "%.9f %d %d" % (t, add / 16384., drop / 16384.)

		# state vector
		# FIXME use pipeparts
		statevector = gst.element_factory_make("queue")
		statevector.set_property("max_size_buffers", 0)
		statevector.set_property("max_size_bytes", 0)
		statevector.set_property("max_size_time", gst.SECOND * 60 * 10) # 10 minutes of buffering
		pipeline.add(statevector)
		# FIXME:  don't hard-code channel name
		pipeparts.src_deferred_link(src, "%s:%s" % (instrument, "LLD-DQ_VECTOR"), statevector.get_pad("sink"))
		# FIXME we don't add a signal handler to the statevector audiorate, I assume it should report the same missing samples?
		statevector = pipeparts.mkaudiorate(pipeline, statevector, skip_to_first = True)
		statevector = pipeparts.mkstatevector(pipeline, statevector, required_on = state_vector_on_bits, required_off = state_vector_off_bits)
		@bottle.route("/%s/state_vector_on_off_gap.txt" % instrument)
		def state_vector_state(elem = statevector):
			import time
			from pylal.date import XLALUTCToGPS
			t = float(XLALUTCToGPS(time.gmtime()))
			on = elem.get_property("on-samples")
			off = elem.get_property("off-samples")
			gap = elem.get_property("gap-samples")
			return "%.9f %d %d %d" % (t, on, off, gap)

		# use state vector to gate strain
		src = pipeparts.mkgate(pipeline, strain, threshold = 1, control = statevector)
		# export state vector state
		src.set_property("emit-signals", True)
		# FIXME:  let the state vector messages going to stderr be
		# controled somehow
		bottle.route("/%s/current_segment.txt" % instrument)(get_gate_state(src, msg = instrument, verbose = True).text)
	
	else:
		raise ValueError("invalid data_source: %s" % gw_data_source_info.data_source)

	# seek some non-live sources FIXME someday this should go away and seeks
	# should only be done on the pipeline that is why this is separated
	# here
	if gw_data_source_info.data_source in ("LIGO", "AdvLIGO", "AdvVirgo", "frames"):
		#
		# seek the data source if not live
		#

		if src.set_state(gst.STATE_READY) != gst.STATE_CHANGE_SUCCESS:
			raise RuntimeError("Element %s did not want to enter ready state" % src.get_name())
		if not src.send_event(gw_data_source_info.seekevent):
			raise RuntimeError("Element %s did not handle seek event" % src.get_name())

	#
	# provide an audioconvert element to allow Virgo data (which is
	# single-precision) to be adapted into the pipeline
	#

	src = pipeparts.mkaudioconvert(pipeline, src)


	#
	# progress report
	#

	if verbose:
		src = pipeparts.mkprogressreport(pipeline, src, "progress_src_%s" % instrument)

	#
	# optional injections
	#

	if gw_data_source_info.injection_filename is not None:
		src = pipeparts.mkinjections(pipeline, src, gw_data_source_info.injection_filename)

	#
	# done
	#

	return src

