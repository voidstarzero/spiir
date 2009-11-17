# Copyright (C) 2009  LIGO Scientific Collaboration
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
import cmath
import numpy
from scipy import interpolate
from scipy import linalg
import sys


from pylal import datatypes as laltypes
from pylal import lalfft
from pylal import spawaveform


import templates


__author__ = "Kipp Cannon <kipp.cannon@ligo.org>, Chad Hanna <chad.hanna@ligo.org>, Drew Keppel <drew.keppel@ligo.org>"
__version__ = "FIXME"
__date__ = "FIXME"


#
# =============================================================================
#
#                           Inspiral Template Stuff
#
# =============================================================================
#


def interpolate_psd(psd, deltaF):
	# FIXME:  maybe better to do linear interpolation in log-log
	# co-ordinates to provide power-law transitions between samples;
	# hard to make it work at DC.
	data = psd.data
	interp = interpolate.interp1d(psd.f0 + numpy.arange(len(data)) * psd.deltaF, data, bounds_error = False)
	return laltypes.REAL8FrequencySeries(
		name = psd.name,
		epoch = psd.epoch,
		f0 = psd.f0,
		deltaF = deltaF,
		data = interp(psd.f0 + numpy.arange(int(len(data) * psd.deltaF / deltaF)) * deltaF)
	)


def generate_template(template_bank_row, f_low, sample_rate, duration, order = 7, end_freq = "light_ring"):
	z = numpy.empty(int(sample_rate * duration), "cdouble")

	spawaveform.waveform(template_bank_row.mass1, template_bank_row.mass2, order, 1.0 / duration, 1.0 / sample_rate, f_low, spawaveform.ffinal(template_bank_row.mass1, template_bank_row.mass2, end_freq), z)

	return laltypes.COMPLEX16FrequencySeries(
		name = "template",
		epoch = laltypes.LIGOTimeGPS(0),
		f0 = 0.0,
		deltaF = 1.0 / duration,
		sampleUnits = laltypes.LALUnit("strain"),
		data = z[:len(z) // 2 + 1]
	)


def generate_templates(template_table, psd, f_low, sample_rate, duration, verbose = False):
	length = int(sample_rate * duration)

	psd = interpolate_psd(psd, 1.0 / duration)

	revplan = lalfft.XLALCreateReverseCOMPLEX16FFTPlan(length, 1)
	tseries = laltypes.COMPLEX16TimeSeries(
		data = numpy.zeros((length,), dtype = "cdouble")
	)

	template_bank = numpy.zeros((2 * len(template_table), int(sample_rate * duration)), dtype = "double")

	for i, row in enumerate(template_table):
		if verbose:
			print >>sys.stderr, "generating template %d/%d:  m1 = %g, m2 = %g" % (i + 1, len(template_table), row.mass1, row.mass2)

		fseries = generate_template(row, f_low, sample_rate, duration)

		lalfft.XLALWhitenCOMPLEX16FrequencySeries(fseries, psd)

		lalfft.XLALCOMPLEX16FreqTimeFFT(tseries, templates.add_quadrature_phase(fseries, length), revplan)

		data = tseries.data

		data *= cmath.sqrt(2 / numpy.dot(data, numpy.conj(data)))

		template_bank[(2 * i + 0), :] = data.real
		template_bank[(2 * i + 1), :] = data.imag

	return template_bank


def decompose_templates(template_bank, tolerance):
	#
	# sum-of-squares for each template (row).
	#

	chifacs = (template_bank * template_bank).sum(1)

	#
	# adjust tolerance according to local norm
	#

	tolerance = 1 - (1 - tolerance) / chifacs.max()

	#
	# S.V.D.
	#

	U, s, Vh = linalg.svd(template_bank.T)

	#
	# determine component count
	#

	residual = numpy.sqrt((s * s).cumsum() / numpy.dot(s, s))
	n = residual.searchsorted(tolerance) + 1

	#
	# clip decomposition
	#

	U = U[:,:n]
	s = s[:n]
	Vh = Vh[:,:n]

	#
	# pre-scale Vh by s
	#

	Vh *= s

	#
	# done.
	#

	return U.T, s, Vh.T, chifacs
