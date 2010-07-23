#!/usr/bin/env python
"""

Unit tests for lal_skymap.

"""
__author__       = "Leo Singer <leo.singer@ligo.org>"
__copyright__    = "Copyright 2010, Leo Singer"


from gstlal.pipeutil import *
from gstlal.lloidparts import mkelems_fast
from gstlal.pipeparts import mkappsink
from gstlal import pipeio
import pylal.xlal.datatypes.snglinspiraltable as sngl
from pylal.datatypes import LIGOTimeGPS
from pylal.date import XLALGreenwichMeanSiderealTime
from pylal import antenna
import random
import numpy
import pylab
import sys


class TestSkymap(object):


	def new_buffer(self, elem, user_data):
		self.outbuf = elem.get_property("last-buffer")
		self.mainloop.quit()

	def __init__(self):
		self.pipeline = gst.Pipeline()
		self.mainloop = gobject.MainLoop()
		self.msg_tupl = None
		bus = self.pipeline.get_bus()
		bus.add_signal_watch()
		bus.connect("message", self.on_message)

	def on_message(self, bus, message):
		if message.type == gst.MESSAGE_EOS:
			self.mainloop.quit()
		elif message.type == gst.MESSAGE_ERROR:
			gerr, dbgmsg = message.parse_error()
			self.msg_tupl = (gerr.domain, gerr.code, gerr.message, dbgmsg)
			self.mainloop.quit()
			sys.exit("error (%s:%d '%s'): %s" % self.msg_tupl)


	def runTest(self):

		ra_deg = int(sys.argv[1]) 
		codec_deg = int(sys.argv[2])

		ifos = ("H1", "L1", "V1")
		start_time = 963704084
		stop_time = start_time + 8
		mid_time = (start_time + stop_time) / 2
		ra = ra_deg * numpy.pi/180
		codec = codec_deg * numpy.pi/180
		dec = numpy.pi/2 - codec
		inc = 0
		pol = 0
		rate = 4096

		colatitude = codec
		longitude = numpy.mod(ra - XLALGreenwichMeanSiderealTime(LIGOTimeGPS(mid_time)), 2*numpy.pi)

		responses = dict(
			(
				ifo,
				complex(*(antenna.response(mid_time, ra, dec, inc, pol, "radians", ifo)[:2]))
			)
			for ifo in ifos
		)

		delays = dict(
			(
				ifo,
				antenna.timeDelay(mid_time, ra, dec, "radians", ifo, "H1")
			)
			for ifo in ifos
		)

		skymap = mkelems_fast(self.pipeline,
			"lal_skymap",
			{
				"bank-filename": "../examples/banks/test_bank.xml"
			}
		)[-1]

		snr_srcs = {}
		sngl_caps = gst.Caps("application/x-lal-snglinspiral,channels=%d" % len(ifos))

		for ifo in ifos:
			snr_srcs[ifo] = mkelems_fast(self.pipeline,
				"lal_numpy_functiongenerator",
				{
					"expression": "%r * 20.0 * exp(-((t - %r)/.0005) ** 2) + random.randn(len(t))" % (responses[ifo], mid_time + delays[ifo]),
					"samplesperbuffer": rate
				},
				"capsfilter", {"caps": gst.Caps("audio/x-raw-complex,width=128,rate=%d" % rate)},
				"taginject", {"tags": "instrument=%s" % ifo},
				skymap,
			)[0]
			snr_srcs[ifo].send_event(gst.event_new_seek(
				1.0, gst.FORMAT_TIME, gst.SEEK_FLAG_NONE,
				gst.SEEK_TYPE_SET, start_time * gst.SECOND,
				gst.SEEK_TYPE_SET, stop_time * gst.SECOND
			))

		appsrc = mkelems_fast(self.pipeline,
			"appsrc",
			{
				"caps": sngl_caps,
				"format": "time",
				"max-bytes": 0
			},
			skymap,
		)[0]
		appsink = mkappsink(self.pipeline, skymap)
		appsink.connect_after('new-buffer', self.new_buffer, None)

		sngl_inspiral_len = len(buffer(sngl.SnglInspiralTable()))
		for buf_time in range(start_time, stop_time):
			if buf_time == mid_time:
				buf = gst.buffer_new_and_alloc(sngl_inspiral_len * len(ifos))
				for i_ifo, ifo in enumerate(ifos):
					s = sngl.SnglInspiralTable()
					trigger_time = LIGOTimeGPS(mid_time + delays[ifo])
					s.end_time = trigger_time.seconds
					s.end_time_ns = trigger_time.nanoseconds
					s.eff_distance = 1.0
					s.ifo = ifo
					s.mass1 = 1.758074
					s.mass2 = 1.124539
					buf[(i_ifo*sngl_inspiral_len):(i_ifo+1)*sngl_inspiral_len] = buffer(s)
			else:
				buf = gst.buffer_new_and_alloc(0)
			buf.timestamp = buf_time * gst.SECOND
			buf.duration = 1 * gst.SECOND
			buf.offset = gst.BUFFER_OFFSET_NONE
			buf.offset_end = gst.BUFFER_OFFSET_NONE
			buf.caps = sngl_caps
			appsrc.emit("push-buffer", buf)

		self.pipeline.set_state(gst.STATE_PLAYING)
		self.mainloop.run()

		theta, phi, span, logp = pipeio.array_from_audio_buffer(self.outbuf).T
		logp = logp.copy()
		logp[numpy.isneginf(logp)] = logp[1-numpy.isneginf(logp)].min()
		print theta
		print phi
		print span
		print logp

		shape = (450, 900)
		print logp.min(), logp.max()
		pylab.pcolor(phi.reshape(shape), theta.reshape(shape), logp.reshape(shape))
		pylab.plot((longitude,), (colatitude,), '+k', markersize=20, markeredgewidth=2)
		ax = pylab.gca()
		ax.autoscale_view(tight=True)
		pylab.ylabel("Colatitude (radians)")
		pylab.xlabel("Longitude (radians)")
		pylab.colorbar()
		ax.invert_yaxis()
		pylab.savefig("%03d-%03d.png" % (ra_deg, codec_deg))


if __name__ == '__main__':
	suite = TestSkymap()
	suite.runTest() 
