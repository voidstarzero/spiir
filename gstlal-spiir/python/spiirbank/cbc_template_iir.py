# Copyright (C) 2010-2012 Shaun Hooper
# Copyright (C) 2013-2014 Qi Chu, David Mckenzie, Kipp Cannon, Chad Hanna, Leo Singer
# Copyright (C) 2015 Qi Chu, Shin Chung, David Mckenzie, Yan Wang
# Copyright (C) 2017-2018 Joel Bosveld
# Copyright (C) 2020 Alex Codoreanu
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

import os
import sys
import numpy
import scipy
import cmath
import math
import lal
import lalsimulation
import logging
import tempfile
from gstlal import cbc_template_fir
from glue.ligolw import ligolw, lsctables, array, param, utils, types
from gstlal.pipeio import repack_complex_array_to_real, repack_real_array_to_complex
from gstlal.spiirbank.optimizer import optimize_a1

Attributes = ligolw.sax.xmlreader.AttributesImpl

# will be DEPRECATED once the C SPIIR coefficient code be swig binded
from gstlal.spiirbank import spiir_decomp as spawaveform


# FIXME:  require calling code to provide the content handler
class DefaultContentHandler(ligolw.LIGOLWContentHandler):
    pass


array.use_in(DefaultContentHandler)
param.use_in(DefaultContentHandler)
lsctables.use_in(DefaultContentHandler)


class XMLContentHandler(ligolw.LIGOLWContentHandler):
    pass


# supported approximants
ValidApproximantsFD = set(("SpinTaylorT4", "SEOBNRv4_ROM"))

# copied from gstlal-inspiral/ templates.py
gstlal_IMR_approximants = set(('EOBNRv2', 'IMRPhenomC', 'SEOBNRv4_ROM', 'SEOBNRv2_ROM_DoubleSpin'))


def condition_imr_template(approximant, data, epoch_time, sample_rate_max,
                           max_ringtime):
    assert -len(
        data
    ) / sample_rate_max <= epoch_time < 0.0, "Epoch returned follows a different convention"
    # find the index for the peak sample using the epoch returned by
    # the waveform generator
    epoch_index = -int(epoch_time * sample_rate_max) - 1
    # align the peaks according to an overestimate of max rinddown
    # time for a given split bank
    target_index = len(data) - 1 - int(sample_rate_max * max_ringtime)
    # rotate phase so that sample with peak amplitude is real
    phase = numpy.arctan2(data[epoch_index].imag, data[epoch_index].real)
    data *= numpy.exp(-1.j * phase)
    data = numpy.roll(data, target_index - epoch_index)
    print "epoch_index %d, target_index %d, roll index %d" % (
        epoch_index, target_index, target_index - epoch_index)
    # re-taper the ends of the waveform that got cyclically permuted
    # around the ring
    tukey_beta = 2. * abs(target_index - epoch_index) / float(len(data))
    assert 0. <= tukey_beta <= 1., "waveform got rolled WAY too much"
    data *= lal.CreateTukeyREAL8Window(len(data), tukey_beta).data.data
    # done
    return data, target_index


def normalized_autocorrelation(fseries, revplan):
    data = fseries.data.data
    fseries = lal.CreateCOMPLEX16FrequencySeries(
        name=fseries.name,
        epoch=fseries.epoch,
        f0=fseries.f0,
        deltaF=fseries.deltaF,
        sampleUnits=fseries.sampleUnits,
        length=len(data))
    fseries.data.data = data * numpy.conj(data)
    tseries = lal.CreateCOMPLEX16TimeSeries(name="timeseries",
                                            epoch=fseries.epoch,
                                            f0=fseries.f0,
                                            deltaT=1. /
                                            (len(data) * fseries.deltaF),
                                            length=len(data),
                                            sampleUnits=lal.DimensionlessUnit)
    tseries.data.data = numpy.empty((len(data), ), dtype="cdouble")
    lal.COMPLEX16FreqTimeFFT(tseries, fseries, revplan)
    data = tseries.data.data
    tseries.data.data = data / data[0]
    return tseries


# Round a number up to the nearest power of 2
def ceil_pow_2(x):
    x = int(math.ceil(x))
    x -= 1
    n = 1
    while n and (x & (x + 1)):
        x |= x >> n
        n *= 2
    return x + 1


def add_quadrature_phase(fseries, n):
    """
    From the Fourier transform of a real-valued function of
    time, compute and return the Fourier transform of the
    complex-valued function of time whose real component is the
    original time series and whose imaginary component is the
    quadrature phase of the real part.  fseries is a LAL
    COMPLEX16FrequencySeries and n is the number of samples in
    the original time series.
    """
    #
    # positive frequencies include Nyquist if n is even
    #

    have_nyquist = not (n % 2)

    #
    # shuffle frequency bins
    #

    positive_frequencies = numpy.array(fseries.data.data)  # work with copy
    positive_frequencies[0] = 0  # set DC to zero
    zeros = numpy.zeros((len(positive_frequencies), ), dtype="cdouble")
    if have_nyquist:
        # complex transform never includes positive Nyquist
        positive_frequencies = positive_frequencies[:-1]

    #
    # prepare output frequency series
    #

    out_fseries = lal.CreateCOMPLEX16FrequencySeries(
        name=fseries.name,
        epoch=fseries.epoch,
        f0=fseries.f0,  # caution: only 0 is supported
        deltaF=fseries.deltaF,
        sampleUnits=fseries.sampleUnits,
        length=len(zeros) + len(positive_frequencies) - 1)
    out_fseries.data.data = numpy.concatenate(
        (zeros, 2 * positive_frequencies[1:]))

    return out_fseries


# end of copy


def tukeywindow(data, samps=200.):
    assert (len(data) >= 2 * samps
            )  # make sure that the user is requesting something sane
    tp = float(samps) / len(data)
    return lal.CreateTukeyREAL8Window(len(data), tp).data.data


# Capital Theta function, defined in lalsuite GeneratePPNInspiral.h


def gen_eta(m1, m2):
    eta = (m1 * m2) / (m1 + m2)**2
    return eta


def gen_PPN_theta(eta, mtot, t_c, t):

    if t <= t_c:
        theta_t = eta * lal.MSUN_SI / (5. * lal.MTSUN_SI * mtot) * (t_c - t)
    else:
        theta_t = 0
    return theta_t


def gen_PPN_freq(eta, mtot, nPN, t_c, t):

    theta = gen_PPN_theta(eta, mtot, t_c, t)
    p = numpy.ones(nPN * 2, dtype='double')
    p[1] = 0
    if theta == 0:
        f_t = 0
    else:
        f_t = lal.MSUN_SI / (8 * numpy.pi * lal.MTSUN_SI * mtot) * (
            p[0] * theta**(-3 / 8.) + p[1] * theta**(-1 / 2.) + p[2] *
            (743 / 2688. + 11 / 32. * eta) * theta**(-5 / 8.) -
            p[3] * 3 * numpy.pi / 10 * theta**(-3 / 4.) + p[4] *
            (1855099 / 14450688. + 56975 / 258048. * eta +
             371 / 2048. * eta**2) * theta**(-7 / 8.) - p[5] *
            (7729 / 21504. + 3 / 256. * eta) * numpy.pi * theta**(-1.))

    return f_t


def calc_fhigh_neglat_PPN(row, negative_latency, verbose=False):

    eta = gen_eta(row.mass1, row.mass2)
    mtot = (row.mass1 + row.mass2) * lal.MSUN_SI
    nPN = 3  # 3 PN hard-coded
    t_c = negative_latency
    t = 0

    f_t = gen_PPN_freq(eta, mtot, nPN, t_c, t)

    if verbose:
        logging.info("end freq calculated by PPN  %f Hz," % f_t)

    return f_t


