# Copyright (C) 2009--2013  LIGO Scientific Collaboration
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


import math
import os
import sys
import threading


import pygtk
pygtk.require("2.0")
import gobject
gobject.threads_init()
import pygst
pygst.require('0.10')
import gst


from glue import iterutils
from glue import lal
from glue import segments
from gstlal import pipeio


__author__ = "Kipp Cannon <kipp.cannon@ligo.org>, Chad Hanna <chad.hanna@ligo.org>, Drew Keppel <drew.keppel@ligo.org>"
__version__ = "FIXME"
__date__ = "FIXME"


## 
# @file
#
# A file that contains the pipeparts module code
#

##
# @package python.pipeparts
#
# pipeparts module


#
# =============================================================================
#
#                             Generic Constructors
#
# =============================================================================
#


#
# Applications should use the element-specific wrappings that follow below.
# The generic constructors are only intended to simplify the writing of
# those wrappings, they are not meant to be how applications create
# elements in pipelines.
#


def mkgeneric(pipeline, src, elem_type_name, **properties):
	if "name" in properties:
		elem = gst.element_factory_make(elem_type_name, properties.pop("name"))
	else:
		elem = gst.element_factory_make(elem_type_name)
	for name, value in properties.items():
		elem.set_property(name.replace("_", "-"), value)
	pipeline.add(elem)
	if isinstance(src, gst.Pad):
		src.get_parent_element().link_pads(src, elem, None)
	elif src is not None:
		src.link(elem)
	return elem


#
# deferred link helper
#


class src_deferred_link(object):
	"""!
	A class that manages the task of watching for and connecting to new
	source pads by name.  The inputs are an element, the name of the
	source pad to watch for on that element, and the sink pad (on a
	different element) to which the source pad should be linked when it
	appears.

	The "pad-added" signal of the element will be used to watch for new
	pads, and if the "no-more-pads" signal is emitted by the element
	before the requested pad has appeared ValueException is raised.
	"""
	def __init__(self, element, srcpadname, sinkpad):
		no_more_pads_handler_id = element.connect("no-more-pads", self.no_more_pads, srcpadname)
		assert no_more_pads_handler_id > 0
		pad_added_data = [srcpadname, sinkpad, no_more_pads_handler_id]
		pad_added_handler_id = element.connect("pad-added", self.pad_added, pad_added_data)
		assert pad_added_handler_id > 0
		pad_added_data.append(pad_added_handler_id)

	@staticmethod
	def pad_added(element, pad, (srcpadname, sinkpad, no_more_pads_handler_id, pad_added_handler_id)):
		if pad.get_name() == srcpadname:
			element.handler_disconnect(no_more_pads_handler_id)
			element.handler_disconnect(pad_added_handler_id)
			pad.link(sinkpad)

	@staticmethod
	def no_more_pads(element, srcpadname):
		raise ValueError("<%s>: no pad named '%s'" % (element.get_name(), srcpadname))


#
# framecpp channeldemux helpers
#


class framecpp_channeldemux_set_units(object):
	def __init__(self, elem, units_dict):
		"""
		Connect a handler for the pad-added signal of the
		framecpp_channeldemux element elem, and when a pad is added
		to the element if the pad's name appears as a key in the
		units_dict dictionary that pad's units property will be set
		to the string value associated with that key in the
		dictionary.

		Example:

		>>> framecpp_channeldemux_set_units(elem, {"H1:LSC-STRAIN": "strain"})

		NOTE:  this is a work-around to address the problem that
		most (all?) frame files do not have units set on their
		channel data, whereas downstream consumers of the data
		might require information about the units.  The demuxer
		provides the units as part of a tag event, and
		framecpp_channeldemux_set_units() can be used to override
		the values, thereby correcting absent or incorrect units
		information.
		"""
		self.elem = elem
		self.pad_added_handler_id = elem.connect("pad-added", self.pad_added, units_dict)

	@staticmethod
	def pad_added(element, pad, units_dict):
		name = pad.get_name()
		if name in units_dict:
			pad.set_property("units", units_dict[name])


class framecpp_channeldemux_check_segments(object):
	def __init__(self, elem, seglists, jitter = lal.LIGOTimeGPS(0, 1)):
		self.elem = elem
		self.probe_handler_ids = {}
		self.jitter = jitter
		# keep a copy of the segmentlistdict incase the calling
		# code modifies it
		self.pad_added_handler_id = elem.connect("pad-added", self.pad_added, seglists.copy())

	def pad_added(self, element, pad, seglists):
		name = pad.get_name()
		if name in self.probe_handler_ids:
			pad.remove_data_probe(self.probe_handler_ids.pop(name))
		if name in seglists:
			self.probe_handler_ids[name] = self.set_probe(pad, seglists[name], self.jitter)

	@staticmethod
	def set_probe(pad, seglist, jitter = lal.LIGOTimeGPS(0, 1)):
		# use a copy of the segmentlist so the probe can modify it
		return pad.add_data_probe(framecpp_channeldemux_check_segments.probe, (segments.segmentlist(seglist), jitter))

	@staticmethod
	def probe(pad, obj, (seglist, jitter)):
		if isinstance(obj, gst.Buffer):
			if not obj.flag_is_set(gst.BUFFER_FLAG_GAP):
				# remove the current buffer from the data
				# we're expecting to see
				seglist -= segments.segmentlist([segments.segment((lal.LIGOTimeGPS(0, obj.timestamp), lal.LIGOTimeGPS(0, obj.timestamp + obj.duration)))])
				# ignore missing data intervals unless
				# they're bigger than the jitter
				iterutils.inplace_filter(lambda seg: abs(seg) > jitter, seglist)
			# are we still expecting to see something that
			# precedes the current buffer?
			preceding = segments.segment((segments.NegInfinity, lal.LIGOTimeGPS(0, obj.timestamp)))
			if seglist.intersects_segment(preceding):
				raise ValueError("%s: detected missing data:  %s" % (pad.get_name(), seglist & segments.segmentlist([preceding])))
		elif isinstance(obj, gst.Event) and obj.type == gst.EVENT_EOS:
			if seglist:
				raise ValueError("%s: at EOS detected missing data: %s" % (pad.get_name(), seglist))
		return True


