#!/usr/bin/env python
#
# Copyright (C) 2010--2013  Kipp Cannon, Chad Hanna, Leo Singer
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
import numpy
import os
import scipy
try:
	from pyfftw.interfaces import scipy_fftpack as fftpack
except ImportError:
	from scipy import fftpack
from scipy import interpolate
import sys
import signal
import warnings

import pygtk
pygtk.require("2.0")
import gobject
gobject.threads_init()
import pygst
pygst.require("0.10")
import gst

from glue.ligolw import utils as ligolw_utils
import lal
import lal.series
import lalsimulation


from gstlal import datasource
from gstlal import pipeparts
from gstlal import pipeio
from gstlal import simplehandler


## @file
# module for reference psds
#
# ### Review Status
#
# | Names                                           | Hash                                     | Date       | Diff to Head of Master      |
# | ----------------------------------------------- | ---------------------------------------- | ---------- | --------------------------- |
# | Florent, Sathya, Duncan Me., Jolien, Kipp, Chad | b3ef077fe87b597578000f140e4aa780f3a227aa | 2014-05-01 | <a href="@gstlal_cgit_diff/python/reference_psd.py?id=HEAD&id2=b3ef077fe87b597578000f140e4aa780f3a227aa">reference_psd.py</a> |
#
# #### Action items
#
# - Make graphs of code and compare with gstreamer graphs
# - Link spectrum movie from DCC
# - Consider exposing the average samples property
# - Check FIR kernel normalization norm


## @package python.reference_psd
# the reference_psd module


#
# =============================================================================
#
#                               PSD Measurement
#
# =============================================================================
#


#
# pipeline handler for PSD measurement
#


class PSDHandler(simplehandler.Handler):
	def __init__(self, *args, **kwargs):
		self.psd = None
		simplehandler.Handler.__init__(self, *args, **kwargs)

	def do_on_message(self, bus, message):
		if message.type == gst.MESSAGE_ELEMENT and message.structure.get_name() == "spectrum":
			self.psd = pipeio.parse_spectrum_message(message)
			return True
		return False


#
# measure_psd()
#

## A pipeline to measure a PSD
#
# @dot
# digraph G {
#	// graph properties
#
#	rankdir=LR;
#	compound=true;
#	node [shape=record fontsize=10 fontname="Verdana"];
#	edge [fontsize=8 fontname="Verdana"];
#
#	// nodes
#
#	"mkbasicsrc()" [URL="\ref datasource.mkbasicsrc()"];
#	capsfilter1 [URL="\ref pipeparts.mkcapsfilter()"];
#	resample [URL="\ref pipeparts.mkresample()"];
#	capsfilter2  [URL="\ref pipeparts.mkcapsfilter()"];
#	queue [URL="\ref pipeparts.mkqueue()"];
#	whiten [URL="\ref pipeparts.mkwhiten()"];
#	fakesink [URL="\ref pipeparts.mkfakesink()"];
#
#	"mkbasicsrc()" -> capsfilter1 -> resample -> capsfilter2 -> queue -> whiten -> fakesink;
# } 
# @enddot
def measure_psd(gw_data_source_info, instrument, rate, psd_fft_length = 8, verbose = False):
	#
	# 8 FFT-lengths is just a ball-parky estimate of how much data is
	# needed for a good PSD, this isn't a requirement of the code (the
	# code requires a minimum of 1)
	#

	if gw_data_source_info.seg is not None and float(abs(gw_data_source_info.seg)) < 8 * psd_fft_length:
		raise ValueError("segment %s too short" % str(gw_data_source_info.seg))

	#
	# build pipeline
	#

	if verbose:
		print >>sys.stderr, "measuring PSD in segment %s" % str(gw_data_source_info.seg)
		print >>sys.stderr, "building pipeline ..."
	mainloop = gobject.MainLoop()
	pipeline = gst.Pipeline("psd")
	handler = PSDHandler(mainloop, pipeline)

	head, statevector, dqvector = datasource.mkbasicsrc(pipeline, gw_data_source_info, instrument, verbose = verbose)
	head = pipeparts.mkcapsfilter(pipeline, head, "audio/x-raw-float, rate=[%d,MAX]" % rate)	# disallow upsampling
	head = pipeparts.mkresample(pipeline, head, quality = 9)
	head = pipeparts.mkcapsfilter(pipeline, head, "audio/x-raw-float, rate=%d" % rate)
	head = pipeparts.mkqueue(pipeline, head, max_size_buffers = 8)
	if gw_data_source_info.seg is not None:
		average_samples = int(round(float(abs(gw_data_source_info.seg)) / (psd_fft_length / 2.) - 1.))
	else:
		#FIXME maybe let the user specify this
		average_samples = 64
	head = pipeparts.mkwhiten(pipeline, head, psd_mode = 0, zero_pad = 0, fft_length = psd_fft_length, average_samples = average_samples, median_samples = 7)
	pipeparts.mkfakesink(pipeline, head)

	#
	# setup signal handler to shutdown pipeline for live data
	#

	if gw_data_source_info.data_source in ("lvshm", "framexmit"):# FIXME what about nds online?
		simplehandler.OneTimeSignalHandler(pipeline)

	#
	# process segment
	#

	if verbose:
		print >>sys.stderr, "putting pipeline into playing state ..."
	if pipeline.set_state(gst.STATE_PLAYING) == gst.STATE_CHANGE_FAILURE:
		raise RuntimeError("pipeline failed to enter playing state")
	if verbose:
		print >>sys.stderr, "running pipeline ..."
	mainloop.run()

	#
	# done
	#

	if verbose:
		print >>sys.stderr, "PSD measurement complete"
	return handler.psd