# Calculate the phase and amplitude from hc and hp
# Unwind the phase (this is slow - consider C extension or using SWIG
# if speed is needed)


def calc_amp_phase(hc, hp):
    amp = numpy.sqrt(hc * hc + hp * hp)
    phase = numpy.arctan2(hc, hp)

    #Unwind the phase
    #Based on the unwinding codes in pycbc
    #and the old LALSimulation interface
    count = 0
    prevval = None
    phaseUW = phase

    #Pycbc uses 2*PI*0.7 for some reason
    #We use the more conventional PI (more in line with MATLAB)
    thresh = lal.PI
    for index, val in enumerate(phase):
        if prevval is None:
            pass
        elif prevval - val >= thresh:
            count = count + 1
        elif val - prevval >= thresh:
            count = count - 1

        phaseUW[index] = phase[index] + count * lal.TWOPI
        prevval = val

    tmp = phaseUW[0]
    for index, val in enumerate(phase):
        phaseUW[index] = phaseUW[index] - tmp

    phase = phaseUW
    return amp, phase


def sample_rates_array_to_str(sample_rates):
    return ",".join([str(a) for a in sample_rates])


def sample_rates_str_to_array(sample_rates_str):
    return numpy.array([int(a) for a in sample_rates_str.split(',')])


def compute_autocorrelation_mask(autocorrelation):
    '''
    Given an autocorrelation time series, estimate the optimal
    autocorrelation length to use and return a matrix which masks
    out the unwanted elements. FIXME TODO for now just returns
    ones
    '''
    return numpy.ones(autocorrelation.shape, dtype="int")


def normalized_crosscorr(a, b, autocorrelation_length=201):

    n_temp = len(a)
    if autocorrelation_length > n_temp:
        raise ValueError, "autocorrelation length (%d) cannot be larger than the template length (%d)" % (
            autocorrelation_length, n_temp)
    if n_temp != len(b):
        pad_length = max(n_temp, len(b))
        b_pad = numpy.zeros(pad_length * 1, dtype=numpy.cdouble)
        b_pad[-len(b):] = b
        b = b_pad
        a_pad = numpy.zeros(pad_length * 1, dtype=numpy.cdouble)
        a_pad[-len(a):] = a
        a = a_pad
    #raise ValueError, "len(a) should be the same as len(b)"

    af = scipy.fft(a)
    bf = scipy.fft(b)
    corr = scipy.ifft(af * numpy.conj(bf))
    abs_corr = abs(corr)
    max_idx = numpy.where(abs_corr == max(abs_corr))[0][0]

    half_len = autocorrelation_length // 2
    auto_bank = numpy.zeros(autocorrelation_length, dtype='cdouble')
    if max_idx == 0:
        auto_bank[::-1] = numpy.concatenate(
            (corr[-half_len:], corr[:half_len + 1]))
        auto_bank /= corr[max_idx]
    else:
        print "Warning: max of autocorrelation happen at position [%d]" % max_idx
        temp_idx = (n_temp - 1) // 2
        temp_corr = numpy.concatenate((corr[-temp_idx:], corr[:-temp_idx]))
        max_idx = numpy.where(abs(temp_corr) == max(abs(temp_corr)))[0][0]

        if max_idx - half_len < 0 or max_idx + half_len + 1 > n_temp:
            raise ValueError, "cannot generate cross-correlation of the given (autocorrelation) length, insufficient data"
        else:
            auto_bank[::-1] = temp_corr[max_idx - half_len:max_idx + half_len +
                                        1]
            auto_bank /= temp_corr[max_idx]

    return auto_bank


def gen_template_working_state(sngl_inspiral_table,
                               f_low=30.,
                               sampleRate=2048.):

    # Some input checking to avoid incomprehensible error messages
    if not sngl_inspiral_table:
        raise ValueError("template list is empty")
    if f_low < 0.:
        raise ValueError("f_low must be >= 0.: %s" % repr(f_low))

    # working f_low to actually use for generating the waveform.  pick
    # template with lowest chirp mass, compute its duration starting
    # from f_low;  the extra time is 10% of this plus 3 cycles (3 /
    # f_low);  invert to obtain f_low corresponding to desired padding.
    # NOTE:  because SimInspiralChirpStartFrequencyBound() does not
    # account for spin, we set the spins to 0 in the call to
    # SimInspiralChirpTimeBound() regardless of the component's spins.
    template = min(sngl_inspiral_table, key=lambda row: row.mchirp)
    tchirp = lalsimulation.SimInspiralChirpTimeBound(
        f_low, template.mass1 * lal.MSUN_SI, template.mass2 * lal.MSUN_SI, 0.,
        0.)
    working_f_low = lalsimulation.SimInspiralChirpStartFrequencyBound(
        1.1 * tchirp + 3. / f_low, template.mass1 * lal.MSUN_SI,
        template.mass2 * lal.MSUN_SI)

    # FIXME: This is a hack to calculate the maximum length of given table, we
    # know that working_f_low_extra_time is about 1/10 of the maximum duration
    working_f_low_extra_time = .1 * tchirp + 1.0
    length_max = int(round(working_f_low_extra_time * 10 * sampleRate))

    # Add 32 seconds to template length for PSD ringing, round up to power of 2 count of samples, this is for psd length to whiten the template later.
    # Multiply by two so that we can later take only half to get rid of wraparound in time domain.
    working_length = 2 * ceil_pow_2(length_max + round(
        (16.0 + working_f_low_extra_time) * sampleRate))
    working_duration = float(working_length) / sampleRate

    working_state = {}
    working_state["working_f_low"] = working_f_low
    working_state["working_length"] = working_length
    working_state["working_duration"] = working_duration
    working_state["length_max"] = length_max
    return working_state


def lalwhitenFD_and_convert2TD(psd, fseries, sampleRate, working_state,
                               flower):
    """
    This function can be called to calculate a whitened waveform using lalwhiten.
    This is for comparison of whitening the waveform using lalwhiten in frequency domain
    and our own whitening in time domain.
    and use this waveform to calculate a autocorrelation function.


    from pylal import datatypes as lal
    from pylal import lalfft
    lalwhiten_amp, lalwhiten_phase = lalwhiten(psd, hp, working_length, working_duration, sampleRate, length_max)
    lalwhiten_wave = lalwhiten_amp * numpy.exp(1j * lalwhiten_phase)
        auto_h = numpy.zeros(length_max * 1, dtype=numpy.cdouble)
        auto_h[-len(lalwhiten_wave):] = lalwhiten_wave
    auto_bank_new = normalized_crosscorr(auto_h, auto_h, autocorrelation_length)
    """
    revplan = lal.CreateReverseCOMPLEX16FFTPlan(
        working_state["working_length"], 1)
    tseries = lal.CreateCOMPLEX16TimeSeries(
        name="timeseries",
        epoch=lal.LIGOTimeGPS(0.),
        f0=0.,
        deltaT=1.0 / sampleRate,
        length=working_state["working_length"],
        sampleUnits=lal.Unit("strain"))

    #
    # whiten and add quadrature phase ("sine" component)
    #

    if psd is not None:
        lal.WhitenCOMPLEX16FrequencySeries(fseries, psd)
    fseries = add_quadrature_phase(fseries, working_state["working_length"])

    #
    # transform template to time domain
    #

    lal.COMPLEX16FreqTimeFFT(tseries, fseries, revplan)

    data = tseries.data.data

    # FIXME: need to condition for IMR wave templates
    data *= tukeywindow(data, samps=32)
    # This is to normalize whitened template so it = h_{whitened at 1MPC}(t)
    # NOTE: because
    # XLALWhitenCOMPLEX16FrequencySeries() computed
    #
    # \tilde{h}'_{k} = \sqrt{2 \Delta f} \tilde{h}_{k} / \sqrt{S_{k}}
    # need to devide the time domain whitened waveform by \sqrt{2 \Delta f}
    data /= numpy.sqrt(2. / working_state["working_duration"])

    #pdb.set_trace()
    return data