#
# framecpp file sink helpers
#


def framecpp_filesink_ldas_path_handler(elem, pspec, (outpath, dir_digits)):
	"""
	Example:

	>>> filesinkelem.connect("notify::timestamp", framecpp_filesink_ldas_path_handler, (".", 5))
	"""
	# get timestamp and truncate to integer seconds
	timestamp = elem.get_property("timestamp") // gst.SECOND

	# extract leading digits
	leading_digits = timestamp // 10**int(math.log10(timestamp) + 1 - dir_digits)

	# get other metadata
	instrument = elem.get_property("instrument")
	frame_type = elem.get_property("frame-type")

	# make target directory, and set path
	path = os.path.join(outpath, "%s-%s-%d" % (instrument, frame_type, leading_digits))
	if not os.path.exists(path):
		os.makedirs(path)
	elem.set_property("path", path)


def framecpp_filesink_cache_entry_from_mfs_message(message):
	"""
	Translate an element message posted by the multifilesink element
	inside a framecpp_filesink bin into a lal.CacheEntry object
	describing the file being written by the multifilesink element.
	"""
	# extract the segment spanned by the file from the message directly
	start = lal.LIGOTimeGPS(0, message.structure["timestamp"])
	end = start + lal.LIGOTimeGPS(0, message.structure["duration"])

	# retrieve the framecpp_filesink bin (for instrument/observatory
	# and frame file type)
	parent = message.src.get_parent()

	# construct and return a CacheEntry object
	return lal.CacheEntry(parent.get_property("instrument"), parent.get_property("frame-type"), segments.segment(start, end), "file://localhost%s" % os.path.abspath(message.structure["filename"]))


#
# =============================================================================
#
#                                Pipeline Parts
#
# =============================================================================
#