def write_psd_fileobj(fileobj, psddict, gz = False):
	"""
	Wrapper around make_psd_xmldoc() to write the XML document directly
	to a Python file object.
	"""
	ligolw_utils.write_fileobj(lal.series.make_psd_xmldoc(psddict), fileobj, gz = gz)


def write_psd(filename, psddict, verbose = False, trap_signals = None):
	"""
	Wrapper around make_psd_xmldoc() to write the XML document directly
	to a named file.
	"""
	ligolw_utils.write_filename(lal.series.make_psd_xmldoc(psddict), filename, gz = (filename or "stdout").endswith(".gz"), verbose = verbose, trap_signals = trap_signals)


#
# =============================================================================
#
#                                PSD Utilities
#
# =============================================================================
#


class HorizonDistance(object):
	def __init__(self, f_min, f_max, delta_f, m1, m2, spin1 = (0., 0., 0.), spin2 = (0., 0., 0.), eccentricity = 0., inclination = 0.):
		"""
		Configures the horizon distance calculation for a specific
		waveform model.  The waveform is pre-computed and stored,
		so this initialization step can be time-consuming but
		computing horizon distances from measured PSDs will be
		fast.

		The waveform model's spectrum parameters, for example its
		Nyquist and frequency resolution, need not match the
		parameters for the PSDs that will ultimately be supplied
		but there are some advantages to be had in getting them to
		match.  For example, computing the waveform with a smaller
		delta_f than will be needed will require additional storage
		and consume additional CPU time for the initialization,
		while computing it with too low an f_max or too large a
		delta_f might lead to inaccurate horizon distance
		estimates.

		f_min (Hz) sets the frequency at which the waveform model
		is to begin.

		f_max (Hz) sets the frequency upto which the waveform's
		model is desired.

		delta_f (Hz) sets the frequency resolution of the desired
		waveform model.

		m1, m2 (solar masses) set the component masses of the
		system to model.

		spin1, spin2 (3-component vectors, geometric units) set the
		spins of the component masses.

		eccentricity [0, 1) sets the eccentricity of the system.

		inclination (rad) sets the orbital inclination of the
		system.

		Example:

		>>> # configure for non-spinning, circular, 1.4+1.4 BNS
		>>> horizon_distance = HorizonDistance(10., 1024., 1./32., 1.4, 1.4)
		>>> # populate a PSD for testing
		>>> import lal, lalsimulation
		>>> psd = lal.CreateREAL8FrequencySeries("psd", lal.lal.LIGOTimeGPS(0), 0., 1./32., lal.Unit("strain^2 s"), horizon_distance.model.data.length)
		>>> lalsimulation.SimNoisePSDaLIGODesignSensitivityP1200087(psd, 0.)
		0
		>>> # compute horizon distance
		>>> D, (f, model) = horizon_distance(psd)
		>>> print "%.4g Mpc" % D
		434.5 Mpc
		>>> # compute distance and spectrum for SNR = 25
		>>> D, (f, model) = horizon_distance(psd, 25.)
		>>> "%.4g Mpc" % D
		'139 Mpc'
		>>> f
		array([   10.     ,    10.03125,    10.0625 , ...,  1023.9375 ,
		        1023.96875,  1024.     ])
		>>> model
		array([  8.00942750e-45,   7.95221458e-45,   7.89520620e-45, ...,
		         1.11473675e-49,   1.11465176e-49,   1.11456678e-49])

		NOTE:

		- Currently the SEOBNRv4_ROM waveform model is used, so its
		  limitations with respect to masses, spins, etc., apply.
		  The choice of waveform model is subject to change.
		"""
		self.f_min = f_min
		self.f_max = f_max
		self.m1 = m1
		self.m2 = m2
		self.spin1 = numpy.array(spin1)
		self.spin2 = numpy.array(spin2)
		self.inclination = inclination
		self.eccentricity = eccentricity
		# NOTE:  the waveform models are computed up-to but not
		# including the supplied f_max parameter so we need to pass
		# (f_max + delta_f) if we want the waveform model defined
		# in the f_max bin
		hp, hc = lalsimulation.SimInspiralFD(
			m1 * lal.MSUN_SI, m2 * lal.MSUN_SI,
			spin1[0], spin1[1], spin1[2],
			spin2[0], spin2[1], spin2[2],
			1.0,	# distance (m)
			inclination,
			0.0,	# reference orbital phase (rad)
			0.0,	# longitude of ascending nodes (rad)
			eccentricity,
			0.0,	# mean anomaly of periastron
			delta_f,
			f_min,
			f_max + delta_f,
			100.,	# reference frequency (Hz)
			None,	# LAL dictionary containing accessory parameters
			lalsimulation.GetApproximantFromString("SEOBNRv4_ROM")
		)
		assert hp.data.length > 0, "huh!?  h+ has zero length!"

		#
		# store |h(f)|^2 for source at D = 1 m.  see (5) in
		# arXiv:1003.2481
		#

		self.model = lal.CreateREAL8FrequencySeries(
			name = "signal spectrum",
			epoch = lal.LIGOTimeGPS(0),
			f0 = hp.f0,
			deltaF = hp.deltaF,
			sampleUnits = hp.sampleUnits * hp.sampleUnits,
			length = hp.data.length
		)
		self.model.data.data[:] = numpy.abs(hp.data.data)**2.


	def __call__(self, psd, snr = 8.):
		"""
		Compute the horizon distance for the configured waveform
		model given the PSD and the SNR at which the horizon is
		defined (default = 8).  Equivalently, from a PSD and an
		observed SNR compute and return the amplitude of the
		configured waveform's spectrum required to achieve that
		SNR.

		The return value is a two-element tuple.  The first element
		is the horizon distance in Mpc.  The second element is,
		itself, a two-element tuple containing two vectors giving
		the frequencies and amplitudes of the waveform model's
		spectrum scaled so as to have the given SNR.  The vectors
		are clipped to the range of frequencies that were used for
		the SNR integral.

		The parameters of the PSD, for example its Nyquist and
		frequency resolution, need not match the parameters of the
		configured waveform model.  In the event of a mismatch, the
		waveform model is resampled to the frequencies at which the
		PSD has been measured.

		The inspiral spectrum returned has the same units as the
		PSD and is normalized so that the SNR is

		SNR^2 = \int (inspiral_spectrum / psd) df

		That is, the ratio of the inspiral spectrum to the PSD
		gives the spectral density of SNR^2.
		"""
		#
		# frequencies at which PSD has been measured
		#

		f = psd.f0 + numpy.arange(psd.data.length) * psd.deltaF

		#
		# nearest-neighbour interpolation of waveform model
		# evaluated at PSD's frequency bins
		#

		indexes = ((f - self.model.f0) / self.model.deltaF).round().astype("int").clip(0, self.model.data.length - 1)
		model = self.model.data.data[indexes]

		#
		# range of indexes for integration
		#

		kmin = (max(psd.f0, self.model.f0, self.f_min) - psd.f0) / psd.deltaF
		kmin = int(round(kmin))
		kmax = (min(psd.f0 + psd.data.length * psd.deltaF, self.model.f0 + self.model.data.length * self.model.deltaF, self.f_max) - psd.f0) / psd.deltaF
		kmax = int(round(kmax)) + 1
		assert kmin < kmax, "PSD and waveform model do not intersect"

		#
		# SNR for source at D = 1 m <--> D in m for source w/ SNR =
		# 1.  see (3) in arXiv:1003.2481
		#

		f = f[kmin:kmax]
		model = model[kmin:kmax]
		D = math.sqrt(4. * (model / psd.data.data[kmin:kmax]).sum() * psd.deltaF)

		#
		# distance at desired SNR
		#

		D /= snr

		#
		# scale inspiral spectrum by distance to achieve desired SNR
		#

		model *= 4. / D**2.

		#
		# D in Mpc for source with specified SNR, and waveform
		# model
		#

		return D / (1e6 * lal.PC_SI), (f, model)