def gen_templateFD(row, approximant, sample_rate, duration, f_low, f_high):
    """
    Generate a single frequency-domain template, which
     (1) is band-limited between f_low and f_high,
     (2) has an IFFT which is duration seconds long and
     (3) has an IFFT which is sampled at sample_rate Hz
    """
    if approximant not in ValidApproximantsFD:
        print("Unsupported approximant given %s" % approximant)

    # FIXME use hcross somday?
    # We don't here because it is not guaranteed to be orthogonal
    # and we add orthogonal phase later

    parameters = {}
    parameters['m1'] = lal.MSUN_SI * row.mass1
    parameters['m2'] = lal.MSUN_SI * row.mass2
    parameters['S1x'] = row.spin1x
    parameters['S1y'] = row.spin1y
    parameters['S1z'] = row.spin1z
    parameters['S2x'] = row.spin2x
    parameters['S2y'] = row.spin2y
    parameters['S2z'] = row.spin2z
    parameters['distance'] = 1.e6 * lal.PC_SI
    parameters['inclination'] = 0.
    parameters['phiRef'] = 0.
    parameters['longAscNodes'] = 0.
    parameters['eccentricity'] = 0.
    parameters['meanPerAno'] = 0.
    parameters['deltaF'] = 1.0 / duration
    parameters['f_min'] = f_low
    parameters['f_max'] = f_high
    parameters['f_ref'] = 0.
    parameters['LALparams'] = None
    parameters['approximant'] = lalsimulation.GetApproximantFromString(
        str(approximant))

    hplus, hcross = lalsimulation.SimInspiralFD(**parameters)
    # NOTE assumes fhigh is the Nyquist frequency!!!
    assert len(hplus.data.data) == int(round(sample_rate * duration)) // 2 + 1
    return hplus


def gen_templateTD(row, approximant, sampleRate, flow):
    # NOTE: IMRPhenomB is FD

    parameters = {}
    parameters['m1'] = lal.MSUN_SI * row.mass1
    parameters['m2'] = lal.MSUN_SI * row.mass2
    parameters['S1x'] = row.spin1x
    parameters['S1y'] = row.spin1y
    parameters['S1z'] = row.spin1z
    parameters['S2x'] = row.spin2x
    parameters['S2y'] = row.spin2y
    parameters['S2z'] = row.spin2z
    parameters['distance'] = 1.e6 * lal.PC_SI
    parameters['inclination'] = 0.
    parameters['phiRef'] = 0.
    parameters['longAscNodes'] = 0.
    parameters['eccentricity'] = 0.
    parameters['meanPerAno'] = 0.
    parameters['deltaT'] = 1.0 / sampleRate
    parameters['f_min'] = flow
    parameters['f_ref'] = 0.
    parameters['LALparams'] = None
    parameters['approximant'] = lalsimulation.GetApproximantFromString(
        str(approximant))

    hp, hc = lalsimulation.SimInspiralTD(**parameters)
    return hp, hc
    # NOTE assumes fhigh is the Nyquist frequency!!!
    # assert len(hplus.data.data) == int(round(sample_rate * duration))//2 +1
    # The following code will plot the original autocorrelation function
    #ori_amp, ori_phase = calc_amp_phase(hc.data.data, hp.data.data)
    #ori_wave = ori_amp * numpy.exp(1j * ori_phase)
    #auto_ori = numpy.zeros(working_length * 1, dtype=numpy.cdouble)
    #auto_ori[-len(ori_wave):] = ori_wave
    #auto_bank_ori = normalized_crosscorr(auto_ori, auto_ori, 201)

    #import matplotlib.pyplot as plt
    #axis_x = numpy.linspace(0, len(phase), len(phase))
    #plt.plot(axis_x, phase)
    #plt.show()


def calc_fhigh_neglat(fseries, working_state, negative_latency, verbose=False):

    # get some units
    working_length = working_state["working_length"]
    working_duration = working_state["working_duration"]
    sampleRate = working_length / working_duration
    dt = 1.0 / (sampleRate)
    df = 1.0 / (working_duration)

    #
    # the length of tmp_fdata is n/2 + 1 where n is the length of the input
    # real signal
    #

    tmp_fdata = numpy.array(fseries.data.data)

    #
    # estimate the end frequency of the uncut waveform first
    # the end frequency is estimated at the overlap accumulation over 0.999
    # i.e. find the smallest index where cumsum(|(fdata)|^2) > 0.999
    # 0.999 is chosen to match the turning point of the cumsum
    #

    cumag = numpy.cumsum(numpy.multiply(tmp_fdata, numpy.conj(tmp_fdata)))
    cumag = cumag / cumag[-1]
    idx_end = numpy.argmax(cumag >= 0.999)

    # the high freq cut off at original uncut waveform

    fhigh = idx_end * df

    #
    # transform template to time domain
    # note here need to use rfft
    # so the converted time domain waveform is real and its length is n,
    # consistent with the original waveform
    #

    tdata = numpy.fft.irfft(tmp_fdata) * df
    assert len(tdata) == working_length

    # apply smoothing window

    tdata *= tukeywindow(tdata, samps=32)

    # truncate at the end
    nsample_cut = int(negative_latency * sampleRate)
    if nsample_cut > 0:
        tdata_neglat = tdata[:-nsample_cut]
    else:
        tdata_neglat = tdata

    # apply smoothing window

    tdata_neglat *= tukeywindow(tdata_neglat, samps=32)

    #
    # estimate the end freq of cut waveform using the same method for
    # uncut waveform above
    #

    # output size is (n-nsample_cut)/2+1
    fdata_neglat = numpy.fft.rfft(tdata_neglat) * dt

    cumag_neglat = numpy.cumsum(
        numpy.multiply(fdata_neglat, numpy.conj(fdata_neglat)))
    cumag_neglat = cumag_neglat / cumag_neglat[-1]
    idx_end = numpy.argmax(cumag_neglat >= 0.999)
    # the high freq cut off at the cut waveform
    df_neglat = 1. / float(working_duration - negative_latency)
    fhigh_neglat = idx_end * df_neglat
    # feed it back to the original waveform
    idx_end_ori = int(fhigh_neglat / df)
    # fseries.data.data[idx_end_ori:] = 0.
    if verbose:
        logging.info("end freq of uncut waveform %f Hz," \
            "end freq of negative latency waveform  %f Hz" % (fhigh,
            fhigh_neglat))
    return fhigh, fhigh_neglat, tdata, fseries.data.data, fdata_neglat