def mkchannelgram(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_channelgram", **properties)


def mkspectrumplot(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_spectrumplot", **properties)


def mkhistogram(pipeline, src):
	return mkgeneric(pipeline, src, "lal_histogramplot")


## Adds a <a href="@gstlalgtkdoc/GSTLALSegmentSrc.html">lal_segmentsrc</a> element to a pipeline with useful default properties
def mksegmentsrc(pipeline, segment_list, blocksize = 4096 * 1 * 1, invert_output = False):
	# default blocksize is 4096 seconds of unsigned integers at
	# 1 Hz, e.g. segments without nanoseconds
	return mkgeneric(pipeline, None, "lal_segmentsrc", blocksize = blocksize, segment_list = segments.segmentlist(segments.segment(a.ns(), b.ns()) for a, b in segment_list), invert_output = invert_output)


## Adds a <a href="@gstlalgtkdoc/GstLALCacheSrc.html">lal_cachesrc</a> element to a pipeline with useful default properties
def mklalcachesrc(pipeline, location, use_mmap = True, **properties):
	return mkgeneric(pipeline, None, "lal_cachesrc", location = location, use_mmap = use_mmap, **properties)


def mklvshmsrc(pipeline, shm_name, **properties):
	return mkgeneric(pipeline, None, "gds_lvshmsrc", shm_name = shm_name, **properties)


def mkframexmitsrc(pipeline, multicast_group, port, **properties):
	return mkgeneric(pipeline, None, "gds_framexmitsrc", multicast_group = multicast_group, port = port, **properties)


def mkigwdparse(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "framecpp_igwdparse", **properties)


## Adds a <a href="@gstpluginsbasedoc/gst-plugins-base-plugins-uridecodebin.html">uridecodebin</a> element to a pipeline with useful default properties
def mkuridecodebin(pipeline, uri, caps = "application/x-igwd-frame,framed=true", **properties):
	return mkgeneric(pipeline, None, "uridecodebin", uri = uri, caps = None if caps is None else gst.Caps(caps), **properties)


def mkframecppchanneldemux(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "framecpp_channeldemux", **properties)


def mkframecppchannelmux(pipeline, channel_src_map, units = None, seglists = None, **properties):
	elem = mkgeneric(pipeline, None, "framecpp_channelmux", **properties)
	if channel_src_map is not None:
		for channel, src in channel_src_map.items():
			for srcpad in src.src_pads():
				if srcpad.link(elem.get_pad(channel)) == gst.PAD_LINK_OK:
					break
	if units is not None:
		framecpp_channeldemux_set_units(elem, units)
	if seglists is not None:
		framecpp_channeldemux_check_segments(elem, seglists)
	return elem


def mkframecppfilesink(pipeline, src, message_forward = True, **properties):
	post_messages = properties.pop("post_messages", True)
	elem = mkgeneric(pipeline, src, "framecpp_filesink", message_forward = message_forward, **properties)
	# FIXME:  there's supposed to be some sort of proxy mechanism for
	# setting properties on child elements, but we can't seem to get
	# anything to work
	elem.get_by_name("multifilesink").set_property("post-messages", post_messages)
	return elem


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-multifilesink.html">multifilesink</a> element to a pipeline with useful default properties
def mkmultifilesink(pipeline, src, next_file = 0, sync = False, async = False, **properties):
	return mkgeneric(pipeline, src, "multifilesink", next_file = next_file, sync = sync, async = async, **properties)


def mkndssrc(pipeline, host, instrument, channel_name, channel_type, blocksize = 16384 * 8 * 1, port = 31200):
	# default blocksize is 1 second of double precision floats at
	# 16384 Hz, e.g., LIGO h(t)
	return mkgeneric(pipeline, None, "ndssrc", blocksize = blocksize, port = port, host = host, channel_name = "%s:%s" % (instrument, channel_name), channel_type = channel_type)


## Adds a <a href="@gstdoc/gstreamer-plugins-capsfilter.html">capsfilter</a> element to a pipeline with useful default properties
def mkcapsfilter(pipeline, src, caps):
	return mkgeneric(pipeline, src, "capsfilter", caps = gst.Caps(caps))


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-capssetter.html">capssetter</a> element to a pipeline with useful default properties
def mkcapssetter(pipeline, src, caps, **properties):
	return mkgeneric(pipeline, src, "capssetter", caps = gst.Caps(caps), **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALStateVector.html">lal_statevector</a> element to a pipeline with useful default properties
def mkstatevector(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_statevector", **properties)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-taginject.html">taginject</a> element to a pipeline with useful default properties
def mktaginject(pipeline, src, tags):
	return mkgeneric(pipeline, src, "taginject", tags = tags)


## Adds a <a href="@gstpluginsbasedoc/gst-plugins-base-plugins-audiotestsrc.html">audiotestsrc</a> element to a pipeline with useful default properties
def mkaudiotestsrc(pipeline, **properties):
	return mkgeneric(pipeline, None, "audiotestsrc", **properties)


## see documentation for mktaginject() mkcapsfilter() and mkaudiotestsrc()
def mkfakesrc(pipeline, instrument, channel_name, blocksize = None, volume = 1e-20, is_live = False, wave = 9, rate = 16384):
	if blocksize is None:
		# default blocksize is 1 second * rate samples/second * 8
		# bytes/sample (assume double-precision floats)
		blocksize = 1 * rate * 8
	return mktaginject(pipeline, mkcapsfilter(pipeline, mkaudiotestsrc(pipeline, samplesperbuffer = blocksize / 8, wave = wave, volume = volume, is_live = is_live), "audio/x-raw-float, width=64, rate=%d" % rate), "instrument=%s,channel-name=%s,units=strain" % (instrument, channel_name))


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-audiofirfilter.html">audiofirfilter</a> element to a pipeline with useful default properties
def mkfirfilter(pipeline, src, kernel, latency, **properties):
	properties.update((name, val) for name, val in (("kernel", kernel), ("latency", latency)) if val is not None)
	return mkgeneric(pipeline, src, "audiofirfilter", **properties)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-audioiirfilter.html">audioiirfilter</a> element to a pipeline with useful default properties
def mkiirfilter(pipeline, src, a, b):
	# convention is z = \exp(-i 2 \pi f / f_{\rm sampling})
	# H(z) = (\sum_{j=0}^{N} a_j z^{-j}) / (\sum_{j=0}^{N} (-1)^{j} b_j z^{-j})
	return mkgeneric(pipeline, src, "audioiirfilter", a = a, b = b)


## Adds a <a href="@gstlalgtkdoc/GSTLALShift.html">lal_shift</a> element to a pipeline with useful default properties
def mkshift(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_shift", **properties)


def mkfakeLIGOsrc(pipeline, location = None, instrument = None, channel_name = None, blocksize = 16384 * 8 * 1):
	properties = {"blocksize": blocksize}
	properties.update((name, val) for name, val in (("instrument", instrument), ("channel_name", channel_name)) if val is not None)
	return mkgeneric(pipeline, None, "lal_fakeligosrc", **properties)


def mkfakeadvLIGOsrc(pipeline, location = None, instrument = None, channel_name = None, blocksize = 16384 * 8 * 1):
	properties = {"blocksize": blocksize}
	properties.update((name, val) for name, val in (("instrument", instrument), ("channel_name", channel_name)) if val is not None)
	return mkgeneric(pipeline, None, "lal_fakeadvligosrc", **properties)


def mkfakeadvvirgosrc(pipeline, location = None, instrument = None, channel_name = None, blocksize = 16384 * 8 * 1):
	properties = {"blocksize": blocksize}
	if instrument is not None:
		properties["instrument"] = instrument
	if channel_name is not None:
		properties["channel_name"] = channel_name
	return mkgeneric(pipeline, None, "lal_fakeadvvirgosrc", **properties)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-progressreport.html">progress_report</a> element to a pipeline with useful default properties
def mkprogressreport(pipeline, src, name):
	return mkgeneric(pipeline, src, "progressreport", do_query = False, name = name)


## Adds a <a href="@gstlalgtkdoc/GSTLALSimulation.html">lal_simulation</a> element to a pipeline with useful default properties
def mkinjections(pipeline, src, filename):
	return mkgeneric(pipeline, src, "lal_simulation", xml_location = filename)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-audiochebband.html">audiochebband</a> element to a pipeline with useful default properties
def mkaudiochebband(pipeline, src, lower_frequency, upper_frequency, poles = 8):
	return mkgeneric(pipeline, src, "audiochebband", lower_frequency = lower_frequency, upper_frequency = upper_frequency, poles = poles)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-audiocheblimit.html">audiocheblimit</a> element to a pipeline with useful default properties
def mkaudiocheblimit(pipeline, src, cutoff, mode = 0, poles = 8):
	return mkgeneric(pipeline, src, "audiocheblimit", cutoff = cutoff, mode = mode, poles = poles)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-audioamplify.html">audioamplify</a> element to a pipeline with useful default properties
def mkaudioamplify(pipeline, src, amplification):
	return mkgeneric(pipeline, src, "audioamplify", clipping_method = 3, amplification = amplification)


## Adds a <a href="@gstlalgtkdoc/GSTLALAudioUnderSample.html">lal_audioundersample</a> element to a pipeline with useful default properties
def mkaudioundersample(pipeline, src):
	return mkgeneric(pipeline, src, "lal_audioundersample")


## Adds a <a href="@gstpluginsbasedoc/gst-plugins-base-plugins-audioresample.html">audioresample</a> element to a pipeline with useful default properties
def mkresample(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "audioresample", **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALInterpolator.html">lal_interpolator</a> element to a pipeline with useful default properties
def mkinterpolator(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_interpolator", **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALWhiten.html">lal_whiten</a> element to a pipeline with useful default properties
def mkwhiten(pipeline, src, psd_mode = 0, zero_pad = 0, fft_length = 8, average_samples = 64, median_samples = 7, **properties):
	return mkgeneric(pipeline, src, "lal_whiten", psd_mode = psd_mode, zero_pad = zero_pad, fft_length = fft_length, average_samples = average_samples, median_samples = median_samples, **properties)


## Adds a <a href="@gstdoc/gstreamer-plugins-tee.html">tee</a> element to a pipeline with useful default properties
def mktee(pipeline, src):
	return mkgeneric(pipeline, src, "tee")


## Adds a <a href="@gstdoc/GstLALAdder.html">lal_adder</a> element to a pipeline with useful default properties
def mkadder(pipeline, srcs, sync = True, **properties):
	elem = mkgeneric(pipeline, None, "lal_adder", sync = sync, **properties)
	if srcs is not None:
		for src in srcs:
			src.link(elem)
	return elem


## Adds a <a href="@gstdoc/gstreamer-plugins-queue.html">queue</a> element to a pipeline with useful default properties
def mkqueue(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "queue", **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALWhiten.html">lal_whiten</a> element to a pipeline with useful default properties
def mkdrop(pipeline, src, drop_samples = 0):
	return mkgeneric(pipeline, src, "lal_drop", drop_samples = drop_samples)


## Adds a <a href="@gstlalgtkdoc/GSTLALNoFakeDisconts.html">lal_nofakedisconts</a> element to a pipeline with useful default properties
def mknofakedisconts(pipeline, src, silent = True):
	return mkgeneric(pipeline, src, "lal_nofakedisconts", silent = silent)


## Adds a <a href="@gstlalgtkdoc/GSTLALFIRBank.html">lal_firbank</a> element to a pipeline with useful default properties
def mkfirbank(pipeline, src, latency = None, fir_matrix = None, time_domain = None, block_stride = None):
	properties = dict((name, value) for name, value in zip(("latency", "fir_matrix", "time_domain", "block_stride"), (latency, fir_matrix, time_domain, block_stride)) if value is not None)
	return mkgeneric(pipeline, src, "lal_firbank", **properties)

def mktdwhiten(pipeline, src, latency = None, kernel = None, taper_length = None):
	# a taper length of 1/4 kernel length mimics the default
	# configuration of the FFT whitener
	if taper_length is None and kernel is not None:
		taper_length = len(kernel) // 4
	properties = dict((name, value) for name, value in zip(("latency", "kernel", "taper_length"), (latency, kernel, taper_length)) if value is not None)
	return mkgeneric(pipeline, src, "lal_tdwhiten", **properties)


def mkiirbank(pipeline, src, a1, b0, delay, name = None):
	properties = dict((name, value) for name, value in (("name", name), ("delay_matrix", delay)) if value is not None)
	if a1 is not None:
		properties["a1_matrix"] = pipeio.repack_complex_array_to_real(a1)
	if b0 is not None:
		properties["b0_matrix"] = pipeio.repack_complex_array_to_real(b0)
	elem = mkgeneric(pipeline, src, "lal_iirbank", **properties)
	elem = mknofakedisconts(pipeline, elem)	# FIXME:  remove after basetransform behaviour fixed
	return elem

def mkcudaiirbank(pipeline, src, a1, b0, delay, name = None):
 	properties = dict((name, value) for name, value in (("name", name), ("delay_matrix", delay)) if value is not None)
 	if a1 is not None:
 		properties["a1_matrix"] = pipeio.repack_complex_array_to_real(a1)
 	if b0 is not None:
 		properties["b0_matrix"] = pipeio.repack_complex_array_to_real(b0)
 	elem = mkgeneric(pipeline, src, "cuda_iirbank", **properties)
 	elem = mknofakedisconts(pipeline, elem)	# FIXME:  remove after basetransform behaviour fixed
 	return elem


def mkcudamultiratespiir(pipeline, src, bank_fname, gap_handle = 0, stream_id = 0, name = None):
	properties = dict((name, value) for name, value in (("name", name), ("bank_fname", bank_fname), ("gap_handle", gap_handle), ("stream_id", stream_id)) if value is not None)
	elem = mkgeneric(pipeline, src, "cuda_multiratespiir", **properties)
	return elem

def mktrim(pipeline, src, initial_offset = None, final_offset = None, inverse = None):
	properties = dict((name, value) for name, value in zip(("initial-offset", "final-offset", "inverse"), (initial_offset,final_offset,inverse)) if value is not None)
	return mkgeneric(pipeline, src, "lal_trim", **properties)


def mkmean(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_mean", **properties)


def mkabs(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "abs", **properties)


def mkpow(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "pow", **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALReblock.html">lal_reblock</a> element to a pipeline with useful default properties
def mkreblock(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_reblock", **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALSumSquares.html">lal_sumsquares</a> element to a pipeline with useful default properties
def mksumsquares(pipeline, src, weights = None):
	if weights is not None:
		return mkgeneric(pipeline, src, "lal_sumsquares", weights = weights)
	else:
		return mkgeneric(pipeline, src, "lal_sumsquares")


## Adds a <a href="@gstlalgtkdoc/GSTLALGate.html">lal_gate</a> element to a pipeline with useful default properties
def mkgate(pipeline, src, threshold = None, control = None, **properties):
	if threshold is not None:
		elem = mkgeneric(pipeline, None, "lal_gate", threshold = threshold, **properties)
	else:
		elem = mkgeneric(pipeline, None, "lal_gate", **properties)
	for peer, padname in ((src, "sink"), (control, "control")):
		if isinstance(peer, gst.Pad):
			peer.get_parent_element().link_pads(peer, elem, padname)
		elif peer is not None:
			peer.link_pads(None, elem, padname)
	return elem


def mkbitvectorgen(pipeline, src, bit_vector, **properties):
	return mkgeneric(pipeline, src, "lal_bitvectorgen", bit_vector = bit_vector, **properties)


## Adds a <a href="@gstlalgtkdoc/GSTLALMatrixMixer.html">lal_matrixmixer</a> element to a pipeline with useful default properties
def mkmatrixmixer(pipeline, src, matrix = None):
	if matrix is not None:
		return mkgeneric(pipeline, src, "lal_matrixmixer", matrix = matrix)
	else:
		return mkgeneric(pipeline, src, "lal_matrixmixer")


## Adds a <a href="@gstlalgtkdoc/GSTLALToggleComplex.html">lal_togglecomplex</a> element to a pipeline with useful default properties
def mktogglecomplex(pipeline, src):
	return mkgeneric(pipeline, src, "lal_togglecomplex")


## Adds a <a href="@gstlalgtkdoc/GSTLALAutoChiSq.html">lal_autochisq</a> element to a pipeline with useful default properties
def mkautochisq(pipeline, src, autocorrelation_matrix = None, mask_matrix = None, latency = 0, snr_thresh=0):
	properties = {}
	if autocorrelation_matrix is not None:
		properties.update({
			"autocorrelation_matrix": pipeio.repack_complex_array_to_real(autocorrelation_matrix),
			"latency": latency,
			"snr_thresh": snr_thresh
		})
	if mask_matrix is not None:
		properties["autocorrelation_mask_matrix"] = mask_matrix
	return mkgeneric(pipeline, src, "lal_autochisq", **properties)


## Adds a <a href="@gstdoc/gstreamer-plugins-fakesink.html">fakesink</a> element to a pipeline with useful default properties
def mkfakesink(pipeline, src):
	return mkgeneric(pipeline, src, "fakesink", sync = False, async = False)


## Adds a <a href="@gstdoc/gstreamer-plugins-filesink.html">filesink</a> element to a pipeline with useful default properties
def mkfilesink(pipeline, src, filename):
	return mkgeneric(pipeline, src, "filesink", sync = False, async = False, buffer_mode = 2, location = filename)


## Adds a <a href="@gstlalgtkdoc/GstTSVEnc.html">lal_nxydump</a> element to a pipeline with useful default properties
def mknxydumpsink(pipeline, src, filename, segment = None):
	if segment is not None:
		elem = mkgeneric(pipeline, src, "lal_nxydump", start_time = segment[0].ns(), stop_time = segment[1].ns())
	else:
		elem = mkgeneric(pipeline, src, "lal_nxydump")
	return mkfilesink(pipeline, elem, filename)


def mknxydumpsinktee(pipeline, src, *args, **properties):
	t = mktee(pipeline, src)
	mknxydumpsink(pipeline, mkqueue(pipeline, t), *args, **properties)
	return t


def mkblcbctriggergen(pipeline, snr, chisq, template_bank_filename, snr_threshold, sigmasq):
	# snr is complex and chisq is real so the correct source and sink
	# pads will be selected automatically
	elem = mkgeneric(pipeline, snr, "lal_blcbctriggergen", bank_filename = template_bank_filename, snr_thresh = snr_threshold, sigmasq = sigmasq)
	chisq.link(elem)
	return elem


def mktriggergen(pipeline, snr, chisq, template_bank_filename, snr_threshold, sigmasq):
	# snr is complex and chisq is real so the correct source and sink
	# pads will be selected automatically
	elem = mkgeneric(pipeline, snr, "lal_triggergen", bank_filename = template_bank_filename, snr_thresh = snr_threshold, sigmasq = sigmasq)
	chisq.link(elem)
	return elem


def mktriggerxmlwritersink(pipeline, src, filename):
	return mkgeneric(pipeline, src, "lal_triggerxmlwriter", sync = False, async = False, location = filename)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-wavenc.html">wavenc</a> element to a pipeline with useful default properties
def mkwavenc(pipeline, src):
	return mkgeneric(pipeline, src, "wavenc")


## Adds a <a href="@gstpluginsbasedoc/gst-plugins-base-plugins-vorbisenc.html">vorbisenc</a> element to a pipeline with useful default properties
def mkvorbisenc(pipeline, src):
	return mkgeneric(pipeline, src, "vorbisenc")


def mkcolorspace(pipeline, src):
	return mkgeneric(pipeline, src, "ffmpegcolorspace")


def mktheoraenc(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "theoraenc", **properties)


def mkmpeg4enc(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "ffenc_mpeg4", **properties)


def mkoggmux(pipeline, src):
	return mkgeneric(pipeline, src, "oggmux")


def mkavimux(pipeline, src):
	return mkgeneric(pipeline, src, "avimux")


## Adds a <a href="@gstpluginsbasedoc/gst-plugins-base-plugins-audioconvert.html">audioconvert</a> element to a pipeline with useful default properties
def mkaudioconvert(pipeline, src, caps_string = None):
	elem = mkgeneric(pipeline, src, "audioconvert")
	if caps_string is not None:
		elem = mkcapsfilter(pipeline, elem, caps_string)
	return elem


## Adds a <a href="@gstpluginsbasedoc/gst-plugins-base-plugins-audiorate.html">audiorate</a> element to a pipeline with useful default properties
def mkaudiorate(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "audiorate", **properties)


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-flacenc.html">flacenc</a> element to a pipeline with useful default properties
def mkflacenc(pipeline, src, quality = 0, **properties):
	return mkgeneric(pipeline, src, "flacenc", quality = quality, **properties)


def mkogmvideosink(pipeline, videosrc, filename, audiosrc = None, verbose = False):
	src = mkcolorspace(pipeline, videosrc)
	src = mkcapsfilter(pipeline, src, "video/x-raw-yuv, format=(fourcc)I420")
	src = mktheoraenc(pipeline, src, border = 2, quality = 48, quick = False)
	src = mkoggmux(pipeline, src)
	if audiosrc is not None:
		mkflacenc(pipeline, mkcapsfilter(pipeline, mkaudioconvert(pipeline, audiosrc), "audio/x-raw-int, width=32, depth=24")).link(src)
	if verbose:
		src = mkprogressreport(pipeline, src, filename)
	mkfilesink(pipeline, src, filename)


def mkvideosink(pipeline, src):
	return mkgeneric(pipeline, mkcolorspace(pipeline, src), "autovideosink")


## Adds a <a href="@gstpluginsgooddoc/gst-plugins-good-plugins-autoaudiosink.html">autoaudiosink</a> element to a pipeline with useful default properties
def mkautoaudiosink(pipeline, src):
	return mkgeneric(pipeline, mkqueue(pipeline, src), "autoaudiosink")


def mkplaybacksink(pipeline, src, amplification = 0.1):
	elems = (
		gst.element_factory_make("audioconvert"),
		gst.element_factory_make("capsfilter"),
		gst.element_factory_make("audioamplify"),
		gst.element_factory_make("audioconvert"),
		gst.element_factory_make("queue"),
		gst.element_factory_make("autoaudiosink")
	)
	elems[1].set_property("caps", gst.Caps("audio/x-raw-float, width=64"))
	elems[2].set_property("amplification", amplification)
	elems[4].set_property("max-size-time", 1 * gst.SECOND)
	pipeline.add(*elems)
	gst.element_link_many(src, *elems)

# FIXME no specific alias for this url since this library only has one element.
# DO NOT DOCUMENT OTHER CODES THIS WAY! Use @gstdoc @gstpluginsbasedoc etc.
## Adds a <a href="http://gstreamer.freedesktop.org/data/doc/gstreamer/head/gst-plugins-base-libs/html/gstreamer-app.html">appsink</a> element to a pipeline with useful default properties
def mkappsink(pipeline, src, max_buffers = 1, drop = False, **properties):
	return mkgeneric(pipeline, src, "appsink", sync = False, async = False, emit_signals = True, max_buffers = max_buffers, drop = drop, **properties)


class AppSync(object):
	def __init__(self, appsink_new_buffer, appsinks = []):
		self.lock = threading.Lock()
		# handler to invoke on availability of new time-ordered
		# buffer
		self.appsink_new_buffer = appsink_new_buffer
		# element --> timestamp of current buffer or None if no
		# buffer yet available
		self.appsinks = {}
		# set of sink elements that are currently at EOS
		self.at_eos = set()
		# do equivalent of .add_sink() for each pre-built appsink
		# element provided at this time
		for elem in appsinks:
			if elem in self.appsinks:
				raise ValueError("duplicate appsinks %s" % repr(elem))
			elem.connect("new-buffer", self.appsink_handler, False)
			elem.connect("eos", self.appsink_handler, True)
			self.appsinks[elem] = None

	def add_sink(self, pipeline, src, drop = False, **properties):
		# NOTE that max buffers must be 1 for this to work
		assert "max_buffers" not in properties
		elem = mkappsink(pipeline, src, max_buffers = 1, drop = drop, **properties)
		elem.connect("new-buffer", self.appsink_handler, False)
		elem.connect("eos", self.appsink_handler, True)
		self.appsinks[elem] = None
		return elem

	def appsink_handler(self, elem, eos):
		with self.lock:
			# update eos status, and retrieve buffer timestamp
			if eos:
				self.at_eos.add(elem)
			else:
				self.at_eos.discard(elem)
				assert self.appsinks[elem] is None
				self.appsinks[elem] = elem.get_last_buffer().timestamp

			# keep looping while we can process buffers
			while True:
				# retrieve the timestamps of all elements that
				# aren't at eos and all elements at eos that still
				# have buffers in them
				timestamps = [(t, e) for e, t in self.appsinks.items() if e not in self.at_eos or t is not None]
				# nothing to do if all elements are at eos and do
				# not have buffers
				if not timestamps:
					break
				# find the element with the oldest timestamp.  None
				# compares as less than everything, so we'll find
				# any element (that isn't at eos) that doesn't yet
				# have a buffer (elements at eos and that are
				# without buffers aren't in the list)
				timestamp, elem_with_oldest = min(timestamps)
				# if there's an element without a buffer, do
				# nothing --- we require all non-eos elements to
				# have buffers before proceding
				if timestamp is None:
					break
				# clear timestamp and pass element to
				# handler func.  function call is done last
				# so that all of our book-keeping has been
				# taken care of in case an exception gets
				# raised
				self.appsinks[elem_with_oldest] = None
				self.appsink_new_buffer(elem_with_oldest)


def connect_appsink_dump_dot(pipeline, appsinks, basename, verbose = False):

	"""
	add a signal handler to write a pipeline graph upon receipt of the
	first trigger buffer.  the caps in the pipeline graph are not fully
	negotiated until data comes out the end, so this version of the graph
	shows the final formats on all links
	"""

	class AppsinkDumpDot(object):
		# data shared by all instances
		# number of times execute method has been invoked, and a mutex
		n_lock = threading.Lock()
		n = 0

		def __init__(self, pipeline, write_after, basename, verbose = False):
			self.pipeline = pipeline
			self.handler_id = None
			self.write_after = write_after
			self.filestem = "%s.%s" % (basename, "TRIGGERS")
			self.verbose = verbose

		def execute(self, elem):
			with self.n_lock:
				type(self).n += 1
				if self.n >= self.write_after:
					write_dump_dot(self.pipeline, self.filestem, verbose = self.verbose)
			elem.disconnect(self.handler_id)

	for sink in appsinks:
		appsink_dump_dot = AppsinkDumpDot(pipeline, len(appsinks), basename = basename, verbose = verbose)
		appsink_dump_dot.handler_id = sink.connect_after("new-buffer", appsink_dump_dot.execute)


def mkchecktimestamps(pipeline, src, name = None, silent = True, timestamp_fuzz = 1):
	return mkgeneric(pipeline, src, "lal_checktimestamps", name = name, silent = silent, timestamp_fuzz = timestamp_fuzz)


## Adds a <a href="@gstlalgtkdoc/GSTLALPeak.html">lal_peak</a> element to a pipeline with useful default properties
def mkpeak(pipeline, src, n):
	return mkgeneric(pipeline, src, "lal_peak", n = n)


def mkitac(pipeline, src, n, bank, autocorrelation_matrix = None, mask_matrix = None, snr_thresh = 0, sigmasq = None):
	properties = {
		"n": n,
		"bank_filename": bank,
		"snr_thresh": snr_thresh
	}
	if autocorrelation_matrix is not None:
		properties["autocorrelation_matrix"] = pipeio.repack_complex_array_to_real(autocorrelation_matrix)
	if mask_matrix is not None:
		properties["autocorrelation_mask"] = mask_matrix
	if sigmasq is not None:
		properties["sigmasq"] = sigmasq
	return mkgeneric(pipeline, src, "lal_itac", **properties)


def mklhocoherentnull(pipeline, H1src, H2src, H1_impulse, H1_latency, H2_impulse, H2_latency, srate):
	elem = mkgeneric(pipeline, None, "lal_lho_coherent_null", block_stride = srate, H1_impulse = H1_impulse, H2_impulse = H2_impulse, H1_latency = H1_latency, H2_latency = H2_latency)
	for peer, padname in ((H1src, "H1sink"), (H2src, "H2sink")):
		if isinstance(peer, gst.Pad):
			peer.get_parent_element().link_pads(peer, elem, padname)
		elif peer is not None:
			peer.link_pads(None, elem, padname)
	return elem

def mkcomputegamma(pipeline, dctrl, exc, cos, sin, **properties):
	elem = mkgeneric(pipeline, None, "lal_compute_gamma", **properties)
	for peer, padname in ((dctrl, "dctrl_sink"), (exc, "exc_sink"), (cos, "cos"), (sin, "sin")):
		if isinstance(peer, gst.Pad):
			peer.get_parent_element().link_pads(peer, elem, padname)
		elif peer is not None:
			peer.link_pads(None, elem, padname)
	return elem

def mkbursttriggergen(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_bursttriggergen", **properties)

def mkodctodqv(pipeline, src, **properties):
	return mkgeneric(pipeline, src, "lal_odc_to_dqv", **properties)

def mktcpserversink(pipeline, src, **properties):
	# units_soft_max = 1 GB
	# FIXME:  are these sensible defaults?
	return mkgeneric(pipeline, src, "tcpserversink", sync = True, sync_method = "latest-keyframe", recover_policy = "keyframe", unit_type = "bytes", units_soft_max = 1024**3, **properties)


def audioresample_variance_gain(quality, num, den):
	"""Calculate the output gain of GStreamer's stock audioresample element.

	The audioresample element has a frequency response of unity "almost" all the
	way up the Nyquist frequency.  However, for an input of unit variance
	Gaussian noise, the output will have a variance very slighly less than 1.
	The return value is the variance that the filter will produce for a given
	"quality" setting and sample rate.

	@param den The denomenator of the ratio of the input and output sample rates
	@param num The numerator of the ratio of the input and output sample rates
	@return The variance of the output signal for unit variance input

	The following example shows how to apply the correction factor using an
	audioamplify element.

	>>> from gstlal.pipeutil import *
	>>> from gstlal.pipeparts import audioresample_variance_gain
	>>> from gstlal import pipeio
	>>> import numpy
	>>> nsamples = 2 ** 17
	>>> num = 2
	>>> den = 1
	>>> def handoff_handler(element, buffer, pad, (quality, filt_len, num, den)):
	...		out_latency = numpy.ceil(float(den) / num * filt_len)
	...		buf = pipeio.array_from_audio_buffer(buffer).flatten()
	...		std = numpy.std(buf[out_latency:-out_latency])
	...		print "quality=%2d, filt_len=%3d, num=%d, den=%d, stdev=%.2f" % (
	...			quality, filt_len, num, den, std)
	...
	>>> for quality in range(11):
	...		pipeline = gst.Pipeline()
	...		correction = 1/numpy.sqrt(audioresample_variance_gain(quality, num, den))
	...		elems = mkelems_in_bin(pipeline,
	...			('audiotestsrc', {'wave':'gaussian-noise','volume':1}),
	...			('capsfilter', {'caps':gst.Caps('audio/x-raw-float,width=64,rate=%d' % num)}),
	...			('audioresample', {'quality':quality}),
	...			('capsfilter', {'caps':gst.Caps('audio/x-raw-float,width=64,rate=%d' % den)}),
	...			('audioamplify', {'amplification':correction,'clipping-method':'none'}),
	...			('fakesink', {'signal-handoffs':True, 'num-buffers':1})
	...		)
	...		filt_len = elems[2].get_property('filter-length')
	...		elems[0].set_property('samplesperbuffer', 2 * filt_len + nsamples)
	...		if elems[-1].connect_after('handoff', handoff_handler, (quality, filt_len, num, den)) < 1:
	...			raise RuntimeError
	...		try:
	...			if pipeline.set_state(gst.STATE_PLAYING) is not gst.STATE_CHANGE_ASYNC:
	...				raise RuntimeError
	...			if not pipeline.get_bus().poll(gst.MESSAGE_EOS, -1):
	...				raise RuntimeError
	...		finally:
	...			if pipeline.set_state(gst.STATE_NULL) is not gst.STATE_CHANGE_SUCCESS:
	...				raise RuntimeError
	...
	quality= 0, filt_len=  8, num=2, den=1, stdev=1.00
	quality= 1, filt_len= 16, num=2, den=1, stdev=1.00
	quality= 2, filt_len= 32, num=2, den=1, stdev=1.00
	quality= 3, filt_len= 48, num=2, den=1, stdev=1.00
	quality= 4, filt_len= 64, num=2, den=1, stdev=1.00
	quality= 5, filt_len= 80, num=2, den=1, stdev=1.00
	quality= 6, filt_len= 96, num=2, den=1, stdev=1.00
	quality= 7, filt_len=128, num=2, den=1, stdev=1.00
	quality= 8, filt_len=160, num=2, den=1, stdev=1.00
	quality= 9, filt_len=192, num=2, den=1, stdev=1.00
	quality=10, filt_len=256, num=2, den=1, stdev=1.00
	"""

	# These constants were measured with 2**22 samples.

	if num > den: # downsampling
		return den * (
			0.7224862140943990596,
			0.7975021342935247892,
			0.8547537598970208483,
			0.8744072146753004704,
			0.9075294214410336568,
			0.9101523813406768859,
			0.9280549396020538744,
			0.9391809530012216189,
			0.9539276644089494939,
			0.9623083437067311285,
			0.9684700588501590213
			)[quality] / num
	elif num < den: # upsampling
		return (
			0.7539740617648067467,
			0.8270076656536116122,
			0.8835072979478705291,
			0.8966758456219333651,
			0.9253434087537378838,
			0.9255866674042573239,
			0.9346487800036394900,
			0.9415331868209220190,
			0.9524608799160205752,
			0.9624372769883490220,
			0.9704505626409354324
			)[quality]
	else: # no change in sample rate
		return 1.


#
# =============================================================================
#
#                                Debug utilities
#
# =============================================================================
#


def write_dump_dot(pipeline, filestem, verbose = False):
	"""
	This function needs the environment variable GST_DEBUG_DUMP_DOT_DIR
	to be set.   The filename will be

	os.path.join($GST_DEBUG_DUMP_DOT_DIR, filestem + ".dot")

	If verbose is True, a message will be written to stderr.
	"""
	if "GST_DEBUG_DUMP_DOT_DIR" not in os.environ:
		raise ValueError("cannot write pipeline, environment variable GST_DEBUG_DUMP_DOT_DIR is not set")
	gst.DEBUG_BIN_TO_DOT_FILE(pipeline, gst.DEBUG_GRAPH_SHOW_ALL, filestem)
	if verbose:
		print >>sys.stderr, "Wrote pipeline to %s" % os.path.join(os.environ["GST_DEBUG_DUMP_DOT_DIR"], "%s.dot" % filestem)