def effective_distance_factor(inclination, fp, fc):
	"""
	Returns the ratio of effective distance to physical distance for
	compact binary mergers.  Inclination is the orbital inclination of
	the system in radians, fp and fc are the F+ and Fx antenna factors.
	See lal.ComputeDetAMResponse() for a function to compute antenna
	factors.  The effective distance is given by

	Deff = effective_distance_factor * D

	See Equation (4.3) of arXiv:0705.1514.
	"""
	cos2i = math.cos(inclination)**2
	return 1.0 / math.sqrt(fp**2 * (1+cos2i)**2 / 4 + fc**2 * cos2i)


class PSDFirKernel(object):

	def __init__(self):
		self.revplan = None
		self.fwdplan = None

	def psd_to_linear_phase_whitening_fir_kernel(self, psd, invert = True, nyquist = None):
		"""
		Compute an acausal finite impulse-response filter kernel from a power
		spectral density conforming to the LAL normalization convention,
		such that if colored Gaussian random noise with the given PSD is fed
		into an FIR filter using the kernel the filter's output will
		be zero-mean unit-variance Gaussian random noise.  The PSD must be
		provided as a lal.REAL8FrequencySeries object.

		The phase response of this filter is 0, just like whitening done in
		the frequency domain.

		The return value is the tuple (kernel, latency, sample rate).  The
		kernel is a numpy array containing the filter kernel, the latency
		is the filter latency in samples and the sample rate is in Hz.  The
		kernel and latency can be used, for example, with gstreamer's stock
		audiofirfilter element.
		"""
		#
		# this could be relaxed with some work
		#

		assert psd.f0 == 0.0

		#
		# extract the PSD bins and determine sample rate for kernel
		#

		data = psd.data.data / 2
		sample_rate = 2 * int(round(psd.f0 + len(data) * psd.deltaF))

		#
		# remove LAL normalization
		#

		data *= sample_rate

		#
		# Change Nyquist frequency if requested
		#

		if nyquist is not None:
			assert nyquist <= sample_rate / 2.
			sample_rate = nyquist * 2
			data = data[:int(nyquist / psd.deltaF) + 1]

		#
		# compute the FIR kernel.  it always has an odd number of samples
		# and no DC offset.
		#

		data[0] = data[-1] = 0.0
		if invert:
			data_nonzeros = (data != 0.)
			data[data_nonzeros] = 1./data[data_nonzeros]
		# repack data:  data[0], data[1], 0, data[2], 0, ....
		tmp = numpy.zeros((2 * len(data) - 1,), dtype = data.dtype)
		tmp[len(data)-1:] = data
		#tmp[:len(data)] = data
		data = tmp

		kernel_fseries = lal.CreateCOMPLEX16FrequencySeries(
			name = "double sided psd",
			epoch = lal.LIGOTimeGPS(0),
			f0 = 0.0,
			deltaF = psd.deltaF,
			length = len(data),
			sampleUnits = lal.Unit("strain s")
		)

		kernel_tseries = lal.CreateCOMPLEX16TimeSeries(
			name = "timeseries of whitening kernel",
			epoch = lal.LIGOTimeGPS(0.),
			f0 = 0.,
			deltaT = 1.0 / sample_rate,
			length = len(data),
			sampleUnits = lal.Unit("strain")
		)


		# FIXME check for change in length
		if self.revplan is None:
			self.revplan = lal.CreateReverseCOMPLEX16FFTPlan(len(data), 1)

		kernel_fseries.data.data = numpy.sqrt(data) + 0.j
		lal.COMPLEX16FreqTimeFFT(kernel_tseries, kernel_fseries, self.revplan)
		kernel = numpy.real(kernel_tseries.data.data)
		kernel = numpy.roll(kernel, (len(data) - 1) / 2)[:] / sample_rate * 2

		#
		# apply a Tukey window whose flat bit is 50% of the kernel.
		# preserve the FIR kernel's square magnitude
		#

		norm_before = numpy.dot(kernel, kernel)
		kernel *= lal.CreateTukeyREAL8Window(len(data), .5).data.data
		kernel *= math.sqrt(norm_before / numpy.dot(kernel, kernel))

		#
		# the kernel's latency
		#

		latency = (len(data) - 1) / 2

		#
		# done
		#

		return kernel, latency, sample_rate


	def linear_phase_fir_kernel_to_minimum_phase_whitening_fir_kernel(self, linear_phase_kernel, sample_rate):
		"""
		Compute the minimum-phase response filter (zero latency) associated with a
		linear-phase response filter (latency equal to half the filter length). 

		From "Design of Optimal Minimum-Phase Digital FIR Filters Using
		Discrete Hilbert Transforms", IEEE Trans. Signal Processing, vol. 48,
		pp. 1491-1495, May 2000.

		The return value is the tuple (kernel, phase response).  The kernel is
		a numpy array containing the filter kernel.  The kernel can be used,
		for example, with gstreamer's stock audiofirfilter element.
		"""
		#
		# compute abs of FFT of kernel
		#

		# FIXME check for change in length
		if self.fwdplan is None:
			self.fwdplan = lal.CreateForwardCOMPLEX16FFTPlan(len(linear_phase_kernel), 1)
		if self.revplan is None:
			self.revplan = lal.CreateReverseCOMPLEX16FFTPlan(len(linear_phase_kernel), 1)

		deltaT = 1. / sample_rate
		deltaF = 1. / (len(linear_phase_kernel) * deltaT)
		working_length = len(linear_phase_kernel)

		kernel_tseries = lal.CreateCOMPLEX16TimeSeries(
			name = "timeseries of whitening kernel",
			epoch = lal.LIGOTimeGPS(0.),
			f0 = 0.,
			deltaT = deltaT,
			length = working_length,
			sampleUnits = lal.Unit("strain")
		)
		kernel_tseries.data.data = linear_phase_kernel

		absX = lal.CreateCOMPLEX16FrequencySeries(
			name = "absX",
			epoch = lal.LIGOTimeGPS(0),
			f0 = 0.0,
			deltaF = deltaF,
			length = working_length,
			sampleUnits = lal.Unit("strain s")
		)

		logabsX = lal.CreateCOMPLEX16FrequencySeries(
			name = "absX",
			epoch = lal.LIGOTimeGPS(0),
			f0 = 0.0,
			deltaF = deltaF,
			length = working_length,
			sampleUnits = lal.Unit("strain s")
		)

		cepstrum = lal.CreateCOMPLEX16TimeSeries(
			name = "cepstrum",
			epoch = lal.LIGOTimeGPS(0.),
			f0 = 0.,
			deltaT = deltaT,
			length = working_length,
			sampleUnits = lal.Unit("strain")
		)

		theta = lal.CreateCOMPLEX16FrequencySeries(
			name = "theta",
			epoch = lal.LIGOTimeGPS(0),
			f0 = 0.0,
			deltaF = deltaF,
			length = working_length,
			sampleUnits = lal.Unit("strain s")
		)

		min_phase_kernel = lal.CreateCOMPLEX16TimeSeries(
			name = "min phase kernel",
			epoch = lal.LIGOTimeGPS(0.),
			f0 = 0.,
			deltaT = deltaT,
			length = working_length,
			sampleUnits = lal.Unit("strain")
		)

		lal.COMPLEX16TimeFreqFFT(absX, kernel_tseries, self.fwdplan)
		absX.data.data[:] = numpy.roll(abs(absX.data.data), -working_length // 2 + 1) * sample_rate

		#
		# compute the cepstrum of the kernel
		# (i.e., the iFFT of the log of the abs of the FFT of the kernel)
		#

		logabsX.data.data[:] = numpy.roll(numpy.log(absX.data.data), +working_length // 2 + 1)
		lal.COMPLEX16FreqTimeFFT(cepstrum, logabsX, self.revplan)
		cepstrum.data.data /= sample_rate

		#
		# compute sgn
		#

		sgn = scipy.ones(working_length)
		sgn[0] = 0.
		sgn[(len(sgn) + 1) // 2] = 0.
		sgn[(len(sgn) + 1) // 2:] *= -1
		cepstrum.data.data *= sgn

		#
		# compute theta
		#

		lal.COMPLEX16TimeFreqFFT(theta, cepstrum, self.fwdplan)
		theta.data.data[:] = numpy.roll(-1.j * theta.data.data, -working_length // 2) * sample_rate

		#
		# compute minimum phase kernel
		#

		absX.data.data[:] = numpy.roll(absX.data.data * scipy.exp(1.j * theta.data.data), +working_length // 2 + 1) / sample_rate
		lal.COMPLEX16FreqTimeFFT(min_phase_kernel, absX, self.revplan)

		kernel = min_phase_kernel.data.data.real

		#
		# this kernel needs to be reversed to follow conventions used with the
		# audiofirfilter and lal_firbank elements
		#

		kernel = kernel[-1::-1]

		#
		# done
		#

		return kernel, -theta.data.data


def interpolate_psd(psd, deltaF):
	#
	# no-op?  and disallow resolution reduction
	#

	if deltaF == psd.deltaF:
		return psd
	assert deltaF < psd.deltaF

	#
	# interpolate PSD by clipping/zero-padding time-domain impulse
	# response of equivalent whitening filter
	#

	#from scipy import fftpack
	#psd_data = psd.data.data
	#x = numpy.zeros((len(psd_data) * 2 - 2,), dtype = "double")
	#psd_data = numpy.where(psd_data, psd_data, float("inf"))
	#x[0] = 1 / psd_data[0]**.5
	#x[1::2] = 1 / psd_data[1:]**.5
	#x = fftpack.irfft(x)
	#if deltaF < psd.deltaF:
	#	x *= numpy.cos(numpy.arange(len(x)) * math.pi / (len(x) + 1))**2
	#	x = numpy.concatenate((x[:(len(x) / 2)], numpy.zeros((int(round(len(x) * psd.deltaF / deltaF)) - len(x),), dtype = "double"), x[(len(x) / 2):]))
	#else:
	#	x = numpy.concatenate((x[:(int(round(len(x) * psd.deltaF / deltaF)) / 2)], x[-(int(round(len(x) * psd.deltaF / deltaF)) / 2):]))
	#	x *= numpy.cos(numpy.arange(len(x)) * math.pi / (len(x) + 1))**2
	#x = 1 / fftpack.rfft(x)**2
	#psd_data = numpy.concatenate(([x[0]], x[1::2]))

	#
	# interpolate PSD with linear interpolator
	#

	#psd_data = psd.data.data
	#f = psd.f0 + numpy.arange(len(psd_data)) * psd.deltaF
	#interp = interpolate.interp1d(f, psd_data, bounds_error = False)
	#f = psd.f0 + numpy.arange(round(len(psd_data) * psd.deltaF / deltaF)) * deltaF
	#psd_data = interp(f)

	#
	# interpolate log(PSD) with cubic spline.  note that the PSD is
	# clipped at 1e-300 to prevent nan's in the interpolator (which
	# doesn't seem to like the occasional sample being -inf)
	#

	psd_data = psd.data.data
	psd_data = numpy.where(psd_data, psd_data, 1e-300)
	f = psd.f0 + numpy.arange(len(psd_data)) * psd.deltaF
	interp = interpolate.splrep(f, numpy.log(psd_data), s = 0)
	f = psd.f0 + numpy.arange(round((len(psd_data) - 1) * psd.deltaF / deltaF) + 1) * deltaF
	psd_data = numpy.exp(interpolate.splev(f, interp, der = 0))

	#
	# return result
	#

	psd = lal.CreateREAL8FrequencySeries(
		name = psd.name,
		epoch = psd.epoch,
		f0 = psd.f0,
		deltaF = deltaF,
		sampleUnits = psd.sampleUnits,
		length = len(psd_data)
	)
	psd.data.data = psd_data

	return psd


def movingmedian(psd, window_size):
	"""
	Assumes that the underlying PSD doesn't have variance, i.e., that there
	is no median / mean correction factor required
	"""
	data = psd.data.data
	datacopy = numpy.copy(data)
	for i in range(window_size, len(data)-window_size):
		datacopy[i] = numpy.median(data[i-window_size:i+window_size])
	psd = lal.CreateREAL8FrequencySeries(
		name = psd.name,
		epoch = psd.epoch,
		f0 = psd.f0,
		deltaF = psd.deltaF,
		sampleUnits = psd.sampleUnits,
		length = len(datacopy)
	)
	psd.data.data = datacopy
	return psd


def polyfit(psd, minsample, maxsample, order, verbose = False):
	# f / f_min between f_min and f_max, i.e. f[0] here is 1
	f = numpy.arange(maxsample - minsample) * psd.deltaF + 1
	data = psd.data.data[minsample:maxsample]

	logf = numpy.linspace(numpy.log(f[0]), numpy.log(f[-1]), 100000)
	interp = interpolate.interp1d(numpy.log(f), numpy.log(data))
	data = interp(logf)
	p = numpy.poly1d(numpy.polyfit(logf, data, order))
	if verbose:
		print >> sys.stderr, "\nFit polynomial is: \n\nlog(PSD) = \n", p, "\n\nwhere x = f / f_min\n"
	data = numpy.exp(p(numpy.log(f)))
	olddata = psd.data.data
	olddata[minsample:maxsample] = data
	psd = lal.CreateREAL8FrequencySeries(
		name = psd.name,
		epoch = psd.epoch,
		f0 = psd.f0,
		deltaF = psd.deltaF,
		sampleUnits = psd.sampleUnits,
		length = len(olddata)
	)
	psd.data.data = olddata
	return psd

def one_second_highpass_kernel(rate, cutoff = 12):
	highpass_filter_fd =  numpy.ones(rate, dtype=complex)
	highpass_filter_fd[:int(cutoff)] = 0.
	highpass_filter_fd[-int(cutoff):] = 0.
	highpass_filter_fd[rate/2-1:rate/2+1] = 0.
	highpass_filter_td = fftpack.ifft(highpass_filter_fd)
	highpass_filter_td = numpy.roll(numpy.real(highpass_filter_td), rate/2)
	highpass_filter_kernel = numpy.zeros(len(highpass_filter_td)+1)
	highpass_filter_kernel[:-1] = highpass_filter_td[:]
	x = numpy.arange(len(highpass_filter_kernel))
	mid = len(x) / 2.
	highpass_filter_kernel *= 1. - (x-mid)**2 / mid**2
	return highpass_filter_kernel