def gen_whitened_amp_phase(psd,
                           approximant,
                           waveform_domain,
                           sampleRate,
                           flower,
                           working_state,
                           row,
                           snr_cut=1.0,
                           is_frequency_whiten=1,
                           negative_latency=0,
                           verbose=False):
    """ Generates whitened waveform from given parameters and PSD, then returns the amplitude and the phase.

    Parameters
    ----------
    psd :
    Power spectral density
    sampleRate :
    Sampling rate in Hz
    flower :
    Low frequency cut-off
    is_freq_whiten :
    Whether perform the whitening in the frequency domain (if True),
    or perform the whitening in the time domain (otherwise).
    Time-domain whitening is quicker and better-conditioned, but less accurate.
    Use frequency-domain whitening by default.
    working_length :
    Number of samples pre-allocated for the template.
    working_duration :
    The period in seconds corresponding to working_length.
    length_max :
    Parameter for frequency-domain whitening.
    row: snglinspiral_table row include information:
    m1, m2:
        component mass
    spin1x, spin1y, spin1z :
    Spin parameters of compact object 1.
    spin2x, spin2y, spin2z :
    Spin parameters of compact object 2.

    Returns
    -------
    Amplitude :
    The amplitude of the whitened template
    Phase :
    The phase of the whitened template
    """

    # initilize the end frequency: fhigh
    fhigh = sampleRate / 2.

    if waveform_domain == "FD" and is_frequency_whiten == 1:
        #
        # generate "cosine" component of frequency-domain template.
        # waveform is generated for a canonical distance of 1 Mpc.
        #
        fseries = gen_templateFD(row, approximant, sampleRate,
                                 working_state["working_duration"], flower,
                                 sampleRate / 2.)
        #
        # acquire the merger time which is not necessarily the end time of
        # the template
        #
        epoch_time = fseries.epoch.gpsSeconds + fseries.epoch.gpsNanoSeconds * 1.e-9

        # estimate the end frequency using power FFT
        if negative_latency > 0:
            fhigh = calc_fhigh_neglat(fseries,
                                      working_state,
                                      negative_latency,
                                      verbose=verbose)[1]
            #
            # compare it with end fhigh estimated using the theoretical post-
            # Newtonian method
            #
            if verbose:
                fhigh_PPN = calc_fhigh_neglat_PPN(row,
                                                  negative_latency,
                                                  verbose=verbose)

        # whiten the FD waveform and transform it back to TD
        data_full = lalwhitenFD_and_convert2TD(psd, fseries, sampleRate,
                                               working_state, flower)
        epoch_index = -int(epoch_time * sampleRate) - len(data_full)
        if verbose:
            logging.info("waveform chose from FD")

    elif waveform_domain == "TD" and is_frequency_whiten == 1:

        #
        # the very first implemenation of SPIIR was using TD waveform
        # without whitening. Then the whitening is done by each filter
        # diving PSD at the the corresponding central frequency of the filter.
        # It produces much less filters but the approximated response is bad.
        # This is because the bandwidth of the SPIIR filters did not hold
        # after whitening. Places where PSD fluctuate rapidly need to be
        # patched with more filters.
        #

        logging.error("TD waveform here not conditioned, caution to use.")
        # get the TD waveform
        hplus, hcross = gen_templateTD(row, approximant, sampleRate, flower)
        # transfomr the TD waveform to FD
        tmptdata = numpy.zeros(working_state["working_length"], )
        tmptdata[-hplus.data.length:] = hplus.data.data

        tmptseries = lal.CreateREAL8TimeSeries(name="template",
                                               epoch=lal.LIGOTimeGPS(0),
                                               f0=0.0,
                                               deltaT=1.0 / sampleRate,
                                               sampleUnits=lal.Unit("strain"),
                                               length=len(tmptdata))
        tmptseries.data.data = tmptdata

        # prepare the working space for FD whitening
        fwdplan = lal.CreateForwardREAL8FFTPlan(
            working_state["working_length"], 1)
        fworkspace = lal.CreateCOMPLEX16FrequencySeries(
            name="template",
            epoch=lal.LIGOTimeGPS(0),
            f0=0.0,
            deltaF=1.0 / working_state["working_duration"],
            length=(working_state["working_length"] // 2 + 1),
            sampleUnits=lal.Unit("strain s"))

        lal.CreateREAL8TimeFreqFFT(fworkspace, tmptseries, fwdplan)
        tmpfseries = numpy.copy(fworkspace.data)

        fseries = lal.CreateCOMPLEX16FrequencySeries(
            name="template",
            epoch=lal.LIGOTimeGPS(0),
            f0=0.0,
            deltaF=1.0 / working_state["working_duration"],
            sampleUnits=lal.Unit("strain"),
            length=len(tmpfseries))
        fseries.data.data = tmpfseries
        # FIXME: epoch_index not supported
        # whiten the FD waveform and transform it back to TD
        data_full = lalwhitenFD_and_convert2TD(psd, fseries, sampleRate,
                                               working_state, flower)
        if verbose:
            logging.info("waveform chose from TD")
    else:
        # FIXME: the hp, hc are now in frequency domain.
        # Need to transform them first into time domain to perform following whitening
        print >> sys.stderr, "Time domain whitening not supported"
        sys.exit()

    # Working length is initially doubled so we can avoid wraparound of templates
    data_full = data_full[len(data_full) // 2:]

    cumag = numpy.cumsum(numpy.multiply(data_full, numpy.conj(data_full)))
    cumag = cumag / cumag[-1]
    filter_start = numpy.argmax(cumag >= 1 - snr_cut)

    data = data_full[filter_start:-int(1 + negative_latency * sampleRate)]

    amp_lalwhiten, phase_lalwhiten = calc_amp_phase(numpy.imag(data),
                                                    numpy.real(data))
    # adjust the offset of the merger time relative to the end time of the cut
    # template
    epoch_index += int(1 + negative_latency * sampleRate)

    if verbose:
        logging.info(
            "original template length %d, cut to construct spiir coeffs %d, epoch_index %d"
            % (len(data_full), len(data), epoch_index))

    return amp_lalwhiten, phase_lalwhiten, data, data_full, epoch_index, fhigh


def gen_spiir_response(length, a1, b0, delay):
    u = spawaveform.iirresponse(length, a1, b0, delay)

    u_pad = numpy.zeros(length * 1, dtype=numpy.cdouble)
    u_pad[-len(u):] = u

    u_rev = u[::-1]
    u_rev_pad = numpy.zeros(length * 1, dtype=numpy.cdouble)
    u_rev_pad[-len(u_rev):] = u_rev

    return u_rev_pad


def pad_data(data, length):

    # get the original waveform
    h = data
    h_pad = numpy.zeros(length * 1, dtype=numpy.cdouble)
    h_pad[-len(h):] = h
    return h_pad


def gen_spiir_coeffs(amp,
                     phase,
                     length,
                     padding=1.3,
                     epsilon=0.02,
                     alpha=.99,
                     beta=0.25,
                     autocorrelation_length=201):
    # make the iir filter coeffs
    a1, b0, delay = spawaveform.iir(amp, phase, epsilon, alpha, beta, padding)

    # get the IIR response
    u_rev_pad = gen_spiir_response(length, a1, b0, delay)

    return a1, b0, delay, u_rev_pad


def gen_norm_spiir_coeffs(amp,
                          phase,
                          length,
                          padding=1.3,
                          epsilon=0.02,
                          alpha=.99,
                          beta=0.25):
    # make the iir filter coeffs
    a1, b0, delay = spawaveform.iir(amp, phase, epsilon, alpha, beta, padding)

    # get the IIR response
    u_rev_pad = gen_spiir_response(length, a1, b0, delay)

    # normalize the approximate waveform so its inner-product is 2
    norm_u = abs(numpy.dot(u_rev_pad, numpy.conj(u_rev_pad)))
    u_rev_pad *= cmath.sqrt(2 / norm_u)

    # normalize the iir coefficients
    b0 *= cmath.sqrt(2 / norm_u)

    return a1, b0, delay, u_rev_pad


class Bank(object):
    def __init__(self, logname=None):
        self.template_bank_filename = None
        self.bank_filename = None
        self.logname = logname
        self.sngl_inspiral_columns = (
            "process_id", "ifo", "search", "channel", "end_time",
            "end_time_ns", "end_time_gmst", "impulse_time", "impulse_time_ns",
            "template_duration", "event_duration", "amplitude", "eff_distance",
            "coa_phase", "mass1", "mass2", "mchirp", "mtotal", "eta", "kappa",
            "chi", "tau0", "tau2", "tau3", "tau4", "tau5", "ttotal", "psi0",
            "psi3", "alpha", "alpha1", "alpha2", "alpha3", "alpha4", "alpha5",
            "alpha6", "beta", "f_final", "snr", "chisq", "chisq_dof",
            "bank_chisq", "bank_chisq_dof", "cont_chisq", "cont_chisq_dof",
            "sigmasq", "rsqveto_duration", "Gamma0", "Gamma1", "Gamma2",
            "Gamma3", "Gamma4", "Gamma5", "Gamma6", "Gamma7", "Gamma8",
            "Gamma9", "spin1x", "spin1y", "spin1z", "spin2x", "spin2y",
            "spin2z", "event_id")

        #self.sngl_inspiral_table = lsctables.New(lsctables.SnglInspiralTable, columns = self.sngl_inspiral_columns)
        self.sngl_inspiral_table = None

        self.sample_rates = []
        self.A = {}
        self.B = {}
        self.D = {}
        self.autocorrelation_bank = None
        self.autocorrelation_mask = None
        self.sigmasq = []
        self.matches = []
        self.flower = None
        self.epsilon = None
        self.negative_latency = 0

    def build_from_tmpltbank(self,
                             filename,
                             templates=None,
                             sampleRate=None,
                             negative_latency=0,
                             padding=1.3,
                             approximant='SpinTaylorT4',
                             waveform_domain="FD",
                             epsilon_start=0.02,
                             epsilon_min=0.001,
                             epsilon_max=None,
                             epsilon_factor=2,
                             filters_min=0,
                             filters_max=None,
                             filters_per_loglen_min=0,
                             filters_per_loglen_max=None,
                             initial_overlap_min=0,
                             b0_optimized_overlap_min=0,
                             final_overlap_min=0,
                             initial_overlap_max=1,
                             b0_optimized_overlap_max=1,
                             final_overlap_max=1,
                             nround_max=10,
                             alpha=.99,
                             beta=0.25,
                             flower=15,
                             snr_cut=0.998,
                             all_psd=None,
                             autocorrelation_length=201,
                             downsample=False,
                             optimizer_options={},
                             verbose=False,
                             debug=False,
                             keep_track=True,
                             output_file=None,
                             remote_log=False,
                             remote_db_engine=None,
                             remote_log_table_name=None,
                             contenthandler=DefaultContentHandler):
        """
            Build SPIIR template bank from physical parameters, e.g. mass, spin.
            """
        if remote_log:
            if remote_db_engine is None or remote_log_table_name is None:
                print "you told me to keep a remote_log but" \
                      "you did not provide me a remote database engine connection " \
                      "or a table to write to."
                exit(0)
            else:
                try:
                    from pandas import DataFrame
                    remote_log_df = DataFrame()
                except Exception as local_exception:
                    print "Failed to import from pandas import DataFrame\n"
                    print "Exception is:\n{}".format(local_exception)

        if keep_track:
            if output_file is None:
                print "you told me to keep track of my status but" \
                      "you did not provide the output file name"
                exit(0)
            else:
                if '.xml.gz' not in output_file:
                    print "the output file should end in '.xml.gz'"
                    exit(0)
                else:
                    track_file = output_file.replace('.xml.gz', '_status.txt')
                    try:
                        with open(track_file, 'w') as w:
                            w.writelines('')
                    except IOError:
                        print "I can't keep track of my run status. \n" \
                              "Please check that you have permission to write to the" \
                              "output directory."

        # Check various inputs are consistent
        assert epsilon_min <= epsilon_start
        assert epsilon_max is None or epsilon_start <= epsilon_max
        assert filters_max is None or filters_min <= filters_max
        assert filters_per_loglen_max is None or filters_per_loglen_min <= filters_per_loglen_max

        # Open template bank file
        self.template_bank_filename = filename
        self.tmpltbank_xmldoc = utils.load_filename(
            filename, contenthandler=contenthandler, verbose=verbose)

        sngl_inspiral_table = lsctables.SnglInspiralTable.get_table(
            self.tmpltbank_xmldoc)

        # put the bank table in and fill in missing attributes with zero
        self.sngl_inspiral_table = lsctables.New(lsctables.SnglInspiralTable)
        for row in sngl_inspiral_table:
            newrow = self.fill_sngl_row(row)
            self.sngl_inspiral_table.append(newrow)

        self.flower = flower
        self.epsilon = epsilon_start
        self.alpha = alpha
        self.beta = beta
        self.negative_latency = negative_latency

        if debug:
            print 'bank object created'

        if sampleRate is None:
            fFinal = max(sngl_inspiral_table.getColumnByName("f_final"))
            sampleRate = int(2**(numpy.ceil(numpy.log2(fFinal) + 1)))

        if verbose:
            logging.basicConfig(format='%(asctime)s %(message)s',
                                level=logging.DEBUG)
            logging.info("fmin = %f, samplerate = %f" % (flower, sampleRate))

        # Check parity of autocorrelation length
        if autocorrelation_length is not None:
            if not (autocorrelation_length % 2):
                raise ValueError, "autocorrelation_length must be odd (got %d)" % autocorrelation_length
            self.autocorrelation_bank = numpy.zeros(
                (len(self.sngl_inspiral_table), autocorrelation_length),
                dtype="cdouble")
            self.autocorrelation_mask = compute_autocorrelation_mask(
                self.autocorrelation_bank)
        else:
            self.autocorrelation_bank = None
            self.autocorrelation_mask = None

        # This occasionally breaks with certain template banks
        # Can just specify a certain instrument as a hack fix
        psd = all_psd[self.sngl_inspiral_table[0].ifo]

        working_state = gen_template_working_state(self.sngl_inspiral_table,
                                                   flower,
                                                   sampleRate=sampleRate)

        if debug:
            print 'working state generated'
            print 'smoothing PSD'

        # Smooth the PSD and interpolate to required resolution
        if psd is not None:
            psd = cbc_template_fir.condition_psd(
                psd,
                1.0 / working_state["working_duration"],
                minfs=(working_state["working_f_low"], flower),
                maxfs=(sampleRate / 2.0 * 0.90, sampleRate / 2.0))
            # This is to avoid nan amp when whitening the amp
            #tmppsd = psd.data
            #tmppsd[numpy.isinf(tmppsd)] = 1.0
            #psd.data = tmppsd

        if verbose:
            logging.info("condition of psd finished")

        #
        # condition the template if necessary (e.g. line up IMR
        # waveforms by peak amplitude)
        #

        Amat = {}
        Bmat = {}
        Dmat = {}

        # templates need depend on the size of the input bank
        if templates is None:
            templates = list(range(len(self.sngl_inspiral_table)))

        if debug:
            print 'starting template generation'
            print 'will create {} templates'.format(len(templates))

        for tmp, row in enumerate(self.sngl_inspiral_table):
            if tmp in templates:
                if debug:
                    print tmp

                if keep_track:
                    with open(track_file, 'w') as w:
                        w.writelines('{}'.format(row))

                if remote_log:
                    remote_log_df.loc[tmp, 'template_id'] = tmp

                spiir_match = -1
                epsilon = epsilon_start
                epsilon_a = None
                epsilon_b = None
                n_filters = 0

                # data = the cutted template.
                # cut at the beginning to avoid long low SNR accumulation
                # cut at the end for negative latency template
                # data_full = original uncut template
                # fhigh is the estimated end frequency of data
                amp, \
                phase, \
                data, \
                data_full, \
                epoch_index, \
                fhigh = gen_whitened_amp_phase(psd,
                                               approximant,
                                               waveform_domain,
                                               sampleRate,
                                               flower,
                                               working_state,
                                               row,
                                               is_frequency_whiten=1,
                                               snr_cut=snr_cut,
                                               negative_latency=negative_latency,
                                               verbose=verbose)

                # fill in the field end with the epoch time, so the pipeline will
                # read this information and adjust for the merger time for the trigger
                row.end = lal.LIGOTimeGPS(float(epoch_index) / sampleRate)
                row.f_final = float(fhigh)

                # get the padded length, so SPIIR approximated waveform u_rev_pad
                # the original cut template h_pad, and the original one will be
                # padded to the same length
                pad_length = ceil_pow_2(len(data_full) + autocorrelation_length)
                nround = 1

                # Collate various requirements
                spiir_match_min = max(initial_overlap_min,
                                      b0_optimized_overlap_min, final_overlap_min)
                n_filters_min = max(filters_min,
                                    filters_per_loglen_min * numpy.log2(len(data)))
                n_filters_max = None
                if filters_per_loglen_max is not None:
                    n_filters_max = filters_per_loglen_max * numpy.log2(len(data))
                    if filters_max is not None:
                        n_filters_max = min(filters_max, n_filters_max)
                else:
                    n_filters_max = filters_max
                if verbose:
                    logging.info(
                        "spiir_match_min %s, n_filters_min %s, n_filters_max %s" %
                        (spiir_match_min, n_filters_min, n_filters_max))

                if remote_log:
                    remote_log_df.loc[tmp, 'spiir_match_min'] = spiir_match_min
                    remote_log_df.loc[tmp, 'n_filters_min'] = n_filters_min
                    remote_log_df.loc[tmp, 'n_filters_max'] = n_filters_max

                # h_pad is just the padded cut template
                h_pad = pad_data(data, pad_length)

                # sigmasq is based on cut template
                fs = float(sampleRate)
                df = 1.0 / (pad_length / fs)
                h_pad_comp = numpy.zeros(pad_length, dtype="cdouble")
                h_pad_comp[:len(h_pad)] = h_pad
                h_pad_fft = numpy.fft.fft(h_pad_comp) / fs

                this_sigmasq = abs((h_pad_fft * h_pad_fft.conjugate()).sum() * df)

                # normalize the cut waveform so its inner-product is 2
                norm_h = abs(numpy.dot(h_pad, numpy.conj(h_pad)))
                h_pad *= cmath.sqrt(2 / norm_h)

                # Iterate to get the filter delays matching our requirements
                while (True):
                    a1, \
                    b0, \
                    delay, \
                    u_rev_pad = gen_norm_spiir_coeffs(amp,
                                                      phase,
                                                      pad_length,
                                                      epsilon=epsilon,
                                                      alpha=alpha,
                                                      beta=beta,
                                                      padding=padding)

                    # compute the SNR
                    spiir_match = abs(numpy.dot(u_rev_pad,
                                                numpy.conj(h_pad))) / 2.0

                    optimizer_state = None
                    if (nround == 1):
                        original_match = spiir_match
                        original_filters = len(a1)

                    n_filters = len(delay)
                    if verbose:
                        logging.info("number of rounds %d, epsilon_a %s, epsilon %f, epsilon_b %s, spiir overlap with"
                                     " template %f, number of filters %d" % (nround,
                                                                             epsilon_a,
                                                                             epsilon,
                                                                             epsilon_b,
                                                                             spiir_match,
                                                                             n_filters))

                    if remote_log:
                        remote_log_df.loc[tmp, 'nround'] = nround
                        remote_log_df.loc[tmp, 'epsilon_a'] = epsilon_a
                        remote_log_df.loc[tmp, 'epsilon'] = epsilon
                        remote_log_df.loc[tmp, 'epsilon_b'] = epsilon_b
                        remote_log_df.loc[tmp, 'spiir_match'] = spiir_match
                        remote_log_df.loc[tmp, 'n_filters'] = n_filters

                    nround += 1
                    epsilon_dir = 0
                    if n_filters_max is not None and n_filters > n_filters_max:
                        # we need to increase epsilon to decrease filters
                        epsilon_dir = 1
                    else:
                        if n_filters >= n_filters_min:
                            # Filters are correct, so we can now do necessary optimization in epsilon loop
                            spiir_match_min = initial_overlap_min
                            spiir_match_max = initial_overlap_max
                            if spiir_match >= spiir_match_min and (
                                    b0_optimized_overlap_min > 0
                                    or final_overlap_min > 0):
                                # optimizer uses convention that template is normalized to 1 not 2
                                spiir_match_min = b0_optimized_overlap_min
                                spiir_match_max = b0_optimized_overlap_max
                                if verbose:
                                    print >> sys.stderr, "Pass -1, overlap %f" % spiir_match

                                a1, \
                                b0, \
                                spiir_match, \
                                optimizer_state = optimize_a1(a1,
                                                              delay,
                                                              h_pad / numpy.sqrt(2),
                                                              passes=0,
                                                              verbose=verbose,
                                                              return_state=True)

                                b0 *= numpy.sqrt(2)

                                if spiir_match >= b0_optimized_overlap_min and (
                                        final_overlap_min > 0):
                                    spiir_match_min = final_overlap_min
                                    spiir_match_max = final_overlap_max
                                    a1, \
                                    b0, \
                                    spiir_match = optimize_a1(a1,
                                                              delay,
                                                              h_pad / numpy.sqrt(2),
                                                              state=optimizer_state,
                                                              **optimizer_options)

                                    b0 *= numpy.sqrt(2)

                        if n_filters < n_filters_min or spiir_match < spiir_match_min:
                            # we need to decrease epsilon to increase filters and match
                            epsilon_dir = -1
                        elif spiir_match > spiir_match_max:
                            # we need to increase epsilon to decrease filters
                            epsilon_dir = 1

                    if epsilon_dir == 1:
                        epsilon_a = epsilon
                        if epsilon_b:
                            epsilon = numpy.sqrt(epsilon_b *
                                                 epsilon)  # geometric mean
                        elif epsilon_max > 0 and epsilon < epsilon_max:
                            epsilon = min(epsilon * epsilon_factor, epsilon_max)
                        elif epsilon_max > 0:
                            if verbose:
                                logging.info(
                                    "failed to meet requirements (epsilon_max)")
                            break
                        else:
                            epsilon = epsilon * epsilon_factor
                    elif epsilon_dir == -1:
                        epsilon_b = epsilon
                        if epsilon_a:
                            epsilon = numpy.sqrt(epsilon_a *
                                                 epsilon)  # geometric mean
                        elif epsilon > epsilon_min:
                            epsilon = max(epsilon / epsilon_factor, epsilon_min)
                        else:
                            if verbose:
                                logging.info(
                                    "failed to meet requirements (epsilon_min)")
                            break
                    else:
                        break

                    if epsilon_a is not None and epsilon_a > epsilon or epsilon_b is not None and epsilon > epsilon_b or epsilon_a is not None and epsilon_b is not None and epsilon_a >= epsilon_b:
                        if verbose:
                            logging.info(
                                "failed to meet requirements (inconsistency)")
                        break
                    if nround > nround_max:
                        if verbose:
                            logging.info(
                                "failed to meet requirements (nround_max)")
                        break

                # Once we have iterated to get the final filter delays, optimize if not already done
                if optimizer_options is not None and (
                        optimizer_state is None
                        or not (spiir_match >= b0_optimized_overlap_min
                                and final_overlap_min > 0)):
                    # optimizer uses convention that template is normalized to 1 not 2
                    a1, \
                    b0, \
                    spiir_match = optimize_a1(a1,
                                              delay,
                                              h_pad / numpy.sqrt(2),
                                              state=optimizer_state,
                                              **optimizer_options)

                    b0 *= numpy.sqrt(2)

                # get the final SPIIR approximated waveform
                u_rev_pad = gen_spiir_response(pad_length, a1, b0, delay)

                # This is actually the cross correlation between the original waveform and this approximation
                # h_full_pad is just the padded original template
                h_full_pad = pad_data(data_full, pad_length)

                # normalize the cut waveform so its inner-product is 2
                norm_h_full = abs(numpy.dot(h_full_pad, numpy.conj(h_full_pad)))
                h_full_pad *= cmath.sqrt(2 / norm_h)

                self.autocorrelation_bank[tmp, :] = \
                    normalized_crosscorr(h_full_pad,
                                         u_rev_pad,
                                         autocorrelation_length)

                # save the overlap of spiir reconstructed waveform with the cut template
                self.matches.append(spiir_match)
                # also take into account that spiir approximant not matching 100% of the original waveform
                self.sigmasq.append(this_sigmasq * spiir_match * spiir_match)

                if verbose:
                    logging.info(
                        "template %4.0d/%4.0d, m1 = %10.6f m2 = %10.6f, epsilon = %1.4f:  %4.0d filters, %10.8f match. original_eps = %1.4f: %4.0d filters, %10.8f match"
                        % (tmp + 1, len(self.sngl_inspiral_table), row.mass1,
                           row.mass2, epsilon, n_filters, spiir_match,
                           epsilon_start, original_filters, original_match))

                if remote_log:
                    remote_log_df.loc[tmp, 'len_sngl_inspiral_table'] = len(self.sngl_inspiral_table)
                    remote_log_df.loc[tmp, 'mass1'] = row.mass1
                    remote_log_df.loc[tmp, 'mass2'] = row.mass2
                    remote_log_df.loc[tmp, 'epsilon'] = epsilon
                    remote_log_df.loc[tmp, 'n_filters'] = n_filters
                    remote_log_df.loc[tmp, 'spiir_match'] = spiir_match
                    remote_log_df.loc[tmp, 'epsilon_start'] = epsilon_start
                    remote_log_df.loc[tmp, 'original_filters'] = original_filters
                    remote_log_df.loc[tmp, 'original_match'] = original_match

                # get the filter frequencies
                fs = -1. * numpy.angle(a1) / 2 / numpy.pi  # Normalised freqeuncy
                a1dict = {}
                b0dict = {}
                delaydict = {}

                if downsample:
                    min_M = 1
                    max_M = int(2**numpy.floor(numpy.log2(sampleRate / flower)))
                    # iterate over the frequencies and put them in the right downsampled bin
                    for i, f in enumerate(fs):
                        M = int(
                            max(min_M, 2**-numpy.ceil(numpy.log2(
                                f * 2.0 * padding))))  # Decimation factor
                        M = max(min_M, M)

                        if M > max_M:
                            continue

                        a1dict.setdefault(sampleRate / M, []).append(a1[i]**M)
                        newdelay = numpy.ceil((delay[i] + 1) / (float(M)))
                        b0dict.setdefault(sampleRate / M, []).append(
                            b0[i] * M**0.5 * a1[i]**(newdelay * M - delay[i]))
                        delaydict.setdefault(sampleRate / M, []).append(newdelay)
                    #logging.info("sampleRate %4.0d, filter %3.0d, M %2.0d, f %10.9f, delay %d, newdelay %d" %
                    # (sampleRate, i, M, f, delay[i], newdelay))
                else:
                    a1dict[int(sampleRate)] = a1
                    b0dict[int(sampleRate)] = b0
                    delaydict[int(sampleRate)] = delay

                # store the coeffs
                for k in a1dict.keys():
                    Amat.setdefault(k, []).append(a1dict[k])
                    Bmat.setdefault(k, []).append(b0dict[k])
                    Dmat.setdefault(k, []).append(delaydict[k])

        max_rows = max([len(Amat[rate]) for rate in Amat.keys()])
        for rate in Amat.keys():
            self.sample_rates.append(rate)
            # get ready to store the coefficients
            max_len = max([len(i) for i in Amat[rate]])
            DmatMin = min([min(elem) for elem in Dmat[rate]])
            DmatMax = max([max(elem) for elem in Dmat[rate]])
            if verbose:
                logging.info(
                    "rate %d, dmin %d, dmax %d, max_row %d, max_len %d" %
                    (rate, DmatMin, DmatMax, max_rows, max_len))

            if remote_log:
                remote_log_df.loc[tmp, 'rate'] = rate
                remote_log_df.loc[tmp, 'DmatMin'] = DmatMin
                remote_log_df.loc[tmp, 'DmatMax'] = DmatMax
                remote_log_df.loc[tmp, 'max_rows'] = max_rows
                remote_log_df.loc[tmp, 'max_len'] = max_len

            self.A[rate] = numpy.zeros((max_rows, max_len),
                                       dtype=numpy.complex128)
            self.B[rate] = numpy.zeros((max_rows, max_len),
                                       dtype=numpy.complex128)
            self.D[rate] = numpy.zeros((max_rows, max_len), dtype=numpy.int32)
            self.D[rate].fill(DmatMin)

            for i, Am in enumerate(Amat[rate]):
                self.A[rate][i, :len(Am)] = Am
            for i, Bm in enumerate(Bmat[rate]):
                self.B[rate][i, :len(Bm)] = Bm
            for i, Dm in enumerate(Dmat[rate]):
                self.D[rate][i, :len(Dm)] = Dm

        if remote_log:
            remote_log_df['filename'] = filename
            remote_log_df.to_sql(remote_log_table_name,
                                 con=remote_db_engine,
                                 chunksize=remote_log_df.filename.size,
                                 if_exists='append',
                                 index=False)
            del remote_log_df


    def downsample_bank(self, flower=15, padding=1.3, verbose=True):
        Amat = {}
        Bmat = {}
        Dmat = {}

        rate = self.A.keys()
        if len(rate) != 1:
            logging.info("Bank already downsampled")
            return
        rate = rate[0]
        sampleRate = rate
        max_rows = max([len(self.A[rate]) for rate in self.A.keys()])
        for row in range(max_rows):
            a1 = numpy.trim_zeros(self.A[rate][row, :], 'b')
            b0 = self.B[rate][row, :len(a1)]
            delay = self.D[rate][row, :len(a1)]
            fs = numpy.abs(
                numpy.angle(a1)) / 2 / numpy.pi  # Normalised freqeuncy

            a1dict = {}
            b0dict = {}
            delaydict = {}

            min_M = 1
            max_M = int(2**numpy.floor(numpy.log2(sampleRate / flower)))
            # iterate over the frequencies and put them in the right downsampled bin
            for i, f in enumerate(fs):
                M = int(
                    max(min_M, 2**-numpy.ceil(numpy.log2(
                        f * 2.0 * padding))))  # Decimation factor
                M = min(max_M, max(min_M, M))

                a1dict.setdefault(sampleRate / M, []).append(a1[i]**M)
                newdelay = numpy.ceil((delay[i] + 1) / (float(M)))
                b0dict.setdefault(sampleRate / M, []).append(
                    b0[i] * M**0.5 * a1[i]**(newdelay * M - delay[i]))
                delaydict.setdefault(sampleRate / M, []).append(newdelay)

            # store the coeffs
            for k in a1dict.keys():
                Amat.setdefault(k, []).append(a1dict[k])
                Bmat.setdefault(k, []).append(b0dict[k])
                Dmat.setdefault(k, []).append(delaydict[k])

        self.A = {}
        self.B = {}
        self.D = {}

        for rate in Amat.keys():
            self.sample_rates.append(rate)
            # get ready to store the coefficients
            max_len = max([len(i) for i in Amat[rate]])
            DmatMin = min([min(elem) for elem in Dmat[rate]])
            DmatMax = max([max(elem) for elem in Dmat[rate]])
            if verbose:
                logging.info(
                    "rate %d, dmin %d, dmax %d, max_row %d, max_len %d" %
                    (rate, DmatMin, DmatMax, max_rows, max_len))

            self.A[rate] = numpy.zeros((max_rows, max_len),
                                       dtype=numpy.complex128)
            self.B[rate] = numpy.zeros((max_rows, max_len),
                                       dtype=numpy.complex128)
            self.D[rate] = numpy.zeros((max_rows, max_len), dtype=numpy.int32)
            self.D[rate].fill(DmatMin)

            for i, Am in enumerate(Amat[rate]):
                self.A[rate][i, :len(Am)] = Am
            for i, Bm in enumerate(Bmat[rate]):
                self.B[rate][i, :len(Bm)] = Bm
            for i, Dm in enumerate(Dmat[rate]):
                self.D[rate][i, :len(Dm)] = Dm

    def write_to_xml(self,
                     filename,
                     contenthandler=DefaultContentHandler,
                     write_psd=False,
                     verbose=False):
        """Write SPIIR banks to a LIGO_LW xml file."""
        # FIXME: does not support clipping and write psd.

        # Create new document
        xmldoc = ligolw.Document()
        lw = ligolw.LIGO_LW()

        # set up root for this sub bank
        root = ligolw.LIGO_LW(Attributes({u"Name": u"gstlal_iir_bank_Bank"}))
        lw.appendChild(root)

        # put the bank table into the output document
        new_sngl_table = lsctables.New(lsctables.SnglInspiralTable,
                                       columns=self.sngl_inspiral_columns)
        for row in self.sngl_inspiral_table:
            new_sngl_table.append(row)

        root.appendChild(new_sngl_table)

        root.appendChild(
            param.Param.build('template_bank_filename', types.FromPyType[str],
                              self.template_bank_filename))
        root.appendChild(
            param.Param.build('sample_rate', types.FromPyType[str],
                              sample_rates_array_to_str(self.sample_rates)))
        root.appendChild(
            param.Param.build('flower', types.FromPyType[float], self.flower))
        root.appendChild(
            param.Param.build('epsilon', types.FromPyType[float],
                              self.epsilon))
        root.appendChild(
            param.Param.build('alpha', types.FromPyType[float], self.alpha))
        root.appendChild(
            param.Param.build('beta', types.FromPyType[float], self.beta))
        root.appendChild(
            param.Param.build('negative_latency', types.FromPyType[int],
                              self.negative_latency))

        # FIXME:  ligolw format now supports complex-valued data
        root.appendChild(
            array.Array.build('autocorrelation_bank_real',
                              self.autocorrelation_bank.real))
        root.appendChild(
            array.Array.build('autocorrelation_bank_imag',
                              self.autocorrelation_bank.imag))
        root.appendChild(
            array.Array.build('autocorrelation_mask',
                              self.autocorrelation_mask))
        root.appendChild(
            array.Array.build('sigmasq', numpy.array(self.sigmasq)))
        root.appendChild(
            array.Array.build('matches', numpy.array(self.matches)))

        # put the SPIIR coeffs in
        for rate in self.A.keys():
            root.appendChild(
                array.Array.build('a_%d' % (rate),
                                  repack_complex_array_to_real(self.A[rate])))
            root.appendChild(
                array.Array.build('b_%d' % (rate),
                                  repack_complex_array_to_real(self.B[rate])))
            root.appendChild(array.Array.build('d_%d' % (rate), self.D[rate]))

        # add top level LIGO_LW to document
        xmldoc.appendChild(lw)

        # Write to file
        utils.write_filename(xmldoc,
                             filename,
                             gz=filename.endswith('.gz'),
                             verbose=verbose)

    def fill_sngl_row(self, row):
        for attr in self.sngl_inspiral_columns:
            try:
                getattr(row, attr)
            except AttributeError:
                setattr(row, attr, None)
        return row

    def read_from_xml(self,
                      filename,
                      contenthandler=DefaultContentHandler,
                      verbose=False):

        self.set_bank_filename(filename)
        # Load document
        xmldoc = utils.load_filename(filename,
                                     contenthandler=contenthandler,
                                     verbose=verbose)

        for root in (
                elem
                for elem in xmldoc.getElementsByTagName(ligolw.LIGO_LW.tagName)
                if elem.hasAttribute(u"Name")
                and elem.Name == "gstlal_iir_bank_Bank"):
            # FIXME: not Read sngl inspiral table

            # Read root-level scalar parameters
            self.template_bank_filename = param.get_pyvalue(
                root, 'template_bank_filename')
            # Get sngl inspiral table
            sngl_inspiral_table = lsctables.SnglInspiralTable.get_table(root)

            # put the bank table in and fill in missing attributes with zero
            self.sngl_inspiral_table = lsctables.New(
                lsctables.SnglInspiralTable)
            for row in sngl_inspiral_table:
                newrow = self.fill_sngl_row(row)
                self.sngl_inspiral_table.append(newrow)

            if os.path.isfile(self.template_bank_filename):
                pass
            else:

                # FIXME teach the trigger generator to get this information a better way
                self.template_bank_filename = tempfile.NamedTemporaryFile(
                    suffix=".gz", delete=False).name
                tmpxmldoc = ligolw.Document()
                tmpxmldoc.appendChild(
                    ligolw.LIGO_LW()).appendChild(sngl_inspiral_table)
                utils.write_filename(tmpxmldoc,
                                     self.template_bank_filename,
                                     gz=True,
                                     verbose=verbose)
                tmpxmldoc.unlink()  # help garbage collector

            self.autocorrelation_bank = array.get_array(
                root,
                'autocorrelation_bank_real').array + 1j * array.get_array(
                    root, 'autocorrelation_bank_imag').array
            self.autocorrelation_mask = array.get_array(
                root, 'autocorrelation_mask').array
            self.sigmasq = array.get_array(root, 'sigmasq').array
            self.matches = array.get_array(root, 'matches').array

            self.epsilon = float(param.get_pyvalue(root, 'epsilon'))
            self.flower = float(param.get_pyvalue(root, 'flower'))
            self.alpha = float(param.get_pyvalue(root, 'alpha'))
            self.beta = float(param.get_pyvalue(root, 'beta'))
            self.negative_latency = int(
                param.get_pyvalue(root, 'negative_latency'))

            # Read the SPIIR coeffs
            self.sample_rates = [
                int(float(r))
                for r in param.get_pyvalue(root, 'sample_rate').split(',')
            ]
            for sr in self.sample_rates:
                self.A[sr] = repack_real_array_to_complex(
                    array.get_array(root, 'a_%d' % (sr, )).array)
                self.B[sr] = repack_real_array_to_complex(
                    array.get_array(root, 'b_%d' % (sr, )).array)
                # if d_sr in the xml is int_8s type, need to convert to int_4s type
                # i.e. the numpy.int32 type to be consistent with the C code
                tmp_D = array.get_array(root, 'd_%d' % (sr, )).array
                self.D[sr] = tmp_D.astype(numpy.int32)

    def get_rates(self, contenthandler=DefaultContentHandler, verbose=False):
        bank_xmldoc = utils.load_filename(self.bank_filename,
                                          contenthandler=contenthandler,
                                          verbose=verbose)
        return [
            int(float(r))
            for r in param.get_pyvalue(bank_xmldoc, 'sample_rate').split(',')
        ]

    # FIXME: remove set_bank_filename when no longer needed
    # by trigger generator element
    def set_bank_filename(self, name):
        self.bank_filename = name


def load_iirbank(filename,
                 snr_threshold,
                 contenthandler=XMLContentHandler,
                 verbose=False):
    tmpltbank_xmldoc = utils.load_filename(filename,
                                           contenthandler=contenthandler,
                                           verbose=verbose)

    bank = Bank.__new__(Bank)
    bank = Bank(tmpltbank_xmldoc, snr_threshold, verbose=verbose)

    bank.set_bank_filename(filename)
    return bank


def get_maxrate_from_xml(filename,
                         contenthandler=DefaultContentHandler,
                         verbose=False):
    xmldoc = utils.load_filename(filename,
                                 contenthandler=contenthandler,
                                 verbose=verbose)

    for root in (
            elem
            for elem in xmldoc.getElementsByTagName(ligolw.LIGO_LW.tagName)
            if elem.hasAttribute(u"Name")
            and elem.Name == "gstlal_iir_bank_Bank"):

        sample_rates = [
            int(float(r))
            for r in param.get_pyvalue(root, 'sample_rate').split(',')
        ]

    return max(sample_rates)


